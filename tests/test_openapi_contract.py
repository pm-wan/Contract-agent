from pathlib import Path
from typing import Any

import yaml
from fastapi.testclient import TestClient

from contract_app.api import app, create_app
from contract_app.api_errors import ProblemDetailsResponse


def test_checked_in_openapi_matches_the_complete_runtime_contract() -> None:
    contract_path = Path(__file__).parents[1] / "docs/development/04-interfaces/OpenAPI.yaml"
    contract: dict[str, Any] = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
    assert contract == app.openapi()


def test_problem_responses_use_one_documented_media_type_and_schema() -> None:
    schema = app.openapi()
    problem_ref = "#/components/schemas/ProblemDetailsResponse"
    documented_problem_response_count = 0
    for path, methods in schema["paths"].items():
        if not path.startswith("/api/v1/"):
            continue
        for method, operation in methods.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            for response in operation["responses"].values():
                content = response.get("content", {})
                if "application/problem+json" not in content:
                    continue
                documented_problem_response_count += 1
                assert content["application/problem+json"]["schema"] == {"$ref": problem_ref}
                assert "application/json" not in content
    assert documented_problem_response_count > 0

    problem_schema = schema["components"]["schemas"]["ProblemDetailsResponse"]
    assert set(problem_schema["required"]) == {"type", "title", "status", "code", "trace_id"}


def test_validation_problem_matches_the_documented_response_model(monkeypatch: Any) -> None:
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("CONTRACT_AUTH_MODE", "development")
    monkeypatch.setenv("DEVELOPMENT_AUTH_TOKEN", "test-only-secret-with-more-than-32-characters")
    monkeypatch.setenv("DEVELOPMENT_AUTH_TENANT_ID", "11111111-1111-4111-8111-111111111111")
    monkeypatch.setenv("DEVELOPMENT_AUTH_ACTOR_ID", "22222222-2222-4222-8222-222222222222")
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused:unused@127.0.0.1:1/unused")
    response = TestClient(create_app()).post(
        "/api/v1/cases",
        json={},
        headers={"Authorization": "Bearer test-only-secret-with-more-than-32-characters"},
    )

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")
    problem = ProblemDetailsResponse.model_validate(response.json())
    assert problem.status == 422
    assert problem.code == "validation_error"
    assert problem.trace_id


def test_case_list_pagination_shape_matches_openapi_contract() -> None:
    contract_path = Path(__file__).parents[1] / "docs/development/04-interfaces/OpenAPI.yaml"
    contract: dict[str, Any] = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
    runtime = app.openapi()
    runtime_list = runtime["paths"]["/api/v1/cases"]["get"]
    documented_list = contract["paths"]["/api/v1/cases"]["get"]

    runtime_parameters = {parameter["name"]: parameter for parameter in runtime_list["parameters"]}
    contract_parameters = {
        parameter["name"]: parameter for parameter in documented_list["parameters"]
    }
    assert set(runtime_parameters) == set(contract_parameters) == {"page", "page_size", "query"}
    assert (
        runtime_parameters["page"]["schema"]["minimum"]
        == contract_parameters["page"]["schema"]["minimum"]
    )
    assert (
        runtime_parameters["page_size"]["schema"]["maximum"]
        == contract_parameters["page_size"]["schema"]["maximum"]
    )
    runtime_query_schema = runtime_parameters["query"]["schema"]
    runtime_query_value_schema = next(
        (
            schema
            for schema in runtime_query_schema.get("anyOf", [])
            if schema.get("type") == "string"
        ),
        runtime_query_schema,
    )
    contract_query_schema = contract_parameters["query"]["schema"]
    contract_query_value_schema = next(
        (
            schema
            for schema in contract_query_schema.get("anyOf", [])
            if schema.get("type") == "string"
        ),
        contract_query_schema,
    )
    assert runtime_query_value_schema["maxLength"] == contract_query_value_schema["maxLength"]
    assert (
        runtime_list["responses"]["200"]["content"]["application/json"]["schema"]
        == documented_list["responses"]["200"]["content"]["application/json"]["schema"]
    )

    runtime_schema = runtime["components"]["schemas"]["CaseListResponse"]
    contract_schema = contract["components"]["schemas"]["CaseListResponse"]
    assert set(runtime_schema["required"]) == set(contract_schema["required"])
    assert set(runtime_schema["properties"]) == set(contract_schema["properties"])

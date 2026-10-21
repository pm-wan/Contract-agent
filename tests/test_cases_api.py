import os
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from contract_app.api import create_app
from contract_app.api_cases import get_case_legal_entity_store, get_database_url
from contract_app.api_documents import get_document_catalog_store, get_document_ingestion_store
from contract_app.api_snapshots import Principal, require_principal
from contract_app.cases.legal_entity import CaseLegalEntityStore
from contract_app.documents.catalog import DocumentCatalogStore
from contract_app.documents.ingestion import DocumentIngestionStore
from contract_app.documents.storage import LocalObjectStore

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
TEST_ADMIN_DATABASE_URL = os.environ.get("TEST_ADMIN_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL or not TEST_ADMIN_DATABASE_URL,
    reason="both TEST_DATABASE_URL and TEST_ADMIN_DATABASE_URL are required",
)


def test_case_creation_grants_creator_membership_and_list_hides_nonmember_cases(
    tmp_path,
) -> None:
    assert TEST_DATABASE_URL is not None
    assert TEST_ADMIN_DATABASE_URL is not None
    tenant, actor, entity = uuid4(), uuid4(), uuid4()
    app = create_app()
    app.dependency_overrides[require_principal] = lambda: Principal(tenant, actor)
    app.dependency_overrides[get_database_url] = lambda: TEST_DATABASE_URL
    app.dependency_overrides[get_case_legal_entity_store] = lambda: CaseLegalEntityStore(
        TEST_DATABASE_URL
    )
    object_store = LocalObjectStore(tmp_path)
    app.dependency_overrides[get_document_ingestion_store] = lambda: DocumentIngestionStore(
        TEST_DATABASE_URL, object_store
    )
    app.dependency_overrides[get_document_catalog_store] = lambda: DocumentCatalogStore(
        TEST_DATABASE_URL
    )
    client = TestClient(app)

    unauthenticated_app = create_app()
    assert unauthenticated_app is not None
    unauthenticated = TestClient(unauthenticated_app).get("/api/v1/cases")
    assert unauthenticated.status_code == 401

    created = client.post(
        "/api/v1/cases",
        headers={"Idempotency-Key": "create-case-first-001"},
        json={"title": "  Initial review  "},
    )
    assert created.status_code == 201, created.text
    case = created.json()
    assert case["title"] == "Initial review"
    assert case["legal_entity_id"] is None
    assert case["can_correct_legal_entity"] is False
    assert case["legal_entity_confirmed_at"] is None
    assert case["status"] == "draft"
    assert case["version"] == 1
    assert (
        client.post(
            "/api/v1/cases",
            headers={"Idempotency-Key": "create-case-first-001"},
            json={"title": "Initial review"},
        ).json()
        == case
    )

    confirmation_request = {
        "legal_entity_id": str(entity),
        "expected_case_version": 1,
        "verification_note": "已与签约主体资料核对",
    }
    confirmation_route = f"/api/v1/cases/{case['case_id']}/legal-entity-confirmations"
    missing_primary = client.post(
        confirmation_route,
        headers={"Idempotency-Key": "entity-confirmation-000"},
        json=confirmation_request,
    )
    assert missing_primary.status_code == 409
    assert missing_primary.json()["code"] == "conflict"
    assert "primary_contract_required_before_entity_confirmation" not in missing_primary.text
    uploaded_primary = client.post(
        f"/api/v1/cases/{case['case_id']}/documents",
        headers={"Idempotency-Key": "entity-primary-upload-0001"},
        data={"role": "primary_contract"},
        files={"file": ("main.pdf", b"%PDF-1.7\n", "application/pdf")},
    )
    assert uploaded_primary.status_code == 201, uploaded_primary.text
    confirmation_request["expected_case_version"] = uploaded_primary.json()["case_version"]
    confirmed = client.post(
        confirmation_route,
        headers={"Idempotency-Key": "entity-confirmation-001"},
        json=confirmation_request,
    )
    assert confirmed.status_code == 200, confirmed.text
    case = confirmed.json()
    assert case["legal_entity_id"] == str(entity)
    assert case["legal_entity_confirmed_at"] is not None
    confirmed_at = case["legal_entity_confirmed_at"]
    assert case["version"] == 3
    assert (
        client.post(
            confirmation_route,
            headers={"Idempotency-Key": "entity-confirmation-001"},
            json=confirmation_request,
        ).json()
        == case
    )
    reviewer_correction = client.post(
        confirmation_route,
        headers={"Idempotency-Key": "entity-confirmation-002"},
        json={**confirmation_request, "legal_entity_id": str(uuid4())},
    )
    assert reviewer_correction.status_code == 409
    assert client.get(f"/api/v1/cases/{case['case_id']}").json() == case
    conflicting_confirmation = client.post(
        confirmation_route,
        headers={"Idempotency-Key": "entity-confirmation-001"},
        json={**confirmation_request, "legal_entity_id": str(uuid4())},
    )
    assert conflicting_confirmation.status_code == 409

    entity_admin = uuid4()
    correction_route = f"/api/v1/cases/{case['case_id']}/legal-entity-corrections"
    correction_context_route = f"/api/v1/cases/{case['case_id']}/legal-entity-correction-context"
    correction_body = {
        "legal_entity_id": str(uuid4()),
        "expected_case_version": case["version"],
        "correction_note": "管理员依据签约主体登记材料纠正",
    }
    denied_correction = client.post(
        correction_route,
        headers={"Idempotency-Key": "entity-correction-reviewer-01"},
        json=correction_body,
    )
    assert denied_correction.status_code == 404
    assert client.get(correction_context_route).status_code == 404
    assert client.get("/api/v1/cases/legal-entity-correction-cases").status_code == 403
    with psycopg.connect(TEST_ADMIN_DATABASE_URL) as connection:
        connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant),))
        connection.execute(
            "INSERT INTO tenant_entity_administrators (tenant_id, actor_id, granted_by) "
            "VALUES (%s, %s, %s)",
            (tenant, entity_admin, uuid4()),
        )
    app.dependency_overrides[require_principal] = lambda: Principal(tenant, entity_admin)
    context_response = client.get(correction_context_route)
    assert context_response.status_code == 200, context_response.text
    assert set(context_response.json()) == {"case_id", "legal_entity_id", "version"}
    queue_response = client.get("/api/v1/cases/legal-entity-correction-cases")
    assert queue_response.status_code == 200, queue_response.text
    assert queue_response.json()["total"] == 1
    assert set(queue_response.json()["items"][0]) == {
        "case_id",
        "title",
        "legal_entity_id",
        "version",
    }
    assert queue_response.json()["items"][0]["case_id"] == case["case_id"]
    # The correction-only endpoints do not grant membership or ordinary case visibility.
    assert client.get(f"/api/v1/cases/{case['case_id']}").status_code == 404
    denied_documents = client.get(f"/api/v1/cases/{case['case_id']}/documents")
    assert denied_documents.status_code == 403
    assert "items" not in denied_documents.json()
    corrected = client.post(
        correction_route,
        headers={"Idempotency-Key": "entity-correction-admin-0001"},
        json=correction_body,
    )
    assert corrected.status_code == 200, corrected.text
    case = corrected.json()
    assert set(case) == {"case_id", "legal_entity_id", "version"}
    assert case["legal_entity_id"] == correction_body["legal_entity_id"]
    assert case["version"] == int(confirmation_request["expected_case_version"]) + 2
    assert (
        client.post(
            correction_route,
            headers={"Idempotency-Key": "entity-correction-admin-0001"},
            json=correction_body,
        ).json()
        == case
    )
    conflicting_correction = client.post(
        correction_route,
        headers={"Idempotency-Key": "entity-correction-admin-0001"},
        json={**correction_body, "correction_note": "不同请求"},
    )
    assert conflicting_correction.status_code == 409
    with psycopg.connect(TEST_ADMIN_DATABASE_URL) as connection:
        connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant),))
        correction_audit = connection.execute(
            "SELECT actor_id, previous_legal_entity_id, legal_entity_id, correction_note "
            "FROM case_legal_entity_corrections WHERE tenant_id = %s AND case_id = %s",
            (tenant, case["case_id"]),
        ).fetchone()
    assert correction_audit == (
        entity_admin,
        entity,
        UUID(correction_body["legal_entity_id"]),
        correction_body["correction_note"],
    )
    app.dependency_overrides[require_principal] = lambda: Principal(tenant, actor)
    replacement_primary = client.post(
        f"/api/v1/cases/{case['case_id']}/documents",
        headers={"Idempotency-Key": "entity-primary-replacement-001"},
        data={"role": "primary_contract"},
        files={"file": ("corrected-main.pdf", b"%PDF-1.7\n corrected scope", "application/pdf")},
    )
    assert replacement_primary.status_code == 201, replacement_primary.text
    case = client.get(f"/api/v1/cases/{case['case_id']}").json()
    app.dependency_overrides[require_principal] = lambda: Principal(tenant, entity_admin)
    with (
        psycopg.connect(TEST_DATABASE_URL) as connection,
        pytest.raises(psycopg.errors.InsufficientPrivilege),
    ):
        connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant),))
        connection.execute(
            "UPDATE case_legal_entity_corrections SET correction_note = 'rewritten' "
            "WHERE tenant_id = %s AND case_id = %s",
            (tenant, case["case_id"]),
        )
    snapshot_id, preview_id, run_id, task_id = uuid4(), uuid4(), uuid4(), uuid4()
    source_sha = "a" * 64
    manifest = f'{{"case_version":{case["version"]}}}'
    with psycopg.connect(TEST_ADMIN_DATABASE_URL) as connection:
        connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant),))
        connection.execute(
            "UPDATE document_versions SET status = 'active' WHERE tenant_id = %s AND id = %s",
            (tenant, UUID(replacement_primary.json()["document_version_id"])),
        )
        selection = connection.execute(
            "INSERT INTO contract_selections "
            "(tenant_id, case_id, document_version_id, relation, selected_by) "
            "SELECT %s, %s, id, 'main', %s FROM document_versions "
            "WHERE tenant_id = %s AND id = %s AND source_kind = 'main' "
            "RETURNING id, document_version_id",
            (
                tenant,
                UUID(case["case_id"]),
                actor,
                tenant,
                UUID(replacement_primary.json()["document_version_id"]),
            ),
        ).fetchone()
        assert selection is not None
        connection.execute(
            "INSERT INTO document_grants "
            "(tenant_id, actor_id, document_version_id, purpose, allow_text, allow_amount, "
            "valid_until) VALUES (%s, %s, %s, 'contract_review', true, true, "
            "clock_timestamp() + interval '1 day')",
            (tenant, actor, selection[1]),
        )
        connection.execute(
            "INSERT INTO source_previews "
            "(tenant_id, id, case_id, actor_id, purpose, require_amount, case_version, "
            "manifest, manifest_sha256, expires_at) "
            "VALUES (%s, %s, %s, %s, 'contract_review', false, %s, %s::jsonb, %s, "
            "clock_timestamp() + interval '1 day')",
            (
                tenant,
                preview_id,
                UUID(case["case_id"]),
                actor,
                case["version"],
                manifest,
                source_sha,
            ),
        )
        connection.execute(
            "INSERT INTO input_snapshots "
            "(tenant_id, id, case_id, preview_id, frozen_by, purpose, require_amount, "
            "case_version, manifest, manifest_sha256) "
            "VALUES (%s, %s, %s, %s, %s, 'contract_review', false, %s, %s::jsonb, %s)",
            (
                tenant,
                snapshot_id,
                UUID(case["case_id"]),
                preview_id,
                actor,
                case["version"],
                manifest,
                source_sha,
            ),
        )
        connection.execute(
            "INSERT INTO input_snapshot_documents "
            "(tenant_id, snapshot_id, document_version_id, selection_id, source_sha256, relation) "
            "SELECT %s, %s, %s, %s, source_sha256, 'main' FROM document_versions "
            "WHERE tenant_id = %s AND id = %s",
            (tenant, snapshot_id, selection[1], selection[0], tenant, selection[1]),
        )
        connection.execute(
            "INSERT INTO audit_runs "
            "(tenant_id, id, case_id, input_snapshot_id, started_by, request_key, status) "
            "VALUES (%s, %s, %s, %s, %s, 'correction-test-run', 'running')",
            (tenant, run_id, UUID(case["case_id"]), snapshot_id, actor),
        )
        connection.execute(
            "INSERT INTO tasks "
            "(tenant_id, id, run_id, task_type, payload, status, lease_owner, lease_until, "
            "idempotency_key) "
            "VALUES (%s, %s, %s, 'contract_review', '{}'::jsonb, 'running', 'worker-1', "
            "clock_timestamp() + interval '1 minute', 'correction-test-task')",
            (tenant, task_id, run_id),
        )
        connection.execute(
            "UPDATE audit_cases SET status = 'ready' WHERE tenant_id = %s AND id = %s",
            (tenant, UUID(case["case_id"])),
        )
    case_version_before_second_correction = case["version"]
    corrected_after_selection = client.post(
        correction_route,
        headers={"Idempotency-Key": "entity-correction-admin-0002"},
        json={
            **correction_body,
            "legal_entity_id": str(uuid4()),
            "expected_case_version": case["version"],
        },
    )
    assert corrected_after_selection.status_code == 200, corrected_after_selection.text
    case = corrected_after_selection.json()
    assert set(case) == {"case_id", "legal_entity_id", "version"}
    assert case["version"] == case_version_before_second_correction + 1
    with psycopg.connect(TEST_ADMIN_DATABASE_URL) as connection:
        connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant),))
        correction_effects = connection.execute(
            "SELECT revoked_at IS NOT NULL FROM contract_selections "
            "WHERE tenant_id = %s AND case_id = %s",
            (tenant, UUID(case["case_id"])),
        ).fetchall()
        grant_revoked = connection.execute(
            "SELECT revoked_at IS NOT NULL FROM document_grants "
            "WHERE tenant_id = %s AND document_version_id = %s",
            (tenant, selection[1]),
        ).fetchone()
        retained_snapshot_row = connection.execute(
            "SELECT count(*) FROM input_snapshots WHERE tenant_id = %s AND id = %s",
            (tenant, snapshot_id),
        ).fetchone()
        retained_snapshot_source_row = connection.execute(
            "SELECT count(*) FROM input_snapshot_documents "
            "WHERE tenant_id = %s AND snapshot_id = %s",
            (tenant, snapshot_id),
        ).fetchone()
        run_state_row = connection.execute(
            "SELECT status FROM audit_runs WHERE tenant_id = %s AND id = %s",
            (tenant, run_id),
        ).fetchone()
        task_state = connection.execute(
            "SELECT status, lease_owner, lease_until, fencing_token FROM tasks "
            "WHERE tenant_id = %s AND id = %s",
            (tenant, task_id),
        ).fetchone()
        snapshot_document_scope_row = connection.execute(
            "SELECT legal_entity_id FROM document_versions WHERE tenant_id = %s AND id = %s",
            (tenant, selection[1]),
        ).fetchone()
    assert correction_effects == [(True,)]
    assert grant_revoked is not None and grant_revoked[0] is True
    assert retained_snapshot_row is not None and retained_snapshot_row[0] == 1
    assert retained_snapshot_source_row is not None and retained_snapshot_source_row[0] == 1
    assert run_state_row is not None and run_state_row[0] == "superseded"
    assert task_state == ("superseded", None, None, 1)
    assert snapshot_document_scope_row is not None
    assert snapshot_document_scope_row[0] == UUID(correction_body["legal_entity_id"])
    app.dependency_overrides[require_principal] = lambda: Principal(tenant, actor)

    history_route = f"/api/v1/cases/{case['case_id']}/legal-entity-history"
    history = client.get(history_route, params={"limit": 1})
    assert history.status_code == 200, history.text
    assert [event["event_type"] for event in history.json()["items"]] == [
        "confirmed",
    ]
    assert history.json()["items"][0]["reason"] == confirmation_request["verification_note"]
    assert history.json()["next_cursor"]
    next_history = client.get(
        history_route, params={"limit": 1, "cursor": history.json()["next_cursor"]}
    )
    assert next_history.status_code == 200, next_history.text
    assert [event["event_type"] for event in next_history.json()["items"]] == ["corrected"]
    assert next_history.json()["items"][0]["reason"] == correction_body["correction_note"]
    assert next_history.json()["items"][0]["previous_legal_entity_id"] == str(entity)
    assert next_history.json()["next_cursor"]
    final_history = client.get(
        history_route, params={"limit": 1, "cursor": next_history.json()["next_cursor"]}
    )
    assert final_history.status_code == 200, final_history.text
    assert final_history.json()["items"][0]["event_type"] == "corrected"
    assert final_history.json()["items"][0]["revoked_selection_count"] == 1
    assert final_history.json()["items"][0]["revoked_grant_count"] == 1
    assert final_history.json()["items"][0]["superseded_run_count"] == 1
    assert final_history.json()["next_cursor"] is None
    app.dependency_overrides[require_principal] = lambda: Principal(tenant, uuid4())
    assert client.get(history_route).status_code == 404
    app.dependency_overrides[require_principal] = lambda: Principal(tenant, actor)

    detail = client.get(f"/api/v1/cases/{case['case_id']}")
    assert detail.status_code == 200
    assert detail.json()["can_correct_legal_entity"] is False
    assert detail.json()["case_id"] == case["case_id"]
    assert detail.json()["legal_entity_id"] == case["legal_entity_id"]
    assert detail.json()["version"] == case["version"]
    case_list = client.get("/api/v1/cases")
    assert case_list.status_code == 200
    assert case_list.json()["items"][0]["legal_entity_confirmed_at"] == confirmed_at

    second = client.post(
        "/api/v1/cases",
        headers={"Idempotency-Key": "create-case-second-01"},
        json={"title": "Second visible review"},
    )
    assert second.status_code == 201, second.text

    rejected = client.post(
        "/api/v1/cases",
        headers={"Idempotency-Key": "create-case-forged-01"},
        json={"title": "forged", "tenant_id": str(uuid4())},
    )
    assert rejected.status_code == 422

    other_case, other_tenant = uuid4(), uuid4()
    with psycopg.connect(TEST_ADMIN_DATABASE_URL) as connection:
        connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant),))
        connection.execute(
            "INSERT INTO audit_cases (tenant_id, id, title) VALUES (%s, %s, 'private')",
            (tenant, other_case),
        )
        connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(other_tenant),))
        connection.execute(
            "INSERT INTO audit_cases (tenant_id, title) VALUES (%s, 'other tenant')",
            (other_tenant,),
        )

    listed = client.get("/api/v1/cases", params={"page": 1, "page_size": 1})
    assert listed.status_code == 200
    assert listed.json()["total"] == 2
    assert listed.json()["page"] == 1
    assert len(listed.json()["items"]) == 1
    second_page = client.get("/api/v1/cases", params={"page": 2, "page_size": 1})
    assert second_page.json()["total"] == 2
    assert second_page.json()["page"] == 2
    assert {
        listed.json()["items"][0]["case_id"],
        second_page.json()["items"][0]["case_id"],
    } == {case["case_id"], second.json()["case_id"]}
    searched = client.get("/api/v1/cases", params={"query": "Initial"})
    assert searched.json()["total"] == 1
    assert searched.json()["items"][0]["case_id"] == case["case_id"]
    clamped = client.get("/api/v1/cases", params={"page": 50, "page_size": 1})
    assert clamped.json()["page"] == 2
    assert UUID(case["case_id"]) != other_case

    inaccessible = client.get(f"/api/v1/cases/{other_case}")
    assert inaccessible.status_code == 404
    assert inaccessible.json()["code"] == "not_found"

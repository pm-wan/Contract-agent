from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from contract_app.api import create_app
from contract_app.api_documents import SelectDocumentRequest


def _request(
    relation: str,
    purpose: str | None = None,
    *,
    confirm_project_relation: bool = False,
) -> dict[str, object]:
    value: dict[str, object] = {
        "document_version_id": str(uuid4()),
        "relation": relation,
        "confirm_project_relation": confirm_project_relation,
        "allow_amount": False,
        "valid_until": (datetime.now(UTC) + timedelta(minutes=30)).isoformat(),
    }
    if purpose is not None:
        value["purpose"] = purpose
    return value


def test_upstream_selection_requires_upstream_comparison_purpose() -> None:
    with pytest.raises(ValidationError, match="document_relation_purpose_mismatch"):
        SelectDocumentRequest.model_validate(_request("upstream"))
    selected = SelectDocumentRequest.model_validate(
        _request("upstream", "upstream_comparison", confirm_project_relation=True)
    )
    assert selected.purpose == "upstream_comparison"
    with pytest.raises(ValidationError, match="project_relation_confirmation_required"):
        SelectDocumentRequest.model_validate(
            _request("upstream", "upstream_comparison", confirm_project_relation=False)
        )


def test_attachments_cannot_be_granted_for_upstream_comparison() -> None:
    with pytest.raises(ValidationError, match="document_relation_purpose_mismatch"):
        SelectDocumentRequest.model_validate(_request("attachment", "upstream_comparison"))
    assert SelectDocumentRequest.model_validate(_request("attachment")).purpose == "contract_review"


def test_main_contract_can_receive_separate_purpose_grants() -> None:
    contract_review = SelectDocumentRequest.model_validate(_request("main"))
    upstream_comparison = SelectDocumentRequest.model_validate(
        _request("main", "upstream_comparison")
    )
    assert contract_review.purpose == "contract_review"
    assert upstream_comparison.purpose == "upstream_comparison"
    with pytest.raises(ValidationError, match="project_relation_confirmation_required"):
        SelectDocumentRequest.model_validate(
            _request("main", "upstream_comparison", confirm_project_relation=True)
        )


def test_openapi_exposes_customer_upload_and_purpose_scoped_selection() -> None:
    schema = create_app().openapi()
    upload_role = schema["components"]["schemas"]["Body_addDocumentVersion"]["properties"]["role"][
        "enum"
    ]
    selection = schema["components"]["schemas"]["SelectDocumentRequest"]["properties"]
    assert "customer_contract" in upload_role
    assert selection["relation"]["enum"] == ["main", "attachment", "upstream"]
    assert selection["purpose"]["default"] == "contract_review"
    assert selection["purpose"]["enum"] == ["contract_review", "upstream_comparison"]
    assert selection["confirm_project_relation"]["default"] is False

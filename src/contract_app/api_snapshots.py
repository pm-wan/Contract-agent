"""M0 fixture-source HTTP contract; identity is supplied only by trusted auth."""

import os
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from contract_app.api_errors import COMMON_PROBLEM_RESPONSES, problem_responses
from contract_app.contextassembly.snapshot import InputSnapshotStore, SourceStatus
from contract_app.auth_security import Principal, require_permission, require_principal
from contract_app.retrieval.authorization import TaskAccess

Purpose = Literal["contract_review", "upstream_comparison"]
router = APIRouter(
    prefix="/api/v1",
    tags=["Sources"],
    responses=COMMON_PROBLEM_RESPONSES,
    dependencies=[Depends(require_permission("cases.use"))],
)


def get_snapshot_store() -> InputSnapshotStore:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise HTTPException(status_code=503, detail="service_unavailable")
    return InputSnapshotStore(database_url)


class PreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purpose: Purpose
    task_requires_amount: bool = False


class FreezeRequest(PreviewRequest):
    preview_id: UUID
    expected_case_version: int = Field(ge=1)
    manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class SourceItemResponse(BaseModel):
    selection_id: UUID
    relation: str
    status: SourceStatus
    document_version_id: UUID | None = None
    source_sha256: str | None = None


class PreviewResponse(BaseModel):
    preview_id: UUID
    case_id: UUID
    purpose: Purpose
    case_version: int
    manifest_sha256: str
    items: list[SourceItemResponse]


class FrozenInputResponse(BaseModel):
    input_snapshot_id: UUID
    case_id: UUID
    purpose: Purpose
    case_version: int
    manifest_sha256: str
    document_version_ids: list[UUID]


@router.post(
    "/cases/{case_id}/source-previews",
    operation_id="previewCaseSources",
    response_model=PreviewResponse,
    status_code=201,
    responses=problem_responses(404),
)
def preview_sources(
    case_id: UUID,
    body: PreviewRequest,
    principal: Annotated[Principal, Depends(require_principal)],
    store: Annotated[InputSnapshotStore, Depends(get_snapshot_store)],
) -> PreviewResponse:
    access = TaskAccess(
        principal.tenant_id,
        case_id,
        principal.actor_id,
        body.purpose,
        body.task_requires_amount,
    )
    preview = store.preview(access)
    return PreviewResponse(
        preview_id=preview.id,
        case_id=preview.case_id,
        purpose=body.purpose,
        case_version=preview.case_version,
        manifest_sha256=preview.manifest_sha256,
        items=[
            SourceItemResponse.model_validate(item, from_attributes=True) for item in preview.items
        ],
    )


@router.post(
    "/cases/{case_id}/input-snapshots",
    operation_id="freezeCaseSources",
    response_model=FrozenInputResponse,
    responses=problem_responses(404, 409),
)
def freeze_sources(
    case_id: UUID,
    body: FreezeRequest,
    principal: Annotated[Principal, Depends(require_principal)],
    store: Annotated[InputSnapshotStore, Depends(get_snapshot_store)],
) -> FrozenInputResponse:
    access = TaskAccess(
        principal.tenant_id,
        case_id,
        principal.actor_id,
        body.purpose,
        body.task_requires_amount,
    )
    frozen = store.freeze(access, body.preview_id, body.expected_case_version, body.manifest_sha256)
    return FrozenInputResponse(
        input_snapshot_id=frozen.id,
        case_id=frozen.case_id,
        purpose=body.purpose,
        case_version=frozen.case_version,
        manifest_sha256=frozen.manifest_sha256,
        document_version_ids=sorted(frozen.document_version_ids),
    )

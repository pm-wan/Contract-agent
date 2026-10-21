"""Case document upload endpoint for authenticated members."""

import os
from dataclasses import asdict
from datetime import datetime
from typing import Annotated, Literal
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, UploadFile
from pydantic import BaseModel, ConfigDict, Field, model_validator
from starlette.responses import Response

from contract_app.api_errors import COMMON_PROBLEM_RESPONSES, problem_responses
from contract_app.api_snapshots import Principal, require_principal
from contract_app.auth_security import require_permission
from contract_app.documents.authorization import (
    DocumentAccessEvent,
    DocumentAuthorizationStore,
    RevokedDocumentSelection,
    SelectedDocument,
    SelectionPurpose,
    SelectionRelation,
)
from contract_app.documents.catalog import (
    AuthorizationStatus,
    CaseDocument,
    DocumentCatalogStore,
    DocumentStatus,
    IndexErrorCode,
    IndexStatus,
    ParseStatus,
    SelectionStatus,
)
from contract_app.documents.content import (
    DocumentContentStore,
    DocumentContentUnavailable,
    ParsedDocumentNotReady,
)
from contract_app.documents.ingestion import DocumentIngestionStore, UploadRole
from contract_app.documents.parse_errors import ParseErrorCode
from contract_app.documents.parser import MAX_DOCUMENT_BYTES
from contract_app.documents.removal import DocumentRemovalStore
from contract_app.documents.storage import create_object_store

router = APIRouter(
    prefix="/api/v1",
    tags=["Documents"],
    responses=COMMON_PROBLEM_RESPONSES,
    dependencies=[Depends(require_permission("cases.use"))],
)


def get_document_ingestion_store() -> DocumentIngestionStore:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise HTTPException(status_code=503, detail="service_unavailable")
    try:
        object_store = create_object_store()
    except (OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=503, detail="service_unavailable") from exc
    return DocumentIngestionStore(database_url, object_store)


def get_document_authorization_store() -> DocumentAuthorizationStore:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise HTTPException(status_code=503, detail="service_unavailable")
    return DocumentAuthorizationStore(database_url)


def get_document_content_store() -> DocumentContentStore:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise HTTPException(status_code=503, detail="service_unavailable")
    try:
        object_store = create_object_store()
    except (OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=503, detail="service_unavailable") from exc
    return DocumentContentStore(database_url, object_store)


def get_document_catalog_store() -> DocumentCatalogStore:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise HTTPException(status_code=503, detail="service_unavailable")
    return DocumentCatalogStore(database_url)


def get_document_removal_store() -> DocumentRemovalStore:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise HTTPException(status_code=503, detail="service_unavailable")
    return DocumentRemovalStore(database_url)


class UploadedDocumentResponse(BaseModel):
    document_version_id: UUID
    case_id: UUID
    file_name: str
    role: UploadRole
    media_type: str
    size_bytes: int
    content_hash: str
    parse_status: ParseStatus
    parser_version: str
    parsed_hash: str | None
    warnings: list[str]
    case_version: int
    document_status: Literal["staged", "active"]
    authorization_status: Literal["not_granted_by_upload"]


class DocumentPurposeAuthorizationResponse(BaseModel):
    purpose: SelectionPurpose
    allow_text: bool
    allow_amount: bool
    valid_until: datetime
    status: Literal["authorized", "expired"]


class CaseDocumentResponse(BaseModel):
    document_version_id: UUID
    case_id: UUID
    file_name: str
    role: UploadRole
    media_type: str
    size_bytes: int
    content_hash: str
    parse_status: ParseStatus
    parse_error_code: ParseErrorCode | None
    parser_version: str
    warnings: list[str]
    case_version: int
    document_status: DocumentStatus
    index_status: IndexStatus
    index_error_code: IndexErrorCode | None
    selection_id: UUID | None
    selection_status: SelectionStatus
    authorization_status: AuthorizationStatus
    amount_authorized: bool
    authorization_valid_until: datetime | None
    purpose_authorizations: list[DocumentPurposeAuthorizationResponse]
    created_at: datetime


def _case_document_response(document: CaseDocument) -> CaseDocumentResponse:
    return CaseDocumentResponse(**asdict(document))


class SelectDocumentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_version_id: UUID
    relation: SelectionRelation
    purpose: SelectionPurpose = "contract_review"
    confirm_project_relation: bool = False
    allow_amount: bool
    valid_until: datetime

    @model_validator(mode="after")
    def validate_relation_purpose(self) -> "SelectDocumentRequest":
        if self.relation == "upstream" and self.purpose != "upstream_comparison":
            raise ValueError("document_relation_purpose_mismatch")
        if self.purpose == "upstream_comparison" and self.relation not in {"main", "upstream"}:
            raise ValueError("document_relation_purpose_mismatch")
        if (self.relation == "upstream") != self.confirm_project_relation:
            raise ValueError("project_relation_confirmation_required")
        return self


class RevokeDocumentSelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_case_version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=500)


class RemoveCaseDocumentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_case_version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=500)


class SelectedDocumentResponse(BaseModel):
    selection_id: UUID
    grant_id: UUID
    document_version_id: UUID
    case_id: UUID
    relation: SelectionRelation
    purpose: SelectionPurpose
    allow_text: Literal[True] = True
    allow_amount: bool
    valid_until: datetime
    case_version: int


class RevokedDocumentSelectionResponse(BaseModel):
    selection_id: UUID
    document_version_id: UUID
    case_id: UUID
    case_version: int
    revoked_at: datetime
    revoked_grant_count: int
    invalidated_run_count: int


class RemovedDocumentResponse(BaseModel):
    document_version_id: UUID
    case_id: UUID
    case_version: int
    removed_at: datetime
    revoked_selection_count: int
    revoked_grant_count: int
    invalidated_run_count: int


class DocumentAccessPermissionResponse(BaseModel):
    actor_id: UUID
    purpose: Literal["contract_review", "upstream_comparison"]
    allow_text: bool
    allow_amount: bool
    valid_until: datetime


class DocumentAccessEventResponse(BaseModel):
    id: UUID
    case_id: UUID
    document_version_id: UUID
    file_name: str
    selection_id: UUID
    actor_id: UUID
    action: Literal["selection_granted", "selection_revoked"]
    permissions: list[DocumentAccessPermissionResponse]
    reason: str | None
    case_version: int
    invalidated_run_ids: list[UUID]
    created_at: datetime


class ParsedTextBlockResponse(BaseModel):
    page: int
    text: str
    start_offset: int
    end_offset: int
    bbox: tuple[float, float, float, float] | None
    kind: Literal["paragraph", "table", "ocr"]
    confidence: float | None


class ParsedDocumentArtifactResponse(BaseModel):
    source_sha256: str
    media_type: str
    parser_version: str
    index_source_sha256: str
    renderer_identity: str | None
    pages: list[str]
    blocks: list[ParsedTextBlockResponse]
    warnings: list[str]


class DocumentAccessEventPageResponse(BaseModel):
    items: list[DocumentAccessEventResponse]
    next_cursor: str | None = None


@router.get(
    "/cases/{case_id}/documents",
    operation_id="listCaseDocuments",
    response_model=list[CaseDocumentResponse],
    responses=problem_responses(404),
)
def list_case_documents(
    case_id: UUID,
    principal: Annotated[Principal, Depends(require_principal)],
    catalog: Annotated[DocumentCatalogStore, Depends(get_document_catalog_store)],
) -> list[CaseDocumentResponse]:
    return [
        _case_document_response(document)
        for document in catalog.list_case_documents(
            principal.tenant_id, case_id, principal.actor_id
        )
    ]


def _selection_response(selection: SelectedDocument) -> SelectedDocumentResponse:
    return SelectedDocumentResponse(
        selection_id=selection.selection_id,
        grant_id=selection.grant_id,
        document_version_id=selection.document_version_id,
        case_id=selection.case_id,
        relation=selection.relation,
        purpose=selection.purpose,
        allow_amount=selection.allow_amount,
        valid_until=selection.valid_until,
        case_version=selection.case_version,
    )


def _revoked_selection_response(
    selection: RevokedDocumentSelection,
) -> RevokedDocumentSelectionResponse:
    return RevokedDocumentSelectionResponse(
        selection_id=selection.selection_id,
        document_version_id=selection.document_version_id,
        case_id=selection.case_id,
        case_version=selection.case_version,
        revoked_at=selection.revoked_at,
        revoked_grant_count=selection.revoked_grant_count,
        invalidated_run_count=selection.invalidated_run_count,
    )


def _access_event_response(event: DocumentAccessEvent) -> DocumentAccessEventResponse:
    return DocumentAccessEventResponse(
        id=event.id,
        case_id=event.case_id,
        document_version_id=event.document_version_id,
        file_name=event.file_name,
        selection_id=event.selection_id,
        actor_id=event.actor_id,
        action=event.action,
        permissions=[
            DocumentAccessPermissionResponse.model_validate(permission)
            for permission in event.permissions
        ],
        reason=event.reason,
        case_version=event.case_version,
        invalidated_run_ids=event.invalidated_run_ids,
        created_at=event.created_at,
    )


@router.get(
    "/cases/{case_id}/document-access-events",
    operation_id="listCaseDocumentAccessEvents",
    response_model=DocumentAccessEventPageResponse,
    responses=problem_responses(404),
)
def list_document_access_events(
    case_id: UUID,
    principal: Annotated[Principal, Depends(require_principal)],
    authorizations: Annotated[
        DocumentAuthorizationStore, Depends(get_document_authorization_store)
    ],
    cursor: Annotated[str | None, Query(max_length=256)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> DocumentAccessEventPageResponse:
    try:
        events, next_cursor = authorizations.list_access_events(
            principal.tenant_id, case_id, principal.actor_id, cursor, limit
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="invalid_cursor_or_limit") from exc
    return DocumentAccessEventPageResponse(
        items=[_access_event_response(event) for event in events], next_cursor=next_cursor
    )


@router.post(
    "/cases/{case_id}/documents",
    operation_id="addDocumentVersion",
    response_model=UploadedDocumentResponse,
    status_code=201,
    responses=problem_responses(404, 409, 413),
)
async def upload_document(
    case_id: UUID,
    file: Annotated[UploadFile, File()],
    role: Annotated[UploadRole, Form()],
    principal: Annotated[Principal, Depends(require_principal)],
    ingestion: Annotated[DocumentIngestionStore, Depends(get_document_ingestion_store)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> UploadedDocumentResponse:
    data = await file.read(MAX_DOCUMENT_BYTES + 1)
    if len(data) > MAX_DOCUMENT_BYTES:
        raise HTTPException(status_code=413, detail="payload_too_large")
    if file.filename is None:
        raise HTTPException(status_code=422, detail="file_name_required")
    document = ingestion.upload(
        principal.tenant_id,
        case_id,
        principal.actor_id,
        file.filename,
        file.content_type or "",
        data,
        role,
        idempotency_key,
    )
    return UploadedDocumentResponse(
        document_version_id=document.document_version_id,
        case_id=document.case_id,
        file_name=document.file_name,
        role=document.role,
        media_type=document.media_type,
        size_bytes=document.size_bytes,
        content_hash=document.content_hash,
        parse_status=document.parse_status,
        parser_version=document.parser_version,
        parsed_hash=document.parsed_hash,
        warnings=list(document.warnings),
        case_version=document.case_version,
        document_status=document.document_status,
        authorization_status=document.authorization_status,
    )


@router.delete(
    "/cases/{case_id}/documents/{document_version_id}",
    operation_id="removeCaseDocument",
    response_model=RemovedDocumentResponse,
    responses=problem_responses(404, 409),
)
def remove_case_document(
    case_id: UUID,
    document_version_id: UUID,
    request: RemoveCaseDocumentRequest,
    principal: Annotated[Principal, Depends(require_principal)],
    removal: Annotated[DocumentRemovalStore, Depends(get_document_removal_store)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> RemovedDocumentResponse:
    document = removal.remove(
        principal.tenant_id,
        case_id,
        principal.actor_id,
        document_version_id,
        request.expected_case_version,
        request.reason,
        idempotency_key,
    )
    return RemovedDocumentResponse(
        document_version_id=document.document_version_id,
        case_id=document.case_id,
        case_version=document.case_version,
        removed_at=document.removed_at,
        revoked_selection_count=document.revoked_selection_count,
        revoked_grant_count=document.revoked_grant_count,
        invalidated_run_count=document.invalidated_run_count,
    )


@router.post(
    "/cases/{case_id}/document-selections",
    operation_id="selectCaseDocument",
    response_model=SelectedDocumentResponse,
    status_code=201,
    responses=problem_responses(404, 409),
)
def select_document(
    case_id: UUID,
    request: SelectDocumentRequest,
    principal: Annotated[Principal, Depends(require_principal)],
    authorizations: Annotated[
        DocumentAuthorizationStore, Depends(get_document_authorization_store)
    ],
) -> SelectedDocumentResponse:
    selection = authorizations.select(
        principal.tenant_id,
        case_id,
        principal.actor_id,
        request.document_version_id,
        request.relation,
        request.allow_amount,
        request.valid_until,
        request.purpose,
        request.confirm_project_relation,
    )
    return _selection_response(selection)


@router.post(
    "/cases/{case_id}/document-selections/{selection_id}/revoke",
    operation_id="revokeCaseDocumentSelection",
    response_model=RevokedDocumentSelectionResponse,
    responses=problem_responses(404, 409),
)
def revoke_document_selection(
    case_id: UUID,
    selection_id: UUID,
    request: RevokeDocumentSelectionRequest,
    principal: Annotated[Principal, Depends(require_principal)],
    authorizations: Annotated[
        DocumentAuthorizationStore, Depends(get_document_authorization_store)
    ],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> RevokedDocumentSelectionResponse:
    selection = authorizations.revoke(
        principal.tenant_id,
        case_id,
        principal.actor_id,
        selection_id,
        request.expected_case_version,
        request.reason,
        idempotency_key,
    )
    return _revoked_selection_response(selection)


@router.get(
    "/cases/{case_id}/documents/{document_version_id}/content",
    operation_id="readCaseDocumentArtifact",
    response_model=ParsedDocumentArtifactResponse,
    responses={
        **problem_responses(404, 409),
        200: {
            "description": (
                "Authorized parsed JSON, original PDF/DOCX bytes, "
                "or canonical DOCX-rendered PDF bytes"
            ),
            "content": {
                "application/pdf": {"schema": {"type": "string", "format": "binary"}},
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document": {
                    "schema": {"type": "string", "format": "binary"}
                },
            },
        },
    },
)
def read_document_content(
    case_id: UUID,
    document_version_id: UUID,
    principal: Annotated[Principal, Depends(require_principal)],
    store: Annotated[DocumentContentStore, Depends(get_document_content_store)],
    kind: Literal["original", "parsed", "rendered"] = "original",
) -> Response:
    try:
        artifact = store.read(
            principal.tenant_id,
            case_id,
            principal.actor_id,
            document_version_id,
            kind,
        )
    except DocumentContentUnavailable as exc:
        raise HTTPException(status_code=404, detail="not_found") from exc
    except ParsedDocumentNotReady as exc:
        raise HTTPException(status_code=409, detail="parsed_content_not_ready") from exc
    file_name = (
        artifact.file_name
        if kind == "original"
        else f"{artifact.file_name}.canonical.pdf"
        if kind == "rendered"
        else f"{artifact.file_name}.parsed.json"
    )
    disposition = (
        "inline"
        if kind in {"original", "rendered"} and artifact.media_type == "application/pdf"
        else "attachment"
    )
    return Response(
        content=artifact.data,
        media_type=artifact.media_type,
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(file_name)}",
            "X-Content-Type-Options": "nosniff",
        },
    )

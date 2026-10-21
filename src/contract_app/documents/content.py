"""Authorized reads of a case-bound upload for human review."""

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from contract_app.documents.parser import DOCX_MEDIA_TYPE
from contract_app.documents.storage import ObjectRef, ObjectStore

ArtifactKind = Literal["original", "parsed", "rendered"]


class DocumentContentUnavailable(PermissionError):
    """Document is unavailable or the principal cannot access its case content."""


class ParsedDocumentNotReady(RuntimeError):
    """The upload is still parsing or parsing failed, so no parsed artifact is available."""


@dataclass(frozen=True, slots=True)
class DocumentArtifact:
    file_name: str
    media_type: str
    data: bytes
    kind: ArtifactKind


class DocumentContentStore:
    def __init__(self, database_url: str, object_store: ObjectStore) -> None:
        self._database_url = database_url
        self._object_store = object_store

    def read(
        self,
        tenant_id: UUID,
        case_id: UUID,
        actor_id: UUID,
        document_version_id: UUID,
        kind: ArtifactKind,
    ) -> DocumentArtifact:
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
            member = connection.execute(
                "SELECT 1 FROM case_members WHERE tenant_id = %s AND case_id = %s "
                "AND actor_id = %s AND role = 'reviewer' AND revoked_at IS NULL",
                (tenant_id, case_id, actor_id),
            ).fetchone()
            document = connection.execute(
                "SELECT source_name, media_type, size_bytes, source_sha256, parsed_size_bytes, "
                "parsed_sha256, rendered_pdf_sha256, rendered_pdf_size_bytes, renderer_identity, "
                "parse_status, storage_backend, status FROM document_versions "
                "WHERE tenant_id = %s AND uploaded_case_id = %s AND id = %s",
                (tenant_id, case_id, document_version_id),
            ).fetchone()
            if (
                member is None
                or document is None
                or document["status"]
                not in {
                    "staged",
                    "active",
                }
            ):
                raise DocumentContentUnavailable("document_content_unavailable")
            if document["storage_backend"] != self._object_store.backend:
                raise DocumentContentUnavailable("document_storage_backend_mismatch")
            if kind == "parsed" and document["parse_status"] not in {
                "completed",
                "needs_material",
            }:
                raise ParsedDocumentNotReady("parsed_content_not_ready")
            if kind == "original":
                reference = ObjectRef(
                    tenant_id,
                    case_id,
                    document["source_sha256"],
                    document["size_bytes"],
                )
                media_type = document["media_type"]
            elif kind == "rendered":
                if (
                    document["media_type"] != DOCX_MEDIA_TYPE
                    or document["parse_status"] not in {"completed", "needs_material"}
                    or document["rendered_pdf_sha256"] is None
                    or document["rendered_pdf_size_bytes"] is None
                    or document["renderer_identity"] is None
                ):
                    raise ParsedDocumentNotReady("canonical_render_not_ready")
                reference = ObjectRef(
                    tenant_id,
                    case_id,
                    document["rendered_pdf_sha256"],
                    document["rendered_pdf_size_bytes"],
                )
                media_type = "application/pdf"
            else:
                reference = ObjectRef(
                    tenant_id,
                    case_id,
                    document["parsed_sha256"],
                    document["parsed_size_bytes"],
                )
                media_type = "application/json"
            file_name = document["source_name"]

        try:
            data = self._object_store.get(reference)
        except FileNotFoundError as exc:
            raise DocumentContentUnavailable("document_content_unavailable") from exc
        return DocumentArtifact(file_name=file_name, media_type=media_type, data=data, kind=kind)

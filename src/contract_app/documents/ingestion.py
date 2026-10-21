"""Persist uploaded originals and parsed artifacts without authorizing model access."""

import hashlib
import logging
import re
from dataclasses import dataclass
from typing import Any, Literal, cast
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row

from contract_app.documents.object_locking import object_key_locks
from contract_app.documents.parser import (
    DOCX_MEDIA_TYPE,
    MAX_DOCUMENT_BYTES,
    PDF_MEDIA_TYPE,
)
from contract_app.documents.storage import ObjectRef, ObjectStore

UploadRole = Literal["primary_contract", "attachment", "customer_contract"]
LOGGER = logging.getLogger(__name__)


class DocumentUploadDenied(PermissionError):
    """Actor cannot upload into this case."""


class DocumentUploadConflict(ValueError):
    """Case state or upload idempotency key conflicts with this command."""


class DocumentUploadInvalid(ValueError):
    """Upload metadata or file contents are invalid."""


@dataclass(frozen=True, slots=True)
class UploadedDocument:
    document_version_id: UUID
    case_id: UUID
    file_name: str
    role: UploadRole
    media_type: str
    size_bytes: int
    content_hash: str
    parse_status: Literal["queued", "processing", "completed", "needs_material", "failed"]
    parser_version: str
    parsed_hash: str | None
    warnings: tuple[str, ...]
    case_version: int
    document_status: Literal["staged", "active"]
    authorization_status: Literal["not_granted_by_upload"] = "not_granted_by_upload"


class DocumentIngestionStore:
    def __init__(self, database_url: str, object_store: ObjectStore) -> None:
        self._database_url = database_url
        self._object_store = object_store

    @staticmethod
    def _file_name(file_name: str) -> str:
        normalized = file_name.replace("\\", "/").split("/")[-1].strip()
        if (
            not normalized
            or len(normalized) > 255
            or any(ord(character) < 32 or ord(character) == 127 for character in normalized)
        ):
            raise DocumentUploadInvalid("file_name_invalid")
        return normalized

    def _case_member(
        self,
        connection: psycopg.Connection[Any],
        tenant_id: UUID,
        case_id: UUID,
        actor_id: UUID,
    ) -> dict[str, Any]:
        case = connection.execute(
            "SELECT id, legal_entity_id, version, status FROM audit_cases "
            "WHERE tenant_id = %s AND id = %s FOR UPDATE",
            (tenant_id, case_id),
        ).fetchone()
        member = connection.execute(
            "SELECT role FROM case_members WHERE tenant_id = %s AND case_id = %s "
            "AND actor_id = %s AND revoked_at IS NULL",
            (tenant_id, case_id, actor_id),
        ).fetchone()
        if case is None or member is None or member["role"] not in {"submitter", "reviewer"}:
            raise DocumentUploadDenied("case_member_not_authorized")
        if case["status"] not in {"draft", "ready"}:
            raise DocumentUploadConflict("case_not_accepting_documents")
        return cast(dict[str, Any], case)

    def _cleanup_unreferenced_objects(self, references: list[ObjectRef]) -> None:
        """Best-effort compensation after rollback, serialized against concurrent uploads."""
        unique_references = {
            (reference.sha256, reference.size_bytes): reference for reference in references
        }
        for reference in unique_references.values():
            try:
                with object_key_locks(
                    self._database_url,
                    self._object_store.backend,
                    reference.tenant_id,
                    reference.case_id,
                    {reference.sha256},
                ):
                    with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
                        connection.execute(
                            "SELECT set_config('app.tenant_id', %s, true)",
                            (str(reference.tenant_id),),
                        )
                        is_referenced_row = connection.execute(
                            "SELECT EXISTS (SELECT 1 FROM document_versions "
                            "WHERE tenant_id = %s AND uploaded_case_id = %s "
                            "AND storage_backend = %s AND (source_sha256 = %s "
                            "OR parsed_sha256 = %s)) AS is_referenced",
                            (
                                reference.tenant_id,
                                reference.case_id,
                                self._object_store.backend,
                                reference.sha256,
                                reference.sha256,
                            ),
                        ).fetchone()
                        is_referenced = bool(
                            is_referenced_row and is_referenced_row["is_referenced"]
                        )
                    if not is_referenced:
                        self._object_store.delete(reference)
            except Exception:
                # Preserve the upload failure; never log the object key or source metadata.
                LOGGER.warning("failed to compensate an uncommitted document object")

    @staticmethod
    def _uploaded(row: dict[str, Any]) -> UploadedDocument:
        role_by_source_kind: dict[str, UploadRole] = {
            "main": "primary_contract",
            "attachment": "attachment",
            "customer": "customer_contract",
        }
        return UploadedDocument(
            document_version_id=row["id"],
            case_id=row["uploaded_case_id"],
            file_name=row["source_name"],
            role=role_by_source_kind[row["source_kind"]],
            media_type=row["media_type"],
            size_bytes=row["size_bytes"],
            content_hash=row["source_sha256"],
            parse_status=row["parse_status"],
            parser_version=row["parser_version"],
            parsed_hash=row["parsed_sha256"],
            warnings=tuple(row["warnings"]),
            case_version=row["upload_case_version"],
            document_status=row["status"],
        )

    def upload(
        self,
        tenant_id: UUID,
        case_id: UUID,
        actor_id: UUID,
        file_name: str,
        media_type: str,
        data: bytes,
        role: UploadRole,
        idempotency_key: str,
    ) -> UploadedDocument:
        if len(data) == 0 or len(data) > MAX_DOCUMENT_BYTES:
            raise DocumentUploadInvalid("document_size_invalid")
        if media_type not in {PDF_MEDIA_TYPE, DOCX_MEDIA_TYPE}:
            raise DocumentUploadInvalid("media_type_unsupported")
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}", idempotency_key) is None:
            raise DocumentUploadInvalid("idempotency_key_invalid")
        safe_name = self._file_name(file_name)
        source_kind_by_role: dict[UploadRole, str] = {
            "primary_contract": "main",
            "attachment": "attachment",
            "customer_contract": "customer",
        }
        source_kind = source_kind_by_role[role]

        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
            case = self._case_member(connection, tenant_id, case_id, actor_id)
            if case["legal_entity_id"] is None and role != "primary_contract":
                raise DocumentUploadConflict("legal_entity_required_for_related_documents")
            existing = connection.execute(
                "SELECT * FROM document_versions WHERE tenant_id = %s AND uploaded_case_id = %s "
                "AND upload_key = %s",
                (tenant_id, case_id, idempotency_key),
            ).fetchone()
        if existing is not None:
            if (
                existing["uploaded_by"] != actor_id
                or existing["source_sha256"] != self._digest(data)
                or existing["source_name"] != safe_name
                or existing["media_type"] != media_type
                or existing["source_kind"] != source_kind
            ):
                raise DocumentUploadConflict("idempotency_key_conflict")
            return self._uploaded(existing)

        candidate_refs = [ObjectRef(tenant_id, case_id, self._digest(data), len(data))]
        try:
            with object_key_locks(
                self._database_url,
                self._object_store.backend,
                tenant_id,
                case_id,
                {reference.sha256 for reference in candidate_refs},
            ):
                original_ref = self._object_store.put(tenant_id, case_id, data)
                return self._persist_upload(
                    tenant_id,
                    case_id,
                    actor_id,
                    safe_name,
                    source_kind,
                    media_type,
                    idempotency_key,
                    original_ref,
                )
        except Exception:
            # Covers store failures and database rollback after either immutable write.
            self._cleanup_unreferenced_objects(candidate_refs)
            raise

    def _persist_upload(
        self,
        tenant_id: UUID,
        case_id: UUID,
        actor_id: UUID,
        safe_name: str,
        source_kind: str,
        media_type: str,
        idempotency_key: str,
        original_ref: ObjectRef,
    ) -> UploadedDocument:
        document_id = uuid4()

        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
            case = self._case_member(connection, tenant_id, case_id, actor_id)
            next_version = int(case["version"]) + 1
            row = connection.execute(
                """
                INSERT INTO document_versions
                  (tenant_id, id, uploaded_case_id, legal_entity_id, source_kind, source_sha256,
                   has_restricted_amount, status, upload_key, uploaded_by, storage_backend,
                   source_name, media_type, size_bytes, parse_status, parser_version,
                   warnings, upload_case_version)
                VALUES (%s, %s, %s, %s, %s, %s, true, 'staged', %s, %s, %s, %s, %s,
                        %s, 'queued', 'pending', '[]'::jsonb, %s)
                ON CONFLICT (tenant_id, uploaded_case_id, upload_key) WHERE upload_key IS NOT NULL
                DO NOTHING
                RETURNING id, uploaded_case_id, source_name, source_kind, media_type, size_bytes,
                          source_sha256, parse_status, parser_version, parsed_sha256,
                          warnings, upload_case_version, status
                """,
                (
                    tenant_id,
                    document_id,
                    case_id,
                    case["legal_entity_id"],
                    source_kind,
                    original_ref.sha256,
                    idempotency_key,
                    actor_id,
                    self._object_store.backend,
                    safe_name,
                    media_type,
                    original_ref.size_bytes,
                    next_version,
                ),
            ).fetchone()
            if row is None:
                duplicate = connection.execute(
                    "SELECT * FROM document_versions WHERE tenant_id = %s "
                    "AND uploaded_case_id = %s "
                    "AND upload_key = %s",
                    (tenant_id, case_id, idempotency_key),
                ).fetchone()
                if (
                    duplicate is None
                    or duplicate["uploaded_by"] != actor_id
                    or duplicate["source_sha256"] != original_ref.sha256
                    or duplicate["source_name"] != safe_name
                    or duplicate["media_type"] != media_type
                    or duplicate["source_kind"] != source_kind
                ):
                    raise DocumentUploadConflict("idempotency_key_conflict")
                return self._uploaded(duplicate)
            connection.execute(
                "INSERT INTO document_parse_jobs "
                "(tenant_id, case_id, document_version_id, source_sha256) "
                "VALUES (%s, %s, %s, %s)",
                (tenant_id, case_id, row["id"], original_ref.sha256),
            )
            new_version = int(case["version"]) + 1
            connection.execute(
                "UPDATE audit_cases SET version = %s WHERE tenant_id = %s AND id = %s",
                (new_version, tenant_id, case_id),
            )
            if new_version != next_version:
                raise DocumentUploadConflict("case_version_changed")
            return self._uploaded(row)

    @staticmethod
    def _digest(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

"""M0 fixture-source preview and freeze; no real connector is implied."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Literal
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from contract_app.retrieval.authorization import AuthorizationProjector, TaskAccess

SourceStatus = Literal[
    "ready",
    "no_permission",
    "version_unavailable",
    "wrong_legal_entity",
    "invalid_relation",
    "relation_unverified",
    "restricted_amount",
    "index_unavailable",
    "parse_pending",
    "parse_failed",
]


class PreviewDenied(PermissionError):
    """Actor has no authority to preview or freeze this case."""


class PreviewConflict(ValueError):
    """A preview is stale, incomplete or inconsistent with current sources."""


@dataclass(frozen=True, slots=True)
class SourceItem:
    selection_id: UUID
    relation: str
    status: SourceStatus
    document_version_id: UUID | None = None
    source_sha256: str | None = None
    parser_version: str | None = None
    parsed_sha256: str | None = None
    rendered_pdf_sha256: str | None = None
    renderer_identity: str | None = None
    pageindex_configuration: dict[str, Any] | None = None
    pageindex_configuration_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class SourcePreview:
    id: UUID
    case_id: UUID
    purpose: str
    case_version: int
    manifest_sha256: str
    items: tuple[SourceItem, ...]


@dataclass(frozen=True, slots=True)
class FrozenInput:
    id: UUID
    case_id: UUID
    purpose: str
    case_version: int
    manifest_sha256: str
    document_version_ids: frozenset[UUID]


class InputSnapshotStore:
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url
        self._projector = AuthorizationProjector(database_url)

    @contextmanager
    def _transaction(self, tenant_id: UUID) -> Iterator[psycopg.Connection[Any]]:
        connection: psycopg.Connection[Any] = psycopg.connect(
            self._database_url, row_factory=dict_row
        )
        try:
            with connection:
                connection.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
                connection.execute(
                    "SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),)
                )
                yield connection
        except psycopg.errors.SerializationFailure as exc:
            raise PreviewConflict("concurrent_sources_changed") from exc

    @staticmethod
    def _case_and_member(connection: psycopg.Connection[Any], access: TaskAccess) -> dict[str, Any]:
        case = connection.execute(
            "SELECT id, version, status, legal_entity_id FROM audit_cases "
            "WHERE tenant_id = %s AND id = %s FOR UPDATE",
            (access.tenant_id, access.case_id),
        ).fetchone()
        if case is None or case["status"] == "cancelled":
            raise PreviewDenied("case_not_available")
        member = connection.execute(
            "SELECT role FROM case_members WHERE tenant_id = %s AND case_id = %s "
            "AND actor_id = %s AND revoked_at IS NULL",
            (access.tenant_id, access.case_id, access.actor_id),
        ).fetchone()
        if member is None or member["role"] not in {"submitter", "reviewer"}:
            raise PreviewDenied("case_member_not_authorized")
        if case["legal_entity_id"] is None:
            raise PreviewConflict("case_legal_entity_unconfirmed")
        return dict(case)

    def _collect(
        self, connection: psycopg.Connection[Any], access: TaskAccess, case: dict[str, Any]
    ) -> tuple[dict[str, Any], tuple[SourceItem, ...]]:
        allowed = self._projector.indexed_documents_in_transaction(access, connection)
        relations = (
            ("main", "attachment", "background")
            if access.purpose == "contract_review"
            else (
                "main",
                "upstream",
            )
        )
        selections = connection.execute(
            "SELECT id, document_version_id, relation, relation_verified_at "
            "FROM contract_selections WHERE tenant_id = %s AND case_id = %s "
            "AND revoked_at IS NULL AND relation = ANY(%s) "
            "ORDER BY relation, id",
            (access.tenant_id, access.case_id, list(relations)),
        ).fetchall()
        clock_row = connection.execute("SELECT clock_timestamp() AS current_time").fetchone()
        assert clock_row is not None
        current_time = clock_row["current_time"]
        records: list[dict[str, Any]] = []
        items: list[SourceItem] = []
        for selection in selections:
            relation = selection["relation"]
            document = connection.execute(
                "SELECT id, legal_entity_id, source_kind, source_sha256, media_type, "
                "parse_status, upload_key, "
                "parser_version, parsed_sha256, rendered_pdf_sha256, renderer_identity, "
                "has_restricted_amount, status FROM document_versions "
                "WHERE tenant_id = %s AND id = %s",
                (access.tenant_id, selection["document_version_id"]),
            ).fetchone()
            grant = connection.execute(
                "SELECT id, allow_text, allow_amount, valid_until FROM document_grants "
                "WHERE tenant_id = %s AND actor_id = %s AND document_version_id = %s "
                "AND purpose = %s AND revoked_at IS NULL",
                (
                    access.tenant_id,
                    access.actor_id,
                    selection["document_version_id"],
                    access.purpose,
                ),
            ).fetchone()
            index = connection.execute(
                "SELECT source_sha256, pageindex_doc_id, pageindex_configuration, "
                "pageindex_configuration_sha256, rendered_pdf_sha256, renderer_identity "
                "FROM document_indexes "
                "WHERE tenant_id = %s AND document_version_id = %s",
                (access.tenant_id, selection["document_version_id"]),
            ).fetchone()
            status: SourceStatus = "ready"
            if document is None or document["status"] != "active":
                status = "version_unavailable"
            elif (
                case["legal_entity_id"] is None
                or document["legal_entity_id"] != case["legal_entity_id"]
            ):
                status = "wrong_legal_entity"
            elif (
                (relation == "main" and document["source_kind"] != "main")
                or (relation == "attachment" and document["source_kind"] != "attachment")
                or (relation == "background" and document["source_kind"] != "history")
                or (relation == "upstream" and document["source_kind"] != "customer")
            ):
                status = "invalid_relation"
            elif relation == "upstream" and selection["relation_verified_at"] is None:
                status = "relation_unverified"
            elif grant is None or not grant["allow_text"] or grant["valid_until"] <= current_time:
                status = "no_permission"
            elif document["has_restricted_amount"] and not (
                grant["allow_amount"] and access.task_requires_amount
            ):
                status = "restricted_amount"
            elif document["upload_key"] is not None and document["parse_status"] in {
                "queued",
                "processing",
            }:
                status = "parse_pending"
            elif document["upload_key"] is not None and document["parse_status"] == "failed":
                status = "parse_failed"
            elif (
                (
                    document["media_type"]
                    == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                    and (
                        document["rendered_pdf_sha256"] is None
                        or document["renderer_identity"] is None
                    )
                )
                or index is None
                or index["source_sha256"] != document["source_sha256"]
                or (
                    index["rendered_pdf_sha256"] != document["rendered_pdf_sha256"]
                    or index["renderer_identity"] != document["renderer_identity"]
                )
            ):
                status = "index_unavailable"
            elif document["id"] not in allowed:
                status = "no_permission"
            record: dict[str, Any] = {
                "selection_id": str(selection["id"]),
                "relation": relation,
                "status": status,
            }
            if status == "ready":
                assert document is not None and grant is not None and index is not None
                record.update(
                    {
                        "document_version_id": str(document["id"]),
                        "source_sha256": document["source_sha256"],
                        "parser_version": document["parser_version"],
                        "parsed_sha256": document["parsed_sha256"],
                        "rendered_pdf_sha256": document["rendered_pdf_sha256"],
                        "renderer_identity": document["renderer_identity"],
                        "pageindex_doc_id": index["pageindex_doc_id"],
                        "pageindex_configuration": index["pageindex_configuration"],
                        "pageindex_configuration_sha256": index["pageindex_configuration_sha256"],
                        "grant_id": str(grant["id"]),
                        "grant_valid_until": grant["valid_until"].isoformat(),
                    }
                )
            records.append(record)
            items.append(
                SourceItem(
                    selection_id=selection["id"],
                    relation=relation,
                    status=status,
                    document_version_id=(
                        document["id"] if status == "ready" and document is not None else None
                    ),
                    source_sha256=(
                        document["source_sha256"]
                        if status == "ready" and document is not None
                        else None
                    ),
                    parser_version=(
                        document["parser_version"]
                        if status == "ready" and document is not None
                        else None
                    ),
                    parsed_sha256=(
                        document["parsed_sha256"]
                        if status == "ready" and document is not None
                        else None
                    ),
                    rendered_pdf_sha256=(
                        document["rendered_pdf_sha256"]
                        if status == "ready" and document is not None
                        else None
                    ),
                    renderer_identity=(
                        document["renderer_identity"]
                        if status == "ready" and document is not None
                        else None
                    ),
                    pageindex_configuration=(
                        index["pageindex_configuration"]
                        if status == "ready" and index is not None
                        else None
                    ),
                    pageindex_configuration_sha256=(
                        index["pageindex_configuration_sha256"]
                        if status == "ready" and index is not None
                        else None
                    ),
                )
            )
        manifest = {
            "version": 1,
            "mode": "m0_fixture",
            "purpose": access.purpose,
            "require_amount": access.task_requires_amount,
            "case_version": case["version"],
            "finance_status": "not_enabled",
            "documents": records,
        }
        return manifest, tuple(items)

    @staticmethod
    def _serialize(manifest: dict[str, Any]) -> tuple[str, str]:
        serialized = json.dumps(manifest, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return serialized, sha256(serialized.encode()).hexdigest()

    @staticmethod
    def _require_upstream_pair(purpose: str, ready_relations: set[str]) -> None:
        if purpose == "upstream_comparison" and not {"main", "upstream"}.issubset(ready_relations):
            raise PreviewConflict("upstream_contract_pair_required")

    def preview(self, access: TaskAccess) -> SourcePreview:
        if access.input_snapshot_id is not None:
            raise ValueError("preview_must_not_bind_snapshot")
        with self._transaction(access.tenant_id) as connection:
            case = self._case_and_member(connection, access)
            if case["status"] not in {"draft", "ready"}:
                raise PreviewConflict("case_not_freezable")
            manifest, items = self._collect(connection, access, case)
            serialized, digest = self._serialize(manifest)
            row = connection.execute(
                """
                INSERT INTO source_previews
                  (tenant_id, case_id, actor_id, purpose, require_amount,
                   case_version, manifest, manifest_sha256, expires_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s,
                        now() + interval '10 minutes')
                RETURNING id
                """,
                (
                    access.tenant_id,
                    access.case_id,
                    access.actor_id,
                    access.purpose,
                    access.task_requires_amount,
                    case["version"],
                    serialized,
                    digest,
                ),
            ).fetchone()
            assert row is not None
            return SourcePreview(
                row["id"], access.case_id, access.purpose, case["version"], digest, items
            )

    def freeze(
        self, access: TaskAccess, preview_id: UUID, expected_case_version: int, manifest_sha256: str
    ) -> FrozenInput:
        if access.input_snapshot_id is not None or expected_case_version <= 0:
            raise ValueError("invalid_freeze_request")
        with self._transaction(access.tenant_id) as connection:
            preview = connection.execute(
                "SELECT * FROM source_previews WHERE tenant_id = %s AND id = %s",
                (access.tenant_id, preview_id),
            ).fetchone()
            if (
                preview is None
                or preview["case_id"] != access.case_id
                or preview["actor_id"] != access.actor_id
            ):
                raise PreviewDenied("preview_not_authorized")
            case = self._case_and_member(connection, access)
            if (
                preview["purpose"] != access.purpose
                or preview["require_amount"] != access.task_requires_amount
            ):
                raise PreviewConflict("preview_scope_changed")
            existing = connection.execute(
                "SELECT id, case_version, manifest_sha256, manifest FROM input_snapshots "
                "WHERE tenant_id = %s AND preview_id = %s",
                (access.tenant_id, preview_id),
            ).fetchone()
            if existing is not None:
                if existing["manifest_sha256"] != manifest_sha256:
                    raise PreviewConflict("preview_hash_mismatch")
                existing_manifest = existing["manifest"]
                existing_documents = (
                    existing_manifest.get("documents")
                    if isinstance(existing_manifest, dict)
                    else None
                )
                ready_relations = {
                    row["relation"]
                    for row in existing_documents or []
                    if isinstance(row, dict) and row.get("status") == "ready"
                }
                self._require_upstream_pair(access.purpose, ready_relations)
                return self._frozen(existing, access.case_id, access.purpose)
            if case["status"] not in {"draft", "ready"}:
                raise PreviewConflict("case_not_freezable")
            clock_row = connection.execute("SELECT clock_timestamp() AS current_time").fetchone()
            assert clock_row is not None
            if (
                preview["expires_at"] <= clock_row["current_time"]
                or preview["case_version"] != expected_case_version
                or case["version"] != expected_case_version
                or preview["manifest_sha256"] != manifest_sha256
            ):
                raise PreviewConflict("preview_expired_or_stale")
            manifest, items = self._collect(connection, access, case)
            serialized, digest = self._serialize(manifest)
            if digest != manifest_sha256 or manifest != preview["manifest"]:
                raise PreviewConflict("preview_sources_changed")
            if not any(item.relation == "main" and item.status == "ready" for item in items):
                raise PreviewConflict("main_contract_not_ready")
            if any(item.status != "ready" for item in items):
                raise PreviewConflict("selected_source_not_ready")
            self._require_upstream_pair(
                access.purpose,
                {item.relation for item in items if item.status == "ready"},
            )
            snapshot = connection.execute(
                """
                INSERT INTO input_snapshots
                  (tenant_id, case_id, preview_id, frozen_by, purpose, require_amount,
                   case_version, manifest, manifest_sha256)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
                RETURNING id
                """,
                (
                    access.tenant_id,
                    access.case_id,
                    preview_id,
                    access.actor_id,
                    access.purpose,
                    access.task_requires_amount,
                    expected_case_version + 1,
                    serialized,
                    digest,
                ),
            ).fetchone()
            assert snapshot is not None
            for item in items:
                assert (
                    item.document_version_id is not None
                    and item.source_sha256 is not None
                    and item.pageindex_configuration is not None
                    and item.pageindex_configuration_sha256 is not None
                )
                connection.execute(
                    """
                    INSERT INTO input_snapshot_documents
                      (tenant_id, snapshot_id, document_version_id,
                      selection_id, source_sha256, relation, parser_version, parsed_sha256,
                       rendered_pdf_sha256, renderer_identity,
                       pageindex_configuration, pageindex_configuration_sha256)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
                    """,
                    (
                        access.tenant_id,
                        snapshot["id"],
                        item.document_version_id,
                        item.selection_id,
                        item.source_sha256,
                        item.relation,
                        item.parser_version,
                        item.parsed_sha256,
                        item.rendered_pdf_sha256,
                        item.renderer_identity,
                        json.dumps(item.pageindex_configuration, ensure_ascii=False),
                        item.pageindex_configuration_sha256,
                    ),
                )
            advanced = connection.execute(
                "UPDATE audit_cases SET status = 'ready', version = version + 1 "
                "WHERE tenant_id = %s AND id = %s AND version = %s RETURNING version",
                (access.tenant_id, access.case_id, expected_case_version),
            ).fetchone()
            if advanced is None or advanced["version"] != expected_case_version + 1:
                raise PreviewConflict("case_version_changed")
            return FrozenInput(
                snapshot["id"],
                access.case_id,
                access.purpose,
                expected_case_version + 1,
                digest,
                frozenset(item.document_version_id for item in items if item.document_version_id),
            )

    @staticmethod
    def _frozen(row: dict[str, Any], case_id: UUID, purpose: str) -> FrozenInput:
        documents = frozenset(
            UUID(item["document_version_id"])
            for item in row["manifest"]["documents"]
            if item["status"] == "ready"
        )
        return FrozenInput(
            row["id"], case_id, purpose, row["case_version"], row["manifest_sha256"], documents
        )

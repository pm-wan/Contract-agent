"""Current, deny-by-default database projection for model-facing document reads.

Grant creation is not exposed here. A task can bind an immutable input snapshot;
current selection and grants are still checked at every model-facing read.
"""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from contract_app.retrieval.pageindex_adapter import (
    IndexedDocument,
    PageIndexAdapter,
    RetrievalDenied,
    RetrievalScope,
)


@dataclass(frozen=True, slots=True)
class TaskAccess:
    tenant_id: UUID
    case_id: UUID
    actor_id: UUID
    purpose: str
    task_requires_amount: bool = False
    input_snapshot_id: UUID | None = None


class AuthorizationProjector:
    """Rebuild a model-visible document set from current selections and grants."""

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    def indexed_documents(self, access: TaskAccess) -> dict[UUID, IndexedDocument]:
        if access.purpose not in {"contract_review", "upstream_comparison"}:
            raise RetrievalDenied("purpose_not_authorized")
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            return self.indexed_documents_in_transaction(access, connection)

    def indexed_documents_in_transaction(
        self, access: TaskAccess, connection: psycopg.Connection[Any]
    ) -> dict[UUID, IndexedDocument]:
        """Use the same authorization predicate inside a preview/freeze transaction."""
        if access.purpose not in {"contract_review", "upstream_comparison"}:
            raise RetrievalDenied("purpose_not_authorized")
        connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(access.tenant_id),))
        connection.execute("SELECT set_config('app.actor_id', %s, true)", (str(access.actor_id),))
        rows = connection.execute(
            """
                SELECT d.id AS document_version_id, d.source_sha256,
                       i.pageindex_doc_id
                FROM audit_cases AS c
                JOIN case_members AS m
                  ON m.tenant_id = c.tenant_id AND m.case_id = c.id
                JOIN contract_selections AS s
                  ON s.tenant_id = c.tenant_id AND s.case_id = c.id
                JOIN document_versions AS d
                  ON d.tenant_id = s.tenant_id AND d.id = s.document_version_id
                JOIN document_grants AS g
                  ON g.tenant_id = d.tenant_id AND g.document_version_id = d.id
                JOIN document_indexes AS i
                  ON i.tenant_id = d.tenant_id AND i.document_version_id = d.id
                     AND i.source_sha256 = d.source_sha256
                WHERE c.tenant_id = %s AND c.id = %s AND c.legal_entity_id IS NOT NULL
                  AND m.actor_id = %s AND m.revoked_at IS NULL
                  AND s.revoked_at IS NULL
                  AND d.status = 'active' AND d.legal_entity_id = c.legal_entity_id
                  AND (d.upload_key IS NULL
                       OR d.parse_status IN ('completed', 'needs_material'))
                  AND g.actor_id = %s AND g.purpose = %s AND g.allow_text
                  AND g.revoked_at IS NULL AND g.valid_until > clock_timestamp()
                  AND (NOT d.has_restricted_amount
                       OR (g.allow_amount AND %s))
                  AND (
                    (s.relation = 'main' AND d.source_kind = 'main')
                    OR (%s = 'contract_review' AND s.relation = 'attachment'
                        AND d.source_kind = 'attachment')
                    OR (%s = 'contract_review' AND s.relation = 'background'
                        AND d.source_kind = 'history')
                    OR (%s = 'upstream_comparison' AND s.relation = 'upstream'
                        AND s.relation_verified_at IS NOT NULL
                        AND d.source_kind = 'customer')
                  )
                  AND (%s::uuid IS NULL OR EXISTS (
                    SELECT 1 FROM input_snapshot_documents AS f
                    JOIN input_snapshots AS snap
                      ON snap.tenant_id = f.tenant_id AND snap.id = f.snapshot_id
                    WHERE f.tenant_id = d.tenant_id AND f.snapshot_id = %s
                      AND f.document_version_id = d.id
                      AND f.source_sha256 = d.source_sha256
                      AND snap.case_id = c.id AND snap.purpose = %s
                  ))
                """,
            (
                access.tenant_id,
                access.case_id,
                access.actor_id,
                access.actor_id,
                access.purpose,
                access.task_requires_amount,
                access.purpose,
                access.purpose,
                access.purpose,
                access.input_snapshot_id,
                access.input_snapshot_id,
                access.purpose,
            ),
        ).fetchall()
        return {row["document_version_id"]: self._document(access, row) for row in rows}

    @staticmethod
    def _document(access: TaskAccess, row: dict[str, Any]) -> IndexedDocument:
        return IndexedDocument(
            tenant_id=access.tenant_id,
            case_id=access.case_id,
            document_version_id=row["document_version_id"],
            source_sha256=row["source_sha256"],
            pageindex_doc_id=row["pageindex_doc_id"],
        )

    def require_document(self, access: TaskAccess, document_version_id: UUID) -> IndexedDocument:
        document = self.indexed_documents(access).get(document_version_id)
        if document is None:
            raise RetrievalDenied("document_version_not_authorized")
        return document

    def require_result_sources(
        self, access: TaskAccess, source_document_version_ids: frozenset[UUID]
    ) -> None:
        """Fail closed before showing a derived result to a current viewer."""
        if not source_document_version_ids:
            raise RetrievalDenied("result_sources_missing")
        allowed = self.indexed_documents(access)
        if not source_document_version_ids.issubset(allowed):
            raise RetrievalDenied("result_source_not_authorized")


class AuthorizedPageIndex:
    """The only PageIndex surface to expose as an AgentScope retrieval tool."""

    def __init__(self, projector: AuthorizationProjector, adapter: PageIndexAdapter) -> None:
        self._projector = projector
        self._adapter = adapter

    def tree(self, access: TaskAccess, document_version_id: UUID) -> dict[str, Any]:
        self._require_snapshot(access)
        document = self._projector.require_document(access, document_version_id)
        return self._adapter.tree(self._scope(access, document), document)

    def page(
        self, access: TaskAccess, document_version_id: UUID, page_number: int
    ) -> list[dict[str, Any]]:
        self._require_snapshot(access)
        document = self._projector.require_document(access, document_version_id)
        return self._adapter.page(self._scope(access, document), document, page_number)

    @staticmethod
    def _require_snapshot(access: TaskAccess) -> None:
        if access.input_snapshot_id is None:
            raise RetrievalDenied("input_snapshot_required")

    @staticmethod
    def _scope(access: TaskAccess, document: IndexedDocument) -> RetrievalScope:
        return RetrievalScope(
            tenant_id=access.tenant_id,
            case_id=access.case_id,
            purpose=access.purpose,
            allowed_document_version_ids=frozenset({document.document_version_id}),
        )

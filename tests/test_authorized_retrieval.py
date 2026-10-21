import os
from hashlib import sha256
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest

from contract_app.contextassembly.snapshot import InputSnapshotStore
from contract_app.retrieval.authorization import (
    AuthorizationProjector,
    AuthorizedPageIndex,
    TaskAccess,
)
from contract_app.retrieval.pageindex_adapter import PageIndexAdapter, RetrievalDenied
from contract_app.workflow.job_store import JobStore

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
TEST_ADMIN_DATABASE_URL = os.environ.get("TEST_ADMIN_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL or not TEST_ADMIN_DATABASE_URL,
    reason="both TEST_DATABASE_URL and TEST_ADMIN_DATABASE_URL are required",
)


class CountingPageIndex:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def submit_document(self, file_path: str, *, wait: bool = False) -> dict[str, Any]:
        raise AssertionError("indexing is outside model-facing retrieval")

    def get_tree(
        self, doc_id: str, node_summary: bool = False, include_text: bool = True
    ) -> dict[str, Any]:
        self.calls.append(("tree", doc_id))
        return {"doc_id": doc_id}

    def get_page_content(self, doc_id: str, pages: str) -> list[dict[str, Any]]:
        self.calls.append(("page", f"{doc_id}:{pages}"))
        return [{"page": int(pages), "text": "synthetic clause"}]


def _admin_connection(tenant_id: UUID) -> psycopg.Connection[Any]:
    assert TEST_ADMIN_DATABASE_URL is not None
    connection = psycopg.connect(TEST_ADMIN_DATABASE_URL)
    connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
    return connection


def _seed_case() -> tuple[UUID, UUID, UUID, dict[str, UUID]]:
    assert TEST_DATABASE_URL is not None
    tenant_id, actor_id, legal_entity_id = uuid4(), uuid4(), uuid4()
    case_id, _ = JobStore(TEST_DATABASE_URL).create_case_and_run(tenant_id, "access fixture")
    documents = {kind: uuid4() for kind in ("main", "history", "customer", "unselected")}
    with _admin_connection(tenant_id) as connection:
        connection.execute(
            "UPDATE audit_cases SET legal_entity_id = %s WHERE id = %s",
            (legal_entity_id, case_id),
        )
        connection.execute(
            "INSERT INTO case_members (tenant_id, case_id, actor_id, role) "
            "VALUES (%s, %s, %s, 'reviewer')",
            (tenant_id, case_id, actor_id),
        )
        for name, document_id in documents.items():
            kind = "history" if name == "unselected" else name
            digest = sha256(name.encode()).hexdigest()
            connection.execute(
                """
                INSERT INTO document_versions
                  (tenant_id, id, legal_entity_id, source_kind,
                   source_sha256, has_restricted_amount)
                VALUES (%s, %s, %s, %s, %s, true)
                """,
                (tenant_id, document_id, legal_entity_id, kind, digest),
            )
            connection.execute(
                """
                INSERT INTO document_indexes
                  (tenant_id, document_version_id, source_sha256, pageindex_doc_id)
                VALUES (%s, %s, %s, %s)
                """,
                (tenant_id, document_id, digest, f"pi-{document_id}"),
            )
            connection.execute(
                """
                INSERT INTO document_grants
                  (tenant_id, actor_id, document_version_id, purpose,
                   allow_text, allow_amount, valid_until)
                VALUES (%s, %s, %s, 'contract_review', true, %s,
                        now() + interval '1 hour')
                """,
                (tenant_id, actor_id, document_id, name != "history"),
            )
        for name, relation in (
            ("main", "main"),
            ("history", "background"),
            ("customer", "upstream"),
        ):
            connection.execute(
                """
                INSERT INTO contract_selections
                  (tenant_id, case_id, document_version_id, relation, selected_by,
                   relation_ref, relation_verified_at)
                VALUES (%s, %s, %s, %s, %s,
                        CASE WHEN %s = 'upstream' THEN 'synthetic-project-link' END,
                        CASE WHEN %s = 'upstream' THEN now() END)
                """,
                (tenant_id, case_id, documents[name], relation, actor_id, relation, relation),
            )
        for name in ("main", "customer"):
            connection.execute(
                """
                INSERT INTO document_grants
                  (tenant_id, actor_id, document_version_id, purpose,
                   allow_text, allow_amount, valid_until)
                VALUES (%s, %s, %s, 'upstream_comparison', true, true,
                        now() + interval '1 hour')
                """,
                (tenant_id, actor_id, documents[name]),
            )
    return tenant_id, case_id, actor_id, documents


def test_current_grants_selection_purpose_and_amount_gate_sdk_calls() -> None:
    assert TEST_DATABASE_URL is not None
    tenant_id, case_id, actor_id, documents = _seed_case()
    client = CountingPageIndex()
    gateway = AuthorizedPageIndex(
        AuthorizationProjector(TEST_DATABASE_URL), PageIndexAdapter(client)
    )
    unbound_review = TaskAccess(tenant_id, case_id, actor_id, "contract_review", True)
    with pytest.raises(RetrievalDenied, match="input_snapshot_required"):
        gateway.tree(unbound_review, documents["main"])
    assert not client.calls
    projector = AuthorizationProjector(TEST_DATABASE_URL)
    assert documents["history"] not in projector.indexed_documents(unbound_review)
    with _admin_connection(tenant_id) as connection:
        connection.execute(
            "UPDATE document_grants SET allow_amount = true "
            "WHERE document_version_id = %s AND actor_id = %s",
            (documents["history"], actor_id),
        )
    store = InputSnapshotStore(TEST_DATABASE_URL)
    preview = store.preview(unbound_review)
    frozen = store.freeze(unbound_review, preview.id, preview.case_version, preview.manifest_sha256)
    review = TaskAccess(tenant_id, case_id, actor_id, "contract_review", True, frozen.id)
    no_amount_task = TaskAccess(tenant_id, case_id, actor_id, "contract_review", False, frozen.id)

    assert gateway.tree(review, documents["main"])["doc_id"] == f"pi-{documents['main']}"
    assert gateway.page(review, documents["main"], 6)[0]["page"] == 6
    assert len(client.calls) == 2
    upstream_unbound = TaskAccess(tenant_id, case_id, actor_id, "upstream_comparison", True)
    upstream_preview = store.preview(upstream_unbound)
    upstream_frozen = store.freeze(
        upstream_unbound,
        upstream_preview.id,
        upstream_preview.case_version,
        upstream_preview.manifest_sha256,
    )
    upstream = TaskAccess(
        tenant_id, case_id, actor_id, "upstream_comparison", True, upstream_frozen.id
    )
    assert gateway.tree(upstream, documents["customer"])["doc_id"] == f"pi-{documents['customer']}"
    assert len(client.calls) == 3
    with pytest.raises(RetrievalDenied):
        gateway.tree(
            TaskAccess(tenant_id, case_id, actor_id, "contract_review", True, upstream_frozen.id),
            documents["main"],
        )
    for denied in (
        documents["customer"],  # selected only for upstream comparison
        documents["unselected"],  # granted, but never selected for this case
    ):
        with pytest.raises(RetrievalDenied):
            gateway.tree(review, denied)
    with pytest.raises(RetrievalDenied):
        gateway.page(no_amount_task, documents["main"], 6)
    with pytest.raises(RetrievalDenied):
        gateway.tree(
            TaskAccess(uuid4(), case_id, actor_id, "contract_review", True, frozen.id),
            documents["main"],
        )
    assert len(client.calls) == 3
    gateway.tree(review, documents["history"])
    assert len(client.calls) == 4

    with _admin_connection(tenant_id) as connection:
        connection.execute(
            "UPDATE document_grants SET revoked_at = now() "
            "WHERE document_version_id = %s AND actor_id = %s",
            (documents["main"], actor_id),
        )
    with pytest.raises(RetrievalDenied):
        gateway.page(review, documents["main"], 6)
    assert len(client.calls) == 4


def test_derived_result_rechecks_viewer_and_current_source_state() -> None:
    assert TEST_DATABASE_URL is not None
    tenant_id, case_id, actor_id, documents = _seed_case()
    projector = AuthorizationProjector(TEST_DATABASE_URL)
    review = TaskAccess(tenant_id, case_id, actor_id, "contract_review", True)
    projector.require_result_sources(review, frozenset({documents["main"]}))
    with pytest.raises(RetrievalDenied):
        projector.require_result_sources(
            review, frozenset({documents["main"], documents["customer"]})
        )
    with pytest.raises(RetrievalDenied):
        projector.require_result_sources(review, frozenset())

    with _admin_connection(tenant_id) as connection:
        connection.execute(
            "UPDATE case_members SET revoked_at = now() WHERE case_id = %s AND actor_id = %s",
            (case_id, actor_id),
        )
    with pytest.raises(RetrievalDenied):
        projector.require_result_sources(review, frozenset({documents["main"]}))


def test_wrong_legal_entity_and_superseded_version_are_denied() -> None:
    assert TEST_DATABASE_URL is not None
    tenant_id, case_id, actor_id, documents = _seed_case()
    projector = AuthorizationProjector(TEST_DATABASE_URL)
    review = TaskAccess(tenant_id, case_id, actor_id, "contract_review", True)
    with _admin_connection(tenant_id) as connection:
        connection.execute(
            "UPDATE document_versions SET legal_entity_id = %s WHERE id = %s",
            (uuid4(), documents["main"]),
        )
    with pytest.raises(RetrievalDenied):
        projector.require_document(review, documents["main"])
    with _admin_connection(tenant_id) as connection:
        connection.execute(
            "UPDATE document_versions SET status = 'superseded' WHERE id = %s",
            (documents["history"],),
        )
    with pytest.raises(RetrievalDenied):
        projector.require_document(review, documents["history"])


def test_upstream_selection_cannot_be_active_without_verified_project_link() -> None:
    tenant_id, case_id, actor_id, documents = _seed_case()
    with pytest.raises(psycopg.errors.CheckViolation), _admin_connection(tenant_id) as connection:
        connection.execute(
            "UPDATE contract_selections SET revoked_at = now() "
            "WHERE case_id = %s AND document_version_id = %s",
            (case_id, documents["customer"]),
        )
        connection.execute(
            """
            INSERT INTO contract_selections
              (tenant_id, case_id, document_version_id, relation, selected_by)
            VALUES (%s, %s, %s, 'upstream', %s)
            """,
            (tenant_id, case_id, documents["customer"], actor_id),
        )

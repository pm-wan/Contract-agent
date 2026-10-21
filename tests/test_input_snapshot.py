import os
from hashlib import sha256
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from contract_app.api import create_app
from contract_app.api_snapshots import Principal, get_snapshot_store, require_principal
from contract_app.contextassembly.snapshot import InputSnapshotStore, PreviewConflict, PreviewDenied
from contract_app.retrieval.authorization import AuthorizationProjector, TaskAccess
from contract_app.retrieval.pageindex_adapter import RetrievalDenied
from contract_app.workflow.job_store import BoundRunConflict, BoundRunDenied, JobStore

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
TEST_ADMIN_DATABASE_URL = os.environ.get("TEST_ADMIN_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL or not TEST_ADMIN_DATABASE_URL,
    reason="both TEST_DATABASE_URL and TEST_ADMIN_DATABASE_URL are required",
)


def _admin(tenant_id: UUID) -> psycopg.Connection[Any]:
    assert TEST_ADMIN_DATABASE_URL is not None
    connection = psycopg.connect(TEST_ADMIN_DATABASE_URL)
    connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
    return connection


def _case() -> tuple[UUID, UUID, UUID, UUID]:
    tenant, actor, legal_entity = uuid4(), uuid4(), uuid4()
    with _admin(tenant) as connection:
        row = connection.execute(
            "INSERT INTO audit_cases (tenant_id, title, legal_entity_id) "
            "VALUES (%s, 'freeze fixture', %s) RETURNING id",
            (tenant, legal_entity),
        ).fetchone()
        assert row is not None
        case_id = row[0]
        connection.execute(
            "INSERT INTO case_members (tenant_id, case_id, actor_id, role) "
            "VALUES (%s, %s, %s, 'reviewer')",
            (tenant, case_id, actor),
        )
    return tenant, case_id, actor, legal_entity


def _document(
    tenant: UUID,
    case_id: UUID,
    actor: UUID,
    legal_entity: UUID,
    *,
    kind: str,
    relation: str,
    purpose: str,
    amount: bool = False,
    indexed: bool = True,
    verified_relation: bool = True,
) -> UUID:
    document_id = uuid4()
    digest = sha256(str(document_id).encode()).hexdigest()
    with _admin(tenant) as connection:
        connection.execute(
            "INSERT INTO document_versions "
            "(tenant_id, id, legal_entity_id, source_kind, source_sha256, has_restricted_amount) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (tenant, document_id, legal_entity, kind, digest, amount),
        )
        if indexed:
            connection.execute(
                "INSERT INTO document_indexes "
                "(tenant_id, document_version_id, source_sha256, pageindex_doc_id) "
                "VALUES (%s, %s, %s, %s)",
                (tenant, document_id, digest, f"pi-{document_id}"),
            )
        connection.execute(
            "INSERT INTO document_grants "
            "(tenant_id, actor_id, document_version_id, purpose, "
            "allow_text, allow_amount, valid_until) "
            "VALUES (%s, %s, %s, %s, true, true, now() + interval '1 hour')",
            (tenant, actor, document_id, purpose),
        )
        connection.execute(
            "INSERT INTO contract_selections "
            "(tenant_id, case_id, document_version_id, relation, selected_by, "
            "relation_ref, relation_verified_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, "
            "CASE WHEN %s THEN now() ELSE NULL END)",
            (
                tenant,
                case_id,
                document_id,
                relation,
                actor,
                "verified-project" if relation == "upstream" and verified_relation else None,
                verified_relation,
            ),
        )
    return document_id


def test_preview_freeze_is_idempotent_and_limits_model_to_frozen_versions() -> None:
    assert TEST_DATABASE_URL is not None
    tenant, case_id, actor, entity = _case()
    main = _document(
        tenant, case_id, actor, entity, kind="main", relation="main", purpose="contract_review"
    )
    history = _document(
        tenant,
        case_id,
        actor,
        entity,
        kind="history",
        relation="background",
        purpose="contract_review",
    )
    access = TaskAccess(tenant, case_id, actor, "contract_review")
    store = InputSnapshotStore(TEST_DATABASE_URL)
    preview = store.preview(access)
    assert preview.case_version == 1
    assert len(preview.items) == 2 and {item.status for item in preview.items} == {"ready"}
    frozen = store.freeze(access, preview.id, preview.case_version, preview.manifest_sha256)
    assert frozen.case_version == 2
    assert frozen.document_version_ids == {main, history}
    assert store.freeze(access, preview.id, preview.case_version, preview.manifest_sha256) == frozen

    newer = _document(
        tenant,
        case_id,
        actor,
        entity,
        kind="history",
        relation="background",
        purpose="contract_review",
    )
    projector = AuthorizationProjector(TEST_DATABASE_URL)
    assert newer in projector.indexed_documents(access)
    frozen_access = TaskAccess(
        tenant, case_id, actor, "contract_review", input_snapshot_id=frozen.id
    )
    assert set(projector.indexed_documents(frozen_access)) == {main, history}
    with pytest.raises(RetrievalDenied, match="document_version_not_authorized"):
        projector.require_document(frozen_access, newer)

    with _admin(tenant) as connection:
        connection.execute(
            "UPDATE document_grants SET revoked_at = now() "
            "WHERE tenant_id = %s AND actor_id = %s AND document_version_id = %s",
            (tenant, actor, history),
        )
    assert set(projector.indexed_documents(frozen_access)) == {main}
    with pytest.raises(RetrievalDenied, match="result_source_not_authorized"):
        projector.require_result_sources(frozen_access, frozenset({main, history}))
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant),))
        assert connection.execute(
            "SELECT status, version FROM audit_cases WHERE id = %s", (case_id,)
        ).fetchone() == ("ready", 2)
        assert connection.execute(
            "SELECT count(*) FROM input_snapshot_documents WHERE snapshot_id = %s", (frozen.id,)
        ).fetchone() == (2,)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute(
                "UPDATE input_snapshots SET manifest_sha256 = %s WHERE id = %s",
                ("0" * 64, frozen.id),
            )


def test_http_preview_freeze_requires_trusted_identity_and_hides_errors() -> None:
    assert TEST_DATABASE_URL is not None
    tenant, case_id, actor, entity = _case()
    main = _document(
        tenant, case_id, actor, entity, kind="main", relation="main", purpose="contract_review"
    )
    app = create_app()
    app.dependency_overrides[get_snapshot_store] = lambda: InputSnapshotStore(TEST_DATABASE_URL)
    client = TestClient(app)
    route = f"/api/v1/cases/{case_id}/source-previews"
    denied = client.post(route, json={"purpose": "contract_review"})
    assert denied.status_code == 401
    assert denied.headers["content-type"] == "application/problem+json"
    assert denied.json()["code"] == "unauthorized"
    assert denied.json()["trace_id"] == denied.headers["X-Request-ID"]
    app.dependency_overrides[require_principal] = lambda: Principal(tenant, actor)
    injected = client.post(route, json={"purpose": "contract_review", "tenant_id": str(uuid4())})
    assert injected.status_code == 422
    assert "tenant_id" not in injected.text
    preview_response = client.post(route, json={"purpose": "contract_review"})
    assert preview_response.status_code == 201
    preview = preview_response.json()
    assert preview["items"][0]["document_version_id"] == str(main)
    freeze_route = f"/api/v1/cases/{case_id}/input-snapshots"
    command = {
        "purpose": "contract_review",
        "preview_id": preview["preview_id"],
        "expected_case_version": preview["case_version"],
        "manifest_sha256": preview["manifest_sha256"],
    }
    frozen = client.post(freeze_route, json=command)
    assert frozen.status_code == 200
    assert frozen.json()["document_version_ids"] == [str(main)]
    assert client.post(freeze_route, json=command).json() == frozen.json()
    app.dependency_overrides[require_principal] = lambda: Principal(tenant, uuid4())
    other_actor = client.post(freeze_route, json=command)
    assert other_actor.status_code == 403
    assert "preview_not_authorized" not in other_actor.text


def test_http_freeze_rejects_stale_preview_without_source_leak() -> None:
    assert TEST_DATABASE_URL is not None
    tenant, case_id, actor, entity = _case()
    main = _document(
        tenant, case_id, actor, entity, kind="main", relation="main", purpose="contract_review"
    )
    app = create_app()
    app.dependency_overrides[get_snapshot_store] = lambda: InputSnapshotStore(TEST_DATABASE_URL)
    app.dependency_overrides[require_principal] = lambda: Principal(tenant, actor)
    client = TestClient(app)
    preview = client.post(
        f"/api/v1/cases/{case_id}/source-previews", json={"purpose": "contract_review"}
    ).json()
    with _admin(tenant) as connection:
        connection.execute(
            "UPDATE document_grants SET revoked_at = now() "
            "WHERE tenant_id = %s AND actor_id = %s AND document_version_id = %s",
            (tenant, actor, main),
        )
    response = client.post(
        f"/api/v1/cases/{case_id}/input-snapshots",
        json={
            "purpose": "contract_review",
            "preview_id": preview["preview_id"],
            "expected_case_version": preview["case_version"],
            "manifest_sha256": preview["manifest_sha256"],
        },
    )
    assert response.status_code == 409
    assert response.json()["code"] == "conflict"
    assert str(main) not in response.text
    assert "preview_sources_changed" not in response.text


def test_parser_artifact_identity_is_part_of_preview_and_freeze() -> None:
    assert TEST_DATABASE_URL is not None and TEST_ADMIN_DATABASE_URL is not None
    tenant, case_id, actor, entity = _case()
    document_id = _document(
        tenant,
        case_id,
        actor,
        entity,
        kind="main",
        relation="main",
        purpose="contract_review",
    )
    parsed_digest = "a" * 64
    with _admin(tenant) as connection:
        connection.execute(
            "UPDATE document_versions SET parser_version = 'parser-test-v1', "
            "parsed_sha256 = %s, parsed_size_bytes = 128 "
            "WHERE tenant_id = %s AND id = %s",
            (parsed_digest, tenant, document_id),
        )

    access = TaskAccess(tenant, case_id, actor, "contract_review")
    store = InputSnapshotStore(TEST_DATABASE_URL)
    preview = store.preview(access)
    item = preview.items[0]
    assert item.parser_version == "parser-test-v1"
    assert item.parsed_sha256 == parsed_digest

    with _admin(tenant) as connection:
        connection.execute(
            "UPDATE document_versions SET parser_version = 'parser-test-v2' "
            "WHERE tenant_id = %s AND id = %s",
            (tenant, document_id),
        )
    with pytest.raises(PreviewConflict, match="preview_sources_changed"):
        store.freeze(access, preview.id, preview.case_version, preview.manifest_sha256)


def test_run_binds_current_snapshot_and_rechecks_authority() -> None:
    assert TEST_DATABASE_URL is not None
    tenant, case_id, actor, entity = _case()
    main = _document(
        tenant, case_id, actor, entity, kind="main", relation="main", purpose="contract_review"
    )
    store = InputSnapshotStore(TEST_DATABASE_URL)
    access = TaskAccess(tenant, case_id, actor, "contract_review")
    preview = store.preview(access)
    frozen = store.freeze(access, preview.id, preview.case_version, preview.manifest_sha256)
    bound = TaskAccess(tenant, case_id, actor, "contract_review", input_snapshot_id=frozen.id)
    jobs = JobStore(TEST_DATABASE_URL)
    with pytest.raises(BoundRunConflict, match="input_snapshot_required"):
        jobs.create_bound_run(access, "run-start-001")
    with pytest.raises(BoundRunDenied, match="run_task_type_not_enabled"):
        jobs.create_bound_run(bound, "run-start-unsupported", initial_task_type="other_task")
    for request_key, payload in (
        ("run-payload-mismatch", {"input_snapshot_id": str(uuid4())}),
        (
            "run-payload-extra",
            {"input_snapshot_id": str(frozen.id), "unrecognized": "must-not-persist"},
        ),
    ):
        with pytest.raises(ValueError, match="review_task_payload_invalid"):
            jobs.create_bound_run(
                bound,
                request_key,
                initial_task_type="contract_review",
                initial_task_payload=payload,
                definition_snapshot={},
            )
    with pytest.raises(BoundRunDenied):
        jobs.create_bound_run(
            TaskAccess(tenant, case_id, uuid4(), "contract_review", input_snapshot_id=frozen.id),
            "run-start-001",
        )
    with pytest.raises(BoundRunDenied):
        jobs.create_bound_run(
            TaskAccess(uuid4(), case_id, actor, "contract_review", input_snapshot_id=frozen.id),
            "run-start-001",
        )
    run_id = jobs.create_bound_run(bound, "run-start-001")
    assert jobs.create_bound_run(bound, "run-start-001") == run_id
    with _admin(tenant) as connection:
        assert connection.execute(
            "SELECT input_snapshot_id, started_by, request_key FROM audit_runs WHERE id = %s",
            (run_id,),
        ).fetchone() == (frozen.id, actor, "run-start-001")
        assert connection.execute(
            "SELECT count(*) FROM audit_runs WHERE input_snapshot_id = %s", (frozen.id,)
        ).fetchone() == (1,)
        other_case = connection.execute(
            "INSERT INTO audit_cases (tenant_id, title, legal_entity_id) "
            "VALUES (%s, 'wrong case', %s) RETURNING id",
            (tenant, entity),
        ).fetchone()
        assert other_case is not None
        connection.execute(
            "INSERT INTO case_members (tenant_id, case_id, actor_id, role) "
            "VALUES (%s, %s, %s, 'reviewer')",
            (tenant, other_case[0], actor),
        )
    with pytest.raises(psycopg.errors.ForeignKeyViolation), _admin(tenant) as connection:
        connection.execute(
            "INSERT INTO audit_runs "
            "(tenant_id, case_id, input_snapshot_id, started_by, request_key) "
            "VALUES (%s, %s, %s, %s, 'wrong-case-run')",
            (tenant, other_case[0], frozen.id, actor),
        )
    with (
        pytest.raises(psycopg.errors.CheckViolation, match="run_binding_immutable"),
        psycopg.connect(TEST_DATABASE_URL) as connection,
    ):
        connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant),))
        connection.execute(
            "UPDATE audit_runs SET request_key = 'changed-key' WHERE id = %s", (run_id,)
        )
    assert jobs.cancel_run(tenant, run_id)
    with _admin(tenant) as connection:
        connection.execute(
            "UPDATE document_grants SET revoked_at = now() WHERE tenant_id = %s "
            "AND actor_id = %s AND document_version_id = %s",
            (tenant, actor, main),
        )
    with pytest.raises(BoundRunDenied, match="input_snapshot_sources_not_authorized"):
        jobs.create_bound_run(bound, "run-start-002")
    with pytest.raises(BoundRunDenied):
        jobs.create_bound_run(bound, "run-start-001")


def test_new_freeze_supersedes_old_snapshot_for_run_creation() -> None:
    assert TEST_DATABASE_URL is not None
    tenant, case_id, actor, entity = _case()
    main = _document(
        tenant, case_id, actor, entity, kind="main", relation="main", purpose="contract_review"
    )
    store = InputSnapshotStore(TEST_DATABASE_URL)
    access = TaskAccess(tenant, case_id, actor, "contract_review")
    first_preview = store.preview(access)
    first = store.freeze(
        access, first_preview.id, first_preview.case_version, first_preview.manifest_sha256
    )
    second_preview = store.preview(access)
    second = store.freeze(
        access, second_preview.id, second_preview.case_version, second_preview.manifest_sha256
    )
    jobs = JobStore(TEST_DATABASE_URL)
    with pytest.raises(BoundRunConflict, match="input_snapshot_stale_or_scope_changed"):
        jobs.create_bound_run(
            TaskAccess(tenant, case_id, actor, "contract_review", input_snapshot_id=first.id),
            "run-start-003",
        )
    with pytest.raises(BoundRunConflict):
        jobs.create_bound_run(
            TaskAccess(tenant, case_id, actor, "upstream_comparison", input_snapshot_id=second.id),
            "run-start-003",
        )
    valid = TaskAccess(tenant, case_id, actor, "contract_review", input_snapshot_id=second.id)
    assert jobs.create_bound_run(valid, "run-start-003")
    second_actor = uuid4()
    with _admin(tenant) as connection:
        connection.execute(
            "INSERT INTO case_members (tenant_id, case_id, actor_id, role) "
            "VALUES (%s, %s, %s, 'reviewer')",
            (tenant, case_id, second_actor),
        )
        connection.execute(
            "INSERT INTO document_grants "
            "(tenant_id, actor_id, document_version_id, purpose, allow_text, allow_amount, "
            "valid_until) VALUES (%s, %s, %s, 'contract_review', true, false, "
            "now() + interval '1 hour')",
            (tenant, second_actor, main),
        )
    with pytest.raises(BoundRunConflict, match="request_key_conflict"):
        other_access = TaskAccess(
            tenant, case_id, second_actor, "contract_review", input_snapshot_id=second.id
        )
        jobs.create_bound_run(
            other_access,
            "run-start-003",
        )


def test_changed_or_expired_preview_cannot_freeze() -> None:
    assert TEST_DATABASE_URL is not None
    tenant, case_id, actor, entity = _case()
    main = _document(
        tenant, case_id, actor, entity, kind="main", relation="main", purpose="contract_review"
    )
    access = TaskAccess(tenant, case_id, actor, "contract_review")
    store = InputSnapshotStore(TEST_DATABASE_URL)
    preview = store.preview(access)
    with _admin(tenant) as connection:
        connection.execute(
            "UPDATE document_grants SET revoked_at = now() "
            "WHERE tenant_id = %s AND actor_id = %s AND document_version_id = %s",
            (tenant, actor, main),
        )
    with pytest.raises(PreviewConflict, match="preview_sources_changed"):
        store.freeze(access, preview.id, preview.case_version, preview.manifest_sha256)
    with _admin(tenant) as connection:
        connection.execute(
            "UPDATE source_previews SET created_at = now() - interval '20 minutes', "
            "expires_at = now() - interval '10 minutes' "
            "WHERE tenant_id = %s AND id = %s",
            (tenant, preview.id),
        )
    with pytest.raises(PreviewConflict, match="preview_expired_or_stale"):
        store.freeze(access, preview.id, preview.case_version, preview.manifest_sha256)
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant),))
        assert connection.execute(
            "SELECT count(*) FROM input_snapshots WHERE case_id = %s", (case_id,)
        ).fetchone() == (0,)


def test_preview_does_not_expose_denied_document_metadata() -> None:
    assert TEST_DATABASE_URL is not None
    tenant, case_id, actor, entity = _case()
    main = _document(
        tenant,
        case_id,
        actor,
        entity,
        kind="main",
        relation="main",
        purpose="contract_review",
        amount=True,
    )
    access = TaskAccess(tenant, case_id, actor, "contract_review")
    store = InputSnapshotStore(TEST_DATABASE_URL)
    preview = store.preview(access)
    assert preview.items[0].status == "restricted_amount"
    assert preview.items[0].document_version_id is None
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant),))
        manifest = connection.execute(
            "SELECT manifest FROM source_previews WHERE id = %s", (preview.id,)
        ).fetchone()[0]
        assert str(main) not in str(manifest)
    with pytest.raises(PreviewConflict, match="main_contract_not_ready"):
        store.freeze(access, preview.id, preview.case_version, preview.manifest_sha256)
    amount_access = TaskAccess(tenant, case_id, actor, "contract_review", task_requires_amount=True)
    allowed = store.preview(amount_access)
    assert allowed.items[0].status == "ready"


def test_other_actor_or_tenant_cannot_freeze_preview() -> None:
    assert TEST_DATABASE_URL is not None
    tenant, case_id, actor, entity = _case()
    _document(
        tenant, case_id, actor, entity, kind="main", relation="main", purpose="contract_review"
    )
    store = InputSnapshotStore(TEST_DATABASE_URL)
    access = TaskAccess(tenant, case_id, actor, "contract_review")
    preview = store.preview(access)
    with pytest.raises(PreviewDenied):
        store.freeze(
            TaskAccess(uuid4(), case_id, actor, "contract_review"),
            preview.id,
            preview.case_version,
            preview.manifest_sha256,
        )
    with pytest.raises(PreviewDenied):
        store.freeze(
            TaskAccess(tenant, case_id, uuid4(), "contract_review"),
            preview.id,
            preview.case_version,
            preview.manifest_sha256,
        )


def test_upstream_freeze_requires_explicit_relation_and_purpose_grants() -> None:
    assert TEST_DATABASE_URL is not None
    tenant, case_id, actor, entity = _case()
    main = _document(
        tenant, case_id, actor, entity, kind="main", relation="main", purpose="upstream_comparison"
    )
    customer = _document(
        tenant,
        case_id,
        actor,
        entity,
        kind="customer",
        relation="upstream",
        purpose="upstream_comparison",
    )
    background = _document(
        tenant,
        case_id,
        actor,
        entity,
        kind="history",
        relation="background",
        purpose="contract_review",
    )
    access = TaskAccess(tenant, case_id, actor, "upstream_comparison")
    store = InputSnapshotStore(TEST_DATABASE_URL)
    preview = store.preview(access)
    assert {item.document_version_id for item in preview.items} == {main, customer}
    frozen = store.freeze(access, preview.id, preview.case_version, preview.manifest_sha256)
    assert frozen.document_version_ids == {main, customer}
    projector = AuthorizationProjector(TEST_DATABASE_URL)
    scoped = TaskAccess(tenant, case_id, actor, "upstream_comparison", input_snapshot_id=frozen.id)
    assert set(projector.indexed_documents(scoped)) == {main, customer}
    with pytest.raises(RetrievalDenied):
        projector.require_document(scoped, background)
    wrong_purpose = TaskAccess(
        tenant, case_id, actor, "contract_review", input_snapshot_id=frozen.id
    )
    assert projector.indexed_documents(wrong_purpose) == {}
    with pytest.raises(BoundRunDenied, match="run_purpose_not_enabled"):
        JobStore(TEST_DATABASE_URL).start_bound_run(
            tenant,
            case_id,
            actor,
            frozen.id,
            "upstream-comparison-must-not-run",
            {},
        )
    jobs = JobStore(TEST_DATABASE_URL)
    with pytest.raises(BoundRunDenied, match="run_purpose_not_enabled"):
        jobs.create_bound_run(scoped, "upstream-helper-no-task")
    with pytest.raises(BoundRunDenied, match="run_purpose_not_enabled"):
        jobs.create_bound_run(
            scoped,
            "upstream-helper-review-task",
            initial_task_type="contract_review",
            initial_task_payload={"input_snapshot_id": str(frozen.id)},
            definition_snapshot={},
        )
    with _admin(tenant) as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM audit_runs WHERE tenant_id = %s AND case_id = %s",
                (tenant, case_id),
            ).fetchone()[0]
            == 0
        )


def test_upstream_freeze_rejects_a_main_contract_without_customer_contract() -> None:
    assert TEST_DATABASE_URL is not None
    tenant, case_id, actor, entity = _case()
    _document(
        tenant,
        case_id,
        actor,
        entity,
        kind="main",
        relation="main",
        purpose="upstream_comparison",
    )
    access = TaskAccess(tenant, case_id, actor, "upstream_comparison")
    store = InputSnapshotStore(TEST_DATABASE_URL)
    preview = store.preview(access)

    assert [item.relation for item in preview.items] == ["main"]
    with pytest.raises(PreviewConflict, match="upstream_contract_pair_required"):
        store.freeze(access, preview.id, preview.case_version, preview.manifest_sha256)

    with _admin(tenant) as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM input_snapshots WHERE tenant_id = %s AND case_id = %s",
                (tenant, case_id),
            ).fetchone()[0]
            == 0
        )


def test_wrong_entity_and_missing_index_are_visible_blockers_not_frozen() -> None:
    assert TEST_DATABASE_URL is not None
    tenant, case_id, actor, entity = _case()
    _document(
        tenant, case_id, actor, entity, kind="main", relation="main", purpose="contract_review"
    )
    wrong_entity = _document(
        tenant,
        case_id,
        actor,
        uuid4(),
        kind="history",
        relation="background",
        purpose="contract_review",
    )
    no_index = _document(
        tenant,
        case_id,
        actor,
        entity,
        kind="history",
        relation="background",
        purpose="contract_review",
        indexed=False,
    )
    access = TaskAccess(tenant, case_id, actor, "contract_review")
    store = InputSnapshotStore(TEST_DATABASE_URL)
    preview = store.preview(access)
    assert {item.status for item in preview.items} == {
        "ready",
        "wrong_legal_entity",
        "index_unavailable",
    }
    assert all(item.document_version_id is None for item in preview.items if item.status != "ready")
    with pytest.raises(PreviewConflict, match="selected_source_not_ready"):
        store.freeze(access, preview.id, preview.case_version, preview.manifest_sha256)
    projector = AuthorizationProjector(TEST_DATABASE_URL)
    assert wrong_entity not in projector.indexed_documents(access)
    assert no_index not in projector.indexed_documents(access)

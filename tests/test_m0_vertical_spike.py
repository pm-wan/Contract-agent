"""Synthetic vertical seam, not a real model/PageIndex quality acceptance test."""

import asyncio
import json
import os
from collections.abc import AsyncGenerator
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4

import fitz
import psycopg
import pytest
from pydantic import SecretStr

from contract_app.agent.boundary import AgentCallBoundary
from contract_app.contextassembly.snapshot import InputSnapshotStore
from contract_app.documents.parser import PDF_MEDIA_TYPE, parse_document
from contract_app.retrieval.authorization import (
    AuthorizationProjector,
    AuthorizedPageIndex,
    TaskAccess,
)
from contract_app.retrieval.pageindex_adapter import (
    PageIndexAdapter,
    RetrievalDenied,
    RetrievalScope,
)
from contract_app.rules.compiler import load_development_rule_bytes
from contract_app.rules.runtime import RuleEvidence, RuleRun, SlotSubmission
from contract_app.workflow.job_store import JobStore

pytest.importorskip("agentscope")
from agentscope.agent import Agent, ReActConfig  # noqa: E402
from agentscope.credential import OpenAICredential  # noqa: E402
from agentscope.formatter import OpenAIChatFormatter  # noqa: E402
from agentscope.message import TextBlock, ToolCallBlock, UserMsg  # noqa: E402
from agentscope.model import ChatModelBase, ChatResponse  # noqa: E402
from agentscope.tool import FunctionTool, ToolChunk, Toolkit  # noqa: E402

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
TEST_ADMIN_DATABASE_URL = os.environ.get("TEST_ADMIN_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL or not TEST_ADMIN_DATABASE_URL,
    reason="synthetic vertical spike requires disposable admin and runtime database URLs",
)
RULE_PATH = (
    Path(__file__).parents[1] / "docs/development/06-rules/rules/legal.payment_conditions.v1.yaml"
)


class SyntheticPageIndexClient:
    def __init__(self, page_text: str) -> None:
        self.page_text = page_text
        self.calls: list[str] = []

    def submit_document(self, file_path: str, *, wait: bool = False) -> dict[str, Any]:
        assert wait and Path(file_path).is_file()
        self.calls.append("submit")
        return {"doc_id": "pi-synthetic-vertical"}

    def get_tree(
        self, doc_id: str, node_summary: bool = False, include_text: bool = True
    ) -> dict[str, Any]:
        self.calls.append("tree")
        return {"doc_id": doc_id}

    def get_page_content(self, doc_id: str, pages: str) -> list[dict[str, Any]]:
        assert doc_id == "pi-synthetic-vertical" and pages == "1"
        self.calls.append("page")
        return [{"page": 1, "text": self.page_text}]


class CandidateModel(ChatModelBase):
    def __init__(self, candidate: dict[str, Any]) -> None:
        super().__init__(
            credential=OpenAICredential(api_key=SecretStr("offline-mock")),
            model="offline-mock",
            parameters=ChatModelBase.Parameters(),
            stream=False,
            max_retries=0,
        )
        self.formatter = OpenAIChatFormatter()
        self.candidate = candidate
        self.calls = 0

    async def _call_api(
        self,
        model_name: str,
        messages: list[Any],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any = None,
        **kwargs: Any,
    ) -> ChatResponse | AsyncGenerator[ChatResponse]:
        del model_name, messages, tool_choice, kwargs
        self.calls += 1
        assert {item["function"]["name"] for item in tools or []} == {"locate_clause"}
        if self.calls == 1:
            return ChatResponse(
                content=[
                    ToolCallBlock(id="call-1", name="locate_clause", input='{"query":"payment"}')
                ],
                is_last=True,
            )
        return ChatResponse(
            content=[TextBlock(text=json.dumps(self.candidate, ensure_ascii=False))],
            is_last=True,
        )


def test_synthetic_parse_authorized_tool_model_candidate_and_revocation(tmp_path: Path) -> None:
    assert TEST_DATABASE_URL is not None and TEST_ADMIN_DATABASE_URL is not None
    pdf = fitz.open()
    pdf.new_page().insert_text((72, 72), "Payment after acceptance")
    source_bytes = pdf.tobytes()
    source_path = tmp_path / "synthetic.pdf"
    source_path.write_bytes(source_bytes)
    parsed = parse_document(source_bytes, PDF_MEDIA_TYPE)
    quote_block = parsed.blocks[0]
    assert parsed.quote(quote_block) == quote_block.text

    tenant_id, actor_id, legal_entity_id, document_id = uuid4(), uuid4(), uuid4(), uuid4()
    case_id, _ = JobStore(TEST_DATABASE_URL).create_case_and_run(tenant_id, "vertical fixture")
    with psycopg.connect(TEST_ADMIN_DATABASE_URL) as connection:
        connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
        connection.execute(
            "UPDATE audit_cases SET legal_entity_id = %s WHERE id = %s",
            (legal_entity_id, case_id),
        )
        connection.execute(
            "INSERT INTO case_members (tenant_id, case_id, actor_id, role) "
            "VALUES (%s, %s, %s, 'reviewer')",
            (tenant_id, case_id, actor_id),
        )
        connection.execute(
            """
            INSERT INTO document_versions
              (tenant_id, id, legal_entity_id, source_kind,
               source_sha256, has_restricted_amount)
            VALUES (%s, %s, %s, 'main', %s, false)
            """,
            (tenant_id, document_id, legal_entity_id, parsed.source_sha256),
        )
        connection.execute(
            """
            INSERT INTO contract_selections
              (tenant_id, case_id, document_version_id, relation, selected_by)
            VALUES (%s, %s, %s, 'main', %s)
            """,
            (tenant_id, case_id, document_id, actor_id),
        )
        connection.execute(
            """
            INSERT INTO document_grants
              (tenant_id, actor_id, document_version_id, purpose,
               allow_text, allow_amount, valid_until)
            VALUES (%s, %s, %s, 'contract_review', true, false,
                    now() + interval '1 hour')
            """,
            (tenant_id, actor_id, document_id),
        )

    client = SyntheticPageIndexClient(parsed.pages[0])
    adapter = PageIndexAdapter(client)
    index_scope = RetrievalScope(tenant_id, case_id, "contract_review", frozenset({document_id}))
    indexed = adapter.index_pdf(
        index_scope, tenant_id, case_id, document_id, parsed.source_sha256, source_path
    )
    with psycopg.connect(TEST_ADMIN_DATABASE_URL) as connection:
        connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
        connection.execute(
            """
            INSERT INTO document_indexes
              (tenant_id, document_version_id, source_sha256, pageindex_doc_id)
            VALUES (%s, %s, %s, %s)
            """,
            (tenant_id, document_id, parsed.source_sha256, indexed.pageindex_doc_id),
        )

    projector = AuthorizationProjector(TEST_DATABASE_URL)
    gateway = AuthorizedPageIndex(projector, adapter)
    agent_boundary = AgentCallBoundary(timeout_seconds=10, max_tool_calls=2)
    unbound_access = TaskAccess(tenant_id, case_id, actor_id, "contract_review")
    preview = InputSnapshotStore(TEST_DATABASE_URL).preview(unbound_access)
    frozen = InputSnapshotStore(TEST_DATABASE_URL).freeze(
        unbound_access, preview.id, preview.case_version, preview.manifest_sha256
    )
    assert frozen.document_version_ids == {document_id}
    access = TaskAccess(
        tenant_id, case_id, actor_id, "contract_review", input_snapshot_id=frozen.id
    )
    with pytest.raises(RetrievalDenied, match="input_snapshot_required"):
        gateway.page(unbound_access, document_id, 1)
    tool_calls: list[str] = []

    def locate_clause(query: str) -> ToolChunk:
        """Read one page of the currently authorized frozen contract."""
        agent_boundary.before_tool()
        tool_calls.append(query)
        page = gateway.page(access, document_id, 1)[0]
        return ToolChunk(content=[TextBlock(text=page["text"])])

    candidate = {
        "slot_id": "payment_clause_present",
        "state": "filled",
        "value": True,
        "evidence": {
            "document_version_id": str(document_id),
            "source_sha256": parsed.source_sha256,
            "page": quote_block.page,
            "start_offset": quote_block.start_offset,
            "end_offset": quote_block.end_offset,
            "quote": quote_block.text,
            "quote_sha256": sha256(quote_block.text.encode()).hexdigest(),
        },
    }
    model = CandidateModel(candidate)
    agent = Agent(
        name="payment-candidate",
        system_prompt="Only propose candidate clauses; the application verifies all evidence.",
        model=model,
        toolkit=Toolkit(tools=[FunctionTool(locate_clause, is_read_only=True)]),
        react_config=ReActConfig(max_iters=3),
    )
    response = asyncio.run(
        agent_boundary.run(agent, UserMsg(name="reviewer", content="Locate payment clause"))
    )
    assert tool_calls == ["payment"] and client.calls == ["submit", "page"]
    assert model.calls >= 2
    candidate_block = next(block for block in response.content if isinstance(block, TextBlock))
    proposed = json.loads(candidate_block.text)
    evidence = proposed["evidence"]
    dependencyless_source = RULE_PATH.read_bytes().replace(
        b"  checks:\n    - legal.price.definition", b"  checks: []", 1
    )
    rule = load_development_rule_bytes(dependencyless_source)
    assert not rule.dependent_checks
    run = RuleRun(
        rule,
        rule.plan(
            {
                "contract.party_role": "customer",
                "contract.contract_type": "service",
                "contract.jurisdiction": "CN-mainland",
            }
        ),
        {document_id: parsed},
        facts={
            "contract.party_role": "customer",
            "contract.contract_type": "service",
            "contract.jurisdiction": "CN-mainland",
        },
    )
    run.submit(
        SlotSubmission(
            slot_id=proposed["slot_id"],
            state=proposed["state"],
            value=proposed["value"],
            evidence=(
                RuleEvidence(
                    kind="document_span",
                    document_version_id=document_id,
                    source_sha256=evidence["source_sha256"],
                    page=evidence["page"],
                    start_offset=evidence["start_offset"],
                    end_offset=evidence["end_offset"],
                    quote=evidence["quote"],
                    quote_sha256=evidence["quote_sha256"],
                ),
            ),
        )
    )
    assert run.assess().status == "incomplete"  # One candidate is never a complete check.
    projector.require_result_sources(access, frozenset({document_id}))

    with psycopg.connect(TEST_ADMIN_DATABASE_URL) as connection:
        connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
        connection.execute(
            "UPDATE document_grants SET revoked_at = now() "
            "WHERE actor_id = %s AND document_version_id = %s",
            (actor_id, document_id),
        )
    with pytest.raises(RetrievalDenied):
        gateway.page(access, document_id, 1)
    with pytest.raises(RetrievalDenied):
        projector.require_result_sources(access, frozenset({document_id}))
    assert client.calls == ["submit", "page"]

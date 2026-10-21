"""Contract-driven research must use retrieved, verbatim evidence."""

import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from contract_app.retrieval.knowledge import digest, parse_source, search_sources
from contract_app.retrieval.legal_web import search_legal_web
from contract_app.workflow.execution_trace import ExecutionTraceRecorder
from contract_app.workflow.research_review import (
    capture_research,
    resolve_quote,
    run_research,
    validate_research,
)


class ResearchReviewTests(unittest.TestCase):
    def setUp(self):
        self.document_id = uuid4()
        self.contract = "合同交付期限为签订后十日。每日迟延交货违约金为合同总价的百分之十。"
        self.source = {
            "id": str(uuid4()),
            "kind": "legal",
            "title": "测试法规原文",
            "source_url": "https://www.gov.cn/test",
            "issuer": "测试机构",
            "scope": "测试",
            "version": 1,
            "content_sha256": "a" * 64,
            "effective_from": "2021-01-01",
            "effective_until": None,
            "segments": [
                {
                    "locator": "第1条",
                    "text": "约定的违约金过分高于造成的损失的，当事人可以请求人民法院适当减少。",
                }
            ],
        }
        self.snapshot = capture_research(
            {"sources": [self.source], "sources_sha256": digest([self.source])}
        )

    def run_fake(self, invalid=False, empty=False, overshoot=False):
        from agentscope.message import TextBlock

        holder = self

        class Tool:
            def __init__(self, function, **_kwargs):
                self.function = function

        class Toolkit:
            def __init__(self, tools):
                self.tools = tools

        class Agent:
            def __init__(self, toolkit, **_kwargs):
                self.toolkit = toolkit

            async def reply(self, _message):
                tool = self.toolkit.tools[0].function
                local = json.loads(
                    tool("legal_local", "交货违约金是否过高", ["违约金"]).content[0].text
                )
                tool("legal_web", "交货违约金 法律", ["违约金"])
                tool("company", "交货期限制度", ["交货"])
                if overshoot:
                    for _ in range(6):
                        tool("legal_local", "违约金", ["违约金"])
                finding = {
                    "domain": "legal",
                    "title": "违约金约定需复核",
                    "status": "risk_candidate",
                    "issue_type": "legality",
                    "unresolved_facts": [],
                    "severity": "medium",
                    "reasoning": "结合损失评估是否需要调整。",
                    "applicability": "需核实实际损失。",
                    "next_action": "法务核实损失。",
                    "contract_evidence": [
                        {
                            "document_version_id": str(holder.document_id),
                            "page": 1,
                            "quote": holder.contract,
                        }
                    ],
                    "source_evidence": [
                        {
                            "source_id": local["matches"][0]["source_id"],
                            "quote": "伪造的法条"
                            if invalid
                            else holder.source["segments"][0]["text"],
                        }
                    ],
                }
                return SimpleNamespace(
                    content=[
                        TextBlock(
                            text=json.dumps(
                                {
                                    "summary": "需要复核违约金。",
                                    "understood_contract": "合同约定交货期限及违约金。",
                                    "reviewed_topics": ["违约金"],
                                    "findings": [] if empty else [finding],
                                    "limitations": [],
                                },
                                ensure_ascii=False,
                            )
                        )
                    ]
                )

        with (
            patch("agentscope.agent.Agent", Agent),
            patch("agentscope.tool.FunctionTool", Tool),
            patch("agentscope.tool.Toolkit", Toolkit),
            patch.dict(os.environ, {"LEGAL_SEARCH_PROVIDER": "disabled"}),
        ):
            return run_research(
                model=None,
                snapshot=self.snapshot,
                documents={self.document_id: SimpleNamespace(pages=(self.contract,))},
                sources=(
                    SimpleNamespace(
                        document_version_id=self.document_id,
                        source_sha256="b" * 64,
                        relation="main",
                    ),
                ),
                access=None,
                gateway=SimpleNamespace(page=lambda *_: []),
                trace=ExecutionTraceRecorder(),
                cancellation=None,
            )

    def test_verbatim_citations_and_missing_company_material(self):
        from contract_app.api_runs import CheckResultResponse

        result = self.run_fake()
        CheckResultResponse.model_validate({**result, "task_result_sha256": "f" * 64})
        self.assertEqual(result["evidence"][0]["quote"], self.contract)
        self.assertEqual(len(result["research_review"]["searches"]), 3)
        self.assertTrue(
            any(
                item["domain"] == "company" and item["status"] == "needs_material"
                for item in result["research_review"]["findings"]
            )
        )

    def test_fabricated_source_citation_is_rejected(self):
        result = self.run_fake(invalid=True)
        finding = result["research_review"]["findings"][0]
        self.assertEqual(finding["status"], "undetermined")
        self.assertEqual(finding["source_evidence"], [])
        self.assertNotIn("伪造的法条", json.dumps(result, ensure_ascii=False))

    def test_whitespace_resolution_preserves_actual_source_span(self):
        self.assertEqual(
            resolve_quote("甲方应当\n支付　价款。", "甲方应当支付价款。"), "甲方应当\n支付　价款。"
        )
        self.assertIsNone(resolve_quote("甲方应当支付价款。", "甲方可以支付价款。"))

    def test_empty_model_findings_cannot_mean_pass(self):
        result = self.run_fake(empty=True)
        self.assertEqual(
            [item["status"] for item in result["research_review"]["findings"]],
            ["needs_material", "needs_material"],
        )

    def test_snapshot_tampering_is_rejected(self):
        self.snapshot["sources"][0]["title"] = "changed"
        with self.assertRaisesRegex(ValueError, "research_snapshot_integrity_invalid"):
            validate_research(self.snapshot)

    def test_search_limit_preserves_evidence_and_marks_unexecuted_query(self):
        result = self.run_fake(overshoot=True)
        review = result["research_review"]
        self.assertTrue(review["findings"][0]["source_evidence"])
        self.assertFalse(review["searches"][-1]["executed"])
        self.assertTrue(any("检索上限" in item for item in review["limitations"]))

    def test_document_search_has_no_check_catalog(self):
        hits = search_sources([self.source], "legal", "迟延交货的违约金", ["违约金"])
        self.assertEqual(hits[0]["locator"], "第1条")
        self.assertFalse(search_sources([self.source], "company", "违约金", ["违约金"]))

    def test_blank_upload_and_unconfigured_online_search(self):
        with self.assertRaisesRegex(ValueError, "knowledge_text_missing"):
            parse_source(b"  \n", "empty.txt")
        with patch.dict(os.environ, {"LEGAL_SEARCH_PROVIDER": "disabled"}):
            hits, warning = search_legal_web("交付违约金")
            self.assertEqual(hits, [])
            self.assertIn("未配置", warning)

    def test_article_paragraphs_keep_their_law_locator(self):
        _, segments, _ = parse_source(
            "第五百八十五条 第一款。\n第二款。\n第五百八十六条 另一条。".encode(), "law.txt"
        )
        self.assertEqual(segments[0]["locator"], "第五百八十五条")
        self.assertEqual(segments[0]["text"], "第五百八十五条 第一款。\n第二款。")

    def test_new_workflow_does_not_execute_the_legacy_rule_catalog(self):
        from contract_app.workflow.contract_review import ContractReviewProcessor

        processor = ContractReviewProcessor.__new__(ContractReviewProcessor)
        processor.model_profile = None
        processor.model = None
        processor.tenant_id = uuid4()
        processor.job_store = SimpleNamespace()
        processor.gateway = None
        processor._load_context = lambda *_: SimpleNamespace(
            definitions=None,
            research=self.snapshot,
            sources=(SimpleNamespace(warnings=()),),
            confirmed_facts={},
            access=None,
        )
        processor._load_parsed_documents = lambda *_: {}
        with (
            patch(
                "contract_app.workflow.research_review.run_research",
                return_value={"research_review": {}},
            ),
            patch(
                "contract_app.workflow.contract_review.load_development_rule_bytes",
                side_effect=AssertionError("legacy rule executed"),
            ),
        ):
            result = processor({}, None, SimpleNamespace(cancelled=None))
            self.assertIn("research_review", result["results"][0])

    def test_start_api_freezes_full_sources_without_a_rule_catalog(self):
        from datetime import UTC, datetime
        from contract_app.api_runs import StartRunRequest, start_run
        from contract_app.auth_security import Principal

        case_id, snapshot_id, run_id = uuid4(), uuid4(), uuid4()
        captured = []
        jobs = SimpleNamespace(start_bound_run=lambda *args, **kwargs: captured.append(args[5]) or run_id,
            get_run=lambda *_: SimpleNamespace(id=run_id, case_id=case_id,
                input_snapshot_id=snapshot_id, status="queued", version=1,
                created_at=datetime.now(UTC), task_type="contract_review"))
        with patch.dict(os.environ, {"APP_ENV": "test", "CONTRACT_REVIEW_EXECUTION_ENABLED": "true"}), \
             patch("contract_app.api_runs.capture_model_profile", return_value={"model": "test"}), \
             patch("contract_app.retrieval.knowledge.capture_sources", return_value={
                 "sources": [self.source], "sources_sha256": digest([self.source])}):
            response = start_run(case_id, StartRunRequest(input_snapshot_id=snapshot_id),
                Principal(uuid4(), uuid4(), permissions=frozenset({"company_rules.use"})),
                jobs, "fixture-key", "unused-fixture-database")
        self.assertEqual(response.run_id, run_id)
        self.assertEqual(set(captured[0]), {"research"})
        self.assertEqual(captured[0]["research"]["sources"][0]["segments"], self.source["segments"])


if __name__ == "__main__":
    unittest.main()

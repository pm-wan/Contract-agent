"""Optional live-model smoke check using synthetic contract and source data only."""

import json
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

from contract_app.api_review_samples import SOURCE_SAMPLES
from contract_app.retrieval.knowledge import digest, parse_source
from contract_app.workflow.contract_review import _configured_model
from contract_app.workflow.execution_trace import ExecutionTraceRecorder
from contract_app.workflow.research_review import capture_research, run_research


def main():
    law_path = Path(__import__("contract_app").__file__).parent / "sample_data" / "civil_code.txt"
    sources = []
    for sample in SOURCE_SAMPLES:
        raw = law_path.read_bytes() if sample["key"] == "civil-code" else sample["content"].encode()
        _, segments, warnings = parse_source(raw, sample["key"] + ".txt")
        sources.append(
            {
                **sample,
                "id": str(uuid4()),
                "version": 1,
                "segments": segments,
                "warnings": warnings,
                "content_sha256": digest(segments),
                "effective_until": sample.get("effective_until"),
            }
        )
    document_id = uuid4()
    text = """销售合同。卖方及我方为演示公司，买方为演示采购公司。签订日2026年10月20日，
适用中华人民共和国法律。出售100套设备，总价人民币200万元。于签约后十日内交货，
买方签收后五日内检验并书面通知质量问题。买方于验收后90日付清款项。
成交价格为公司标准价格优惠20%。本次没有取得内部折扣、账期和大额合同审批。
卖方逾期交货的，每逾期一日按合同总价10%支付违约金。"""
    gateway = SimpleNamespace(
        page=lambda access, identifier, page: (
            [{"text": text}]
            if identifier == document_id and page == 1
            else (_ for _ in ()).throw(ValueError("denied"))
        )
    )
    result = run_research(
        model=_configured_model(),
        snapshot=capture_research({"sources": sources, "sources_sha256": digest(sources)}),
        documents={document_id: SimpleNamespace(pages=(text,))},
        sources=(
            SimpleNamespace(
                document_version_id=document_id, relation="main", source_sha256="b" * 64
            ),
        ),
        access=None,
        gateway=gateway,
        trace=ExecutionTraceRecorder(),
        cancellation=Event(),
    )
    review = result["research_review"]
    assert any(
        item["domain"] == "company" and item["source_evidence"] for item in review["findings"]
    )
    assert any(item["domain"] == "legal" and item["source_evidence"] for item in review["findings"])
    print(
        json.dumps(
            {
                "live_model": True,
                "findings": [
                    {"domain": item["domain"], "title": item["title"], "status": item["status"]}
                    for item in review["findings"]
                ],
                "searches": [
                    {
                        "channel": item["channel"],
                        "query": item["query"],
                        "hits": len(item["source_ids"]),
                    }
                    for item in review["searches"]
                ],
                "validated_contract_quotes": len(result["evidence"]),
                "limitations": review["limitations"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()

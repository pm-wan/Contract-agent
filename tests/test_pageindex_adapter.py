import json
import sys
from hashlib import sha256
from importlib.metadata import version
from pathlib import Path
from types import ModuleType
from typing import Any
from uuid import uuid4

import pytest

from contract_app.retrieval.pageindex_adapter import (
    IndexedDocument,
    PageIndexAdapter,
    RetrievalDenied,
    RetrievalScope,
    local_pageindex_adapter,
)


class FakePageIndexClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def submit_document(self, file_path: str, wait: bool = False) -> dict[str, Any]:
        self.calls.append(("submit", file_path))
        assert wait
        return {"doc_id": "pi-frozen-1"}

    def get_tree(
        self, doc_id: str, node_summary: bool = False, include_text: bool = True
    ) -> dict[str, Any]:
        self.calls.append(("tree", doc_id))
        assert not include_text
        return {"doc_id": doc_id, "status": "completed"}

    def get_page_content(self, doc_id: str, pages: str) -> list[dict[str, Any]]:
        self.calls.append(("page", f"{doc_id}:{pages}"))
        return [{"page": int(pages), "text": "合同条款"}]


def test_only_frozen_document_can_be_indexed_and_read(tmp_path: Path) -> None:
    tenant, case, document_id = uuid4(), uuid4(), uuid4()
    source = tmp_path / "contract.pdf"
    source.write_bytes(b"%PDF-1.7\nsynthetic")
    digest = sha256(source.read_bytes()).hexdigest()
    scope = RetrievalScope(tenant, case, "contract_review", frozenset({document_id}))
    fake = FakePageIndexClient()
    adapter = PageIndexAdapter(fake)
    document = adapter.index_pdf(scope, tenant, case, document_id, digest, source)
    assert adapter.tree(scope, document)["doc_id"] == "pi-frozen-1"
    assert adapter.page(scope, document, 1)[0]["text"] == "合同条款"
    assert len(fake.calls) == 3

    other_scope = RetrievalScope(tenant, case, "contract_review", frozenset())
    with pytest.raises(RetrievalDenied):
        adapter.tree(other_scope, document)
    with pytest.raises(RetrievalDenied):
        adapter.page(other_scope, document, 1)
    with pytest.raises(RetrievalDenied):
        adapter.index_pdf(other_scope, tenant, case, document_id, digest, source)
    assert len(fake.calls) == 3


def test_wrong_tenant_and_hash_rejected_before_sdk(tmp_path: Path) -> None:
    tenant, case, document_id = uuid4(), uuid4(), uuid4()
    source = tmp_path / "contract.pdf"
    source.write_bytes(b"%PDF-1.7\nsynthetic")
    scope = RetrievalScope(tenant, case, "contract_review", frozenset({document_id}))
    fake = FakePageIndexClient()
    adapter = PageIndexAdapter(fake)
    with pytest.raises(RetrievalDenied):
        adapter.index_pdf(
            scope, uuid4(), case, document_id, sha256(source.read_bytes()).hexdigest(), source
        )
    with pytest.raises(ValueError, match="indexed_source_hash_mismatch"):
        adapter.index_pdf(scope, tenant, case, document_id, "0" * 64, source)
    wrong_case = IndexedDocument(tenant, uuid4(), document_id, "0" * 64, "pi-other")
    with pytest.raises(RetrievalDenied):
        adapter.tree(scope, wrong_case)
    assert fake.calls == []


def test_pageindex_configuration_is_canonical_and_unversioned_adapters_are_explicit() -> None:
    configured = {"provider": "pageindex", "model": "fixture-model", "sdk_version": "1.2.3"}
    adapter = PageIndexAdapter(FakePageIndexClient(), configuration=configured)
    expected = sha256(
        json.dumps(configured, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()
    assert adapter.configuration_snapshot == {
        "configuration": configured,
        "configuration_sha256": expected,
    }

    unversioned = PageIndexAdapter(FakePageIndexClient()).configuration_snapshot
    assert unversioned["configuration"]["configuration_status"] == "unversioned"


def test_local_pageindex_uses_pypdf_for_the_legacy_reader_namespace(tmp_path: Path) -> None:
    import pymupdf
    import pypdf

    adapter = local_pageindex_adapter(tmp_path / "pageindex", "fixture-model")
    import pageindex.utils

    assert pageindex.utils.PyPDF2 is pypdf
    configuration = adapter.configuration_snapshot["configuration"]
    assert configuration["pdf_reader"] == "pypdf"
    assert configuration["pdf_reader_version"] == version("pypdf")

    pdf = pymupdf.open()
    pdf.new_page().insert_text((72, 72), "PageIndex reader compatibility")
    source = tmp_path / "reader.pdf"
    source.write_bytes(pdf.tobytes())
    pdf.close()
    from pageindex.local_api import LocalAPI

    assert LocalAPI._extract_page_texts(str(source)) == ["PageIndex reader compatibility"]


def test_local_pageindex_fails_closed_if_legacy_reader_was_already_imported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "PyPDF2", ModuleType("PyPDF2"))

    with pytest.raises(RuntimeError, match="unsafe_pdf_reader_loaded_before_pageindex"):
        local_pageindex_adapter(tmp_path / "pageindex", "fixture-model")

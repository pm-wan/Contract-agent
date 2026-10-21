"""Scope checked PageIndex access for frozen document versions."""

import json
import sys
from dataclasses import dataclass
from hashlib import sha256
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID


class PageIndexClientProtocol(Protocol):
    def submit_document(self, file_path: str, *, wait: bool = False) -> dict[str, Any]: ...

    def get_tree(
        self, doc_id: str, node_summary: bool = False, include_text: bool = True
    ) -> dict[str, Any]: ...

    def get_page_content(self, doc_id: str, pages: str) -> list[dict[str, Any]]: ...


@dataclass(frozen=True, slots=True)
class RetrievalScope:
    tenant_id: UUID
    case_id: UUID
    purpose: str
    allowed_document_version_ids: frozenset[UUID]


@dataclass(frozen=True, slots=True)
class IndexedDocument:
    tenant_id: UUID
    case_id: UUID
    document_version_id: UUID
    source_sha256: str
    pageindex_doc_id: str


class RetrievalDenied(PermissionError):
    """The requested version is outside the frozen authorized scope."""


class PageIndexAdapter:
    def __init__(
        self,
        client: PageIndexClientProtocol,
        *,
        configuration: dict[str, Any] | None = None,
    ) -> None:
        self._client = client
        self._configuration = configuration or {
            "provider": "custom",
            "configuration_status": "unversioned",
        }
        try:
            serialized = json.dumps(
                self._configuration, sort_keys=True, ensure_ascii=False, separators=(",", ":")
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("pageindex_configuration_not_json") from exc
        self._configuration_sha256 = sha256(serialized.encode("utf-8")).hexdigest()

    @property
    def configuration_snapshot(self) -> dict[str, Any]:
        """Return a copy of the configuration identity used to build an index."""
        return {
            "configuration": json.loads(json.dumps(self._configuration, ensure_ascii=False)),
            "configuration_sha256": self._configuration_sha256,
        }

    @staticmethod
    def _authorize(scope: RetrievalScope, document: IndexedDocument) -> None:
        if (
            scope.purpose not in {"contract_review", "upstream_comparison"}
            or scope.tenant_id != document.tenant_id
            or scope.case_id != document.case_id
            or document.document_version_id not in scope.allowed_document_version_ids
        ):
            raise RetrievalDenied("document_version_not_authorized")

    def index_pdf(
        self,
        scope: RetrievalScope,
        tenant_id: UUID,
        case_id: UUID,
        document_version_id: UUID,
        source_sha256: str,
        path: Path,
        *,
        index_sha256: str | None = None,
    ) -> IndexedDocument:
        candidate = IndexedDocument(
            tenant_id, case_id, document_version_id, source_sha256, "pending"
        )
        self._authorize(scope, candidate)
        if path.suffix.lower() != ".pdf" or not path.is_file():
            raise ValueError("indexed_source_must_be_pdf")
        data = path.read_bytes()
        expected_index_sha256 = index_sha256 or source_sha256
        if not data.startswith(b"%PDF-") or sha256(data).hexdigest() != expected_index_sha256:
            raise ValueError("indexed_source_hash_mismatch")
        response = self._client.submit_document(str(path), wait=True)
        pageindex_doc_id = response.get("doc_id")
        if not isinstance(pageindex_doc_id, str) or not pageindex_doc_id:
            raise ValueError("pageindex_document_id_missing")
        return IndexedDocument(
            tenant_id, case_id, document_version_id, source_sha256, pageindex_doc_id
        )

    def tree(self, scope: RetrievalScope, document: IndexedDocument) -> dict[str, Any]:
        self._authorize(scope, document)
        return self._client.get_tree(document.pageindex_doc_id, include_text=False)

    def page(
        self, scope: RetrievalScope, document: IndexedDocument, page_number: int
    ) -> list[dict[str, Any]]:
        self._authorize(scope, document)
        if page_number <= 0:
            raise ValueError("page_number_must_be_positive")
        return self._client.get_page_content(document.pageindex_doc_id, str(page_number))


def local_pageindex_adapter(storage_path: Path, index_model: str) -> PageIndexAdapter:
    """Construct the actual local SDK only when the model profile is configured."""
    import pypdf
    from pypdf import generic as pypdf_generic

    # PageIndex imports the retired PyPDF2 namespace and PyPDF2.generic.
    # Its current dependency is vulnerable to a malformed-PDF infinite loop,
    # so bind both legacy imports to pypdf before importing any PageIndex module.
    existing_legacy_reader = sys.modules.get("PyPDF2")
    if existing_legacy_reader is not None and existing_legacy_reader is not pypdf:
        raise RuntimeError("unsafe_pdf_reader_loaded_before_pageindex")
    existing_legacy_generic = sys.modules.get("PyPDF2.generic")
    if existing_legacy_generic is not None and existing_legacy_generic is not pypdf_generic:
        raise RuntimeError("unsafe_pdf_reader_loaded_before_pageindex")
    sys.modules["PyPDF2"] = pypdf
    sys.modules["PyPDF2.generic"] = pypdf_generic
    from pageindex import PageIndexClient

    try:
        sdk_version = version("pageindex")
    except PackageNotFoundError:
        sdk_version = "unknown"
    client = PageIndexClient(index={"model": index_model, "storage_path": str(storage_path)})
    return PageIndexAdapter(
        client,
        configuration={
            "provider": "pageindex",
            "client": "PageIndexClient",
            "sdk_version": sdk_version,
            "model": index_model,
            "storage_mode": "local",
            "pdf_reader": "pypdf",
            "pdf_reader_version": version("pypdf"),
            "configuration_schema_version": "1",
        },
    )

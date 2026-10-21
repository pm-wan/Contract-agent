"""Fail if PageIndex's vulnerable legacy reader distribution is installed."""

from __future__ import annotations

import tempfile
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path

from contract_app.retrieval.pageindex_adapter import local_pageindex_adapter


def main() -> int:
    try:
        distribution("PyPDF2")
    except PackageNotFoundError:
        pass
    else:
        raise SystemExit("unsafe_pdf_reader_distribution_installed")

    with tempfile.TemporaryDirectory(prefix="pageindex-reader-check-") as temporary:
        local_pageindex_adapter(Path(temporary) / "pageindex", "ci-placeholder")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

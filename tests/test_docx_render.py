from hashlib import sha256
from pathlib import Path

import pytest

from contract_app.documents import docx_render
from contract_app.documents.docx_render import DocxRenderError, render_docx_to_pdf


def _fake_libreoffice(path: Path, action: str) -> Path:
    path.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then echo "LibreOffice 25.2.3.2"; exit 0; fi\n'
        f"{action}\n",
        encoding="utf-8",
    )
    path.chmod(0o700)
    return path


def test_renderer_binds_versioned_environment_and_source_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_libreoffice(
        tmp_path / "libreoffice",
        'test "$SAL_DISABLE_OPENCL" = "1" || exit 31\n'
        'while [ "$#" -gt 0 ]; do if [ "$1" = "--outdir" ]; then shift; out="$1"; fi; shift; done\n'
        "printf '%s' '%PDF-1.4' > \"$out/source.pdf\"\nprintf '%0100d' 0 >> \"$out/source.pdf\"",
    )
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("DOCX_RENDERER_EXPECTED_VERSION", "25.2.3")
    monkeypatch.setenv("DOCX_RENDERER_ENVIRONMENT_ID", "deb13-noto-cjk-r1")

    result = render_docx_to_pdf(b"validated-docx-source")

    assert result.pdf_bytes.startswith(b"%PDF-")
    assert result.source_sha256 == sha256(b"validated-docx-source").hexdigest()
    assert result.renderer_identity == (
        "libreoffice:25.2.3.2:writer_pdf_Export:opencl-disabled:deb13-noto-cjk-r1"
    )


def test_renderer_rejects_an_unversioned_environment_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_libreoffice(tmp_path / "libreoffice", "exit 1")
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.delenv("DOCX_RENDERER_EXPECTED_VERSION", raising=False)
    monkeypatch.setenv("DOCX_RENDERER_ENVIRONMENT_ID", "not a stable id")

    with pytest.raises(DocxRenderError, match="docx_renderer_environment_invalid"):
        render_docx_to_pdf(b"validated-docx-source")


def test_renderer_rejects_oversized_output_before_reading_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_libreoffice(
        tmp_path / "libreoffice",
        'while [ "$#" -gt 0 ]; do if [ "$1" = "--outdir" ]; then shift; out="$1"; fi; shift; done\n'
        "printf '%s' '%PDF-1.4' > \"$out/source.pdf\"\nprintf '%0100d' 0 >> \"$out/source.pdf\"",
    )
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("DOCX_RENDERER_ENVIRONMENT_ID", "test-renderer-r1")
    monkeypatch.delenv("DOCX_RENDERER_EXPECTED_VERSION", raising=False)
    monkeypatch.setattr(docx_render, "MAX_RENDERED_PDF_BYTES", 107)
    original_read_bytes = Path.read_bytes

    def reject_output_read(path: Path) -> bytes:
        if path.name == "source.pdf":
            raise AssertionError("oversized renderer output must not be read")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", reject_output_read)

    with pytest.raises(DocxRenderError, match="docx_renderer_output_invalid"):
        render_docx_to_pdf(b"validated-docx-source")


def test_renderer_timeout_kills_the_converter_process_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_libreoffice(tmp_path / "libreoffice", "/bin/sleep 10")
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("DOCX_RENDERER_ENVIRONMENT_ID", "test-renderer-r1")
    monkeypatch.delenv("DOCX_RENDERER_EXPECTED_VERSION", raising=False)
    monkeypatch.setattr(docx_render, "RENDER_TIMEOUT_SECONDS", 0.05)

    with pytest.raises(DocxRenderError, match="docx_renderer_timeout"):
        render_docx_to_pdf(b"validated-docx-source")

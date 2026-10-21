import io
import os
import subprocess
from pathlib import Path
from typing import Any

import fitz
import pytest

from contract_app.documents import ocr as ocr_module
from contract_app.documents.ocr import OcrError, OcrWord, TesseractCliOcr
from contract_app.documents.parser import PDF_MEDIA_TYPE, ParseError, parse_document


class FakeOcr:
    version = "fake-ocr-v1"

    def __init__(self, words: tuple[OcrWord, ...]) -> None:
        self.words = words
        self.calls = 0

    def recognize_png(self, data: bytes) -> tuple[OcrWord, ...]:
        assert data.startswith(b"\x89PNG\r\n\x1a\n")
        self.calls += 1
        return self.words


def _image_pdf(*, with_native_text: bool = False) -> bytes:
    pdf = fitz.open()
    page = pdf.new_page()
    pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 100, 40), False)
    pixmap.clear_with(255)
    page.insert_image(fitz.Rect(40, 90, 240, 170), pixmap=pixmap)
    if with_native_text:
        page.insert_text((72, 72), "Native text")
    return pdf.tobytes()


def _write_fake_tesseract(tmp_path: Path, name: str, action: str) -> tuple[Path, Path]:
    executable = tmp_path / name
    tessdata = tmp_path / "tessdata"
    tessdata.mkdir(exist_ok=True)
    (tessdata / "chi_sim.traineddata").write_bytes(b"synthetic-chi-sim-model")
    (tessdata / "eng.traineddata").write_bytes(b"synthetic-eng-model")
    language_header = f'List of available languages in "{tessdata}" (2):'
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "import time\n"
        "if '--version' in sys.argv:\n"
        "    print('tesseract 5.4.1')\n"
        "    raise SystemExit(0)\n"
        "if '--list-langs' in sys.argv:\n"
        f"    print({language_header!r})\n"
        "    print('chi_sim')\n"
        "    print('eng')\n"
        "    raise SystemExit(0)\n"
        f"{action}\n",
        encoding="utf-8",
    )
    executable.chmod(0o700)
    return executable, tessdata


def test_scanned_page_ocr_offsets_bbox_and_confidence_are_traceable() -> None:
    ocr = FakeOcr(
        (
            OcrWord("Payment", 100, 100, 70, 20, 96.0, (1, 1, 1)),
            OcrWord("due", 180, 100, 35, 20, 91.0, (1, 1, 1)),
            OcrWord("after", 100, 140, 50, 20, 79.0, (1, 1, 2)),
        )
    )
    parsed = parse_document(_image_pdf(), PDF_MEDIA_TYPE, ocr=ocr)
    assert parsed.pages == ("Payment due\nafter",)
    assert [parsed.quote(block) for block in parsed.blocks] == ["Payment due", "after"]
    assert [block.kind for block in parsed.blocks] == ["ocr", "ocr"]
    assert parsed.blocks[0].bbox is not None
    assert parsed.blocks[0].bbox[0] == pytest.approx(50, abs=1)
    assert parsed.blocks[0].confidence == 0.91
    assert parsed.blocks[1].confidence == 0.79
    assert parsed.warnings == (
        "page_1_ocr_requires_review",
        "page_1_ocr_low_confidence",
    )
    assert parsed.parser_version.endswith("fake-ocr-v1")
    assert ocr.calls == 1


def test_mixed_page_images_are_disclosed_not_silently_treated_as_complete() -> None:
    ocr = FakeOcr(())
    parsed = parse_document(_image_pdf(with_native_text=True), PDF_MEDIA_TYPE, ocr=ocr)
    assert "Native text" in parsed.pages[0]
    assert parsed.warnings == ("page_1_has_images_unverified",)
    assert ocr.calls == 0


def test_empty_or_out_of_bounds_ocr_remains_incomplete() -> None:
    empty = parse_document(_image_pdf(), PDF_MEDIA_TYPE, ocr=FakeOcr(()))
    assert empty.pages == ("",)
    assert "page_1_ocr_no_text" in empty.warnings
    invalid = FakeOcr((OcrWord("bad", 10_000, 0, 20, 20, 99, (1, 1, 1)),))
    with pytest.raises(ParseError, match="ocr_coordinates_invalid"):
        parse_document(_image_pdf(), PDF_MEDIA_TYPE, ocr=invalid)


def test_tesseract_tsv_adapter_uses_stdin_and_rejects_bad_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    png = b"\x89PNG\r\n\x1a\nsynthetic-test-bytes"
    header = b"level\tblock_num\tpar_num\tline_num\tleft\ttop\twidth\theight\tconf\ttext\n"
    row = b"5\t1\t1\t1\t10\t20\t30\t12\t91.5\tPayment\n"

    executable = tmp_path / "fake-tesseract"
    executable.write_bytes(b"synthetic executable")
    tessdata = tmp_path / "tessdata"
    tessdata.mkdir()
    (tessdata / "chi_sim.traineddata").write_bytes(b"chi-sim-model")
    (tessdata / "eng.traineddata").write_bytes(b"eng-model")
    monkeypatch.setattr(ocr_module.shutil, "which", lambda _name: str(executable))

    class FakeProcess:
        def __init__(self, output: bytes, returncode: int = 0) -> None:
            self.stdin: io.BytesIO | None = None
            self.stdout = io.BytesIO(output)
            self.returncode = returncode

        def wait(self, timeout: float | None = None) -> int:
            assert timeout == 45
            return self.returncode

        def kill(self) -> None:
            self.returncode = -9

    def fake_popen(argv: list[str], **kwargs: Any) -> FakeProcess:
        assert argv[0] == str(executable)
        assert kwargs == {
            "stdin": subprocess.PIPE
            if "--version" not in argv and "--list-langs" not in argv
            else subprocess.DEVNULL,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.DEVNULL,
            "bufsize": 0,
        }

        if argv[1:] == ["--version"]:
            return FakeProcess(b"tesseract 5.4.1\n leptonica-1.84.1\n")
        if argv[1:] == ["--list-langs"]:
            return FakeProcess(
                f'List of available languages in "{tessdata}" (2):\nchi_sim\neng\n'.encode()
            )
        assert argv[1:] == ["stdin", "stdout", "-l", "chi_sim+eng", "tsv"]
        process = FakeProcess(header + row)
        process.stdin = io.BytesIO()
        return process

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    adapter = TesseractCliOcr()
    words = adapter.recognize_png(png)
    assert words == (OcrWord("Payment", 10, 20, 30, 12, 91.5, (1, 1, 1)),)
    assert adapter.version.startswith("tesseract-cli-tsv-v2:5.4.1:")
    assert len(adapter.version) <= 128

    def bad_popen(argv: list[str], **_kwargs: Any) -> FakeProcess:
        process = FakeProcess(b"not tsv")
        process.stdin = io.BytesIO()
        return process

    monkeypatch.setattr(subprocess, "Popen", bad_popen)
    with pytest.raises(OcrError, match="ocr_output_invalid"):
        adapter.recognize_png(png)


@pytest.mark.skipif(os.name == "nt", reason="executable script test requires POSIX")
def test_tesseract_identity_tracks_traineddata_content(tmp_path: Path) -> None:
    executable, tessdata = _write_fake_tesseract(
        tmp_path,
        "identity-tesseract",
        "sys.stdin.buffer.read()\n"
        "sys.stdout.buffer.write(b'level\\tblock_num\\tpar_num\\tline_num\\tleft\\ttop\\twidth\\theight\\tconf\\ttext\\n')",
    )
    png = b"\x89PNG\r\n\x1a\nsynthetic-test-bytes"
    adapter = TesseractCliOcr(executable=str(executable), timeout_seconds=5)
    adapter.recognize_png(png)
    first_version = adapter.version

    (tessdata / "chi_sim.traineddata").write_bytes(b"changed-chi-sim-model")
    with pytest.raises(OcrError, match="ocr_failed"):
        adapter.recognize_png(png)

    replacement = TesseractCliOcr(executable=str(executable), timeout_seconds=5)
    replacement.recognize_png(png)
    assert replacement.version != first_version


@pytest.mark.skipif(os.name == "nt", reason="executable script test requires POSIX")
def test_tesseract_output_limit_is_enforced_while_reading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ocr_module, "MAX_OCR_TSV_BYTES", 64)
    executable, _tessdata = _write_fake_tesseract(
        tmp_path,
        "fake-tesseract",
        "sys.stdin.buffer.read()\n"
        "chunk = b'x' * 4096\n"
        "while True:\n"
        "    sys.stdout.buffer.write(chunk)\n"
        "    sys.stdout.buffer.flush()",
    )

    with pytest.raises(OcrError, match="ocr_output_too_large"):
        TesseractCliOcr(executable=str(executable), timeout_seconds=5).recognize_png(
            b"\x89PNG\r\n\x1a\nsynthetic-test-bytes"
        )


@pytest.mark.skipif(os.name == "nt", reason="executable script test requires POSIX")
def test_tesseract_timeout_terminates_stalled_process(tmp_path: Path) -> None:
    executable, _tessdata = _write_fake_tesseract(
        tmp_path,
        "stalled-tesseract",
        "sys.stdin.buffer.read()\ntime.sleep(10)",
    )

    with pytest.raises(OcrError, match="ocr_timeout"):
        TesseractCliOcr(executable=str(executable), timeout_seconds=2).recognize_png(
            b"\x89PNG\r\n\x1a\nsynthetic-test-bytes"
        )


def test_ocr_unavailable_fails_closed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    executable = tmp_path / "missing-at-launch"
    executable.write_bytes(b"synthetic executable")
    tessdata = tmp_path / "tessdata"
    tessdata.mkdir()
    (tessdata / "chi_sim.traineddata").write_bytes(b"chi-sim-model")
    (tessdata / "eng.traineddata").write_bytes(b"eng-model")
    monkeypatch.setattr(ocr_module.shutil, "which", lambda _name: str(executable))

    def missing(_argv: list[str], **_kwargs: Any) -> Any:
        raise FileNotFoundError("tesseract")

    monkeypatch.setattr(subprocess, "Popen", missing)
    with pytest.raises(ParseError, match="ocr_unavailable"):
        parse_document(_image_pdf(), PDF_MEDIA_TYPE, ocr=TesseractCliOcr())

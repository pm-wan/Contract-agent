from hashlib import sha256
from io import BytesIO

import fitz
import pytest
from docx import Document

from contract_app.documents.docx_render import RenderedDocx
from contract_app.documents.parser import (
    DOCX_MEDIA_TYPE,
    PDF_MEDIA_TYPE,
    ParseError,
    parse_document,
)


def _rendered_docx(data: bytes, pages: tuple[str, ...]) -> RenderedDocx:
    pdf = fitz.open()
    for text in pages:
        page = pdf.new_page()
        page.insert_textbox(fitz.Rect(48, 48, 550, 790), text, fontsize=12)
    return RenderedDocx(pdf.tobytes(), "test-renderer:1", sha256(data).hexdigest())


def test_pdf_block_round_trips_to_frozen_page() -> None:
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "Payment after acceptance")
    data = pdf.tobytes()
    parsed = parse_document(data, PDF_MEDIA_TYPE)
    assert len(parsed.pages) == 1
    assert len(parsed.blocks) == 1
    assert parsed.quote(parsed.blocks[0]) == parsed.blocks[0].text
    assert parsed.blocks[0].bbox is not None


def test_scan_without_text_layer_is_explicitly_flagged() -> None:
    pdf = fitz.open()
    pdf.new_page()
    parsed = parse_document(pdf.tobytes(), PDF_MEDIA_TYPE)
    assert parsed.blocks == ()
    assert parsed.warnings == ("page_1_has_no_text_layer",)


def test_docx_paragraph_and_table_offsets() -> None:
    document = Document()
    document.add_paragraph("付款条件")
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "阶段"
    table.cell(0, 1).text = "金额"
    stream = BytesIO()
    document.save(stream)
    data = stream.getvalue()
    rendered = _rendered_docx(data, ("付款条件", "阶段    金额"))
    parsed = parse_document(
        data,
        DOCX_MEDIA_TYPE,
        rendered_docx=rendered,
    )
    assert all(parsed.quote(block) == block.text for block in parsed.blocks)
    assert len(parsed.pages) == 2
    assert parsed.index_source_sha256 == sha256(rendered.pdf_bytes).hexdigest()
    assert "docx_pages_use_canonical_pdf_rendering" in parsed.warnings


def test_docx_nested_table_text_is_not_duplicated_as_an_outer_row() -> None:
    document = Document()
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).add_table(rows=1, cols=1).cell(0, 0).text = "Nested payment terms"
    table.cell(0, 1).text = "Outer amount"
    stream = BytesIO()
    document.save(stream)

    data = stream.getvalue()
    parsed = parse_document(
        data,
        DOCX_MEDIA_TYPE,
        rendered_docx=_rendered_docx(data, ("Nested payment terms    Outer amount",)),
    )

    assert len(parsed.blocks) == 1
    assert parsed.blocks[0].kind == "paragraph"
    assert parsed.pages[0].count("Nested payment terms") == 1
    assert parsed.pages[0].count("Outer amount") == 1


def test_docx_preserves_paragraph_tabs_and_manual_line_breaks() -> None:
    document = Document()
    paragraph = document.add_paragraph()
    paragraph.add_run("Payment terms")
    paragraph.add_run().add_break()
    paragraph.add_run("Payment after acceptance")
    paragraph.add_run().add_tab()
    paragraph.add_run("Signature confirmation")
    stream = BytesIO()
    document.save(stream)

    data = stream.getvalue()
    parsed = parse_document(
        data,
        DOCX_MEDIA_TYPE,
        rendered_docx=_rendered_docx(
            data, ("Payment terms\nPayment after acceptance    Signature confirmation",)
        ),
    )

    assert "Payment terms" in parsed.pages[0]
    assert "Payment after acceptance" in parsed.pages[0]
    assert parsed.parser_version == "m1-pymupdf-docx-render-2"
    assert parsed.renderer_identity == "test-renderer:1"
    assert parsed.index_source_sha256 is not None


def test_docx_header_and_footer_content_are_reported_as_unreviewed() -> None:
    document = Document()
    document.add_paragraph("Contract body")
    document.sections[0].header.paragraphs[0].text = "Party A: Example Company"
    document.sections[0].footer.paragraphs[0].text = "Internal version information"
    stream = BytesIO()
    document.save(stream)

    data = stream.getvalue()
    parsed = parse_document(
        data,
        DOCX_MEDIA_TYPE,
        rendered_docx=_rendered_docx(
            data, ("Party A: Example Company\nContract body\nInternal version information",)
        ),
    )

    assert "Contract body" in parsed.pages[0]
    assert "Party A: Example Company" in parsed.pages[0]
    assert "Internal version information" in parsed.pages[0]


def test_signature_and_size_checks() -> None:
    with pytest.raises(ParseError, match="pdf_signature_invalid"):
        parse_document(b"not a pdf", PDF_MEDIA_TYPE)
    with pytest.raises(ParseError, match="document_size_invalid"):
        parse_document(b"", PDF_MEDIA_TYPE)

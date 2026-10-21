from hashlib import sha256

import pytest

from contract_app.documents.artifacts import parsed_document_sha256
from contract_app.documents.models import ParsedDocument, TextBlock


def test_parsed_document_and_text_blocks_copy_mutable_nested_inputs() -> None:
    source_hash = sha256(b"immutable source").hexdigest()
    pages = ["待核验的合同文本。"]
    bbox = [1.0, 2.0, 3.0, 4.0]
    warnings = ["page_1_has_images_unverified"]
    block = TextBlock(
        page=1,
        text=pages[0],
        start_offset=0,
        end_offset=len(pages[0]),
        bbox=bbox,
    )
    blocks = [block]
    parsed = ParsedDocument(
        source_sha256=source_hash,
        media_type="application/pdf",
        parser_version="test-parser-v1",
        pages=pages,
        blocks=blocks,
        warnings=warnings,
        index_source_sha256=source_hash,
    )
    identity_before_mutation = parsed_document_sha256(parsed)

    pages[0] = "调用方后来改写的文本。"
    bbox[0] = 99.0
    blocks.clear()
    warnings.clear()

    assert parsed.pages == ("待核验的合同文本。",)
    assert parsed.blocks == (block,)
    assert parsed.warnings == ("page_1_has_images_unverified",)
    assert parsed.blocks[0].bbox == (1.0, 2.0, 3.0, 4.0)
    assert parsed_document_sha256(parsed) == identity_before_mutation
    with pytest.raises(TypeError):
        parsed.pages[0] = "不能改写冻结解析产物。"  # type: ignore[index]
    with pytest.raises(TypeError):
        parsed.blocks[0].bbox[0] = 5.0  # type: ignore[index]


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("pages", "not a page sequence", "parsed_document_pages_invalid"),
        ("pages", ["valid", object()], "parsed_document_pages_invalid"),
        ("blocks", [object()], "parsed_document_blocks_invalid"),
        ("warnings", ["valid", object()], "parsed_document_warnings_invalid"),
    ],
)
def test_parsed_document_rejects_invalid_sequence_shapes(
    field: str, value: object, error: str
) -> None:
    source_hash = sha256(b"immutable source").hexdigest()
    fields: dict[str, object] = {
        "source_sha256": source_hash,
        "media_type": "application/pdf",
        "parser_version": "test-parser-v1",
        "pages": ("text",),
        "blocks": (),
        "warnings": (),
        "index_source_sha256": source_hash,
    }
    fields[field] = value

    with pytest.raises(ValueError, match=error):
        ParsedDocument(**fields)  # type: ignore[arg-type]


def test_text_block_rejects_invalid_or_nonfinite_bbox() -> None:
    with pytest.raises(ValueError, match="text_block_bbox_invalid"):
        TextBlock(1, "text", 0, 4, [0, 1, float("nan"), 3])  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("page", True),
        ("text", 42),
        ("start_offset", True),
        ("end_offset", False),
        ("kind", ["paragraph"]),
    ],
)
def test_text_block_rejects_invalid_identity_fields(field: str, value: object) -> None:
    fields: dict[str, object] = {
        "page": 1,
        "text": "text",
        "start_offset": 0,
        "end_offset": 4,
        "bbox": None,
        "kind": "paragraph",
    }
    fields[field] = value

    with pytest.raises(ValueError, match="text_block_identity_invalid"):
        TextBlock(**fields)  # type: ignore[arg-type]


def test_parsed_document_rejects_invalid_identity_field_types() -> None:
    with pytest.raises(ValueError, match="parsed_document_identity_invalid"):
        ParsedDocument(
            source_sha256=object(),  # type: ignore[arg-type]
            media_type="application/pdf",
            parser_version="test-parser-v1",
            pages=(),
            blocks=(),
            warnings=(),
        )

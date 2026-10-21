"""Framework independent parsed document representation."""

import math
from dataclasses import dataclass
from typing import Literal

PDF_MEDIA_TYPE = "application/pdf"
DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
NON_BLOCKING_PARSER_WARNINGS = frozenset({"docx_pages_use_canonical_pdf_rendering"})


def has_material_parser_warnings(warnings: tuple[str, ...] | list[str]) -> bool:
    """Return whether a parser warning signals incomplete or uncertain coverage."""
    return any(warning not in NON_BLOCKING_PARSER_WARNINGS for warning in warnings)


@dataclass(frozen=True, slots=True)
class TextBlock:
    page: int
    text: str
    start_offset: int
    end_offset: int
    bbox: tuple[float, float, float, float] | None
    kind: Literal["paragraph", "table", "ocr"] = "paragraph"
    confidence: float | None = None

    def __post_init__(self) -> None:
        if (
            isinstance(self.page, bool)
            or not isinstance(self.page, int)
            or not isinstance(self.text, str)
            or isinstance(self.start_offset, bool)
            or not isinstance(self.start_offset, int)
            or isinstance(self.end_offset, bool)
            or not isinstance(self.end_offset, int)
            or not isinstance(self.kind, str)
            or self.kind not in {"paragraph", "table", "ocr"}
        ):
            raise ValueError("text_block_identity_invalid")
        if self.bbox is not None:
            if not isinstance(self.bbox, (tuple, list)) or len(self.bbox) != 4:
                raise ValueError("text_block_bbox_invalid")
            if any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or (isinstance(value, float) and not math.isfinite(value))
                for value in self.bbox
            ):
                raise ValueError("text_block_bbox_invalid")
            object.__setattr__(self, "bbox", tuple(self.bbox))
        if self.confidence is not None and (
            isinstance(self.confidence, bool)
            or not isinstance(self.confidence, (int, float))
            or (isinstance(self.confidence, float) and not math.isfinite(self.confidence))
        ):
            raise ValueError("text_block_confidence_invalid")


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    source_sha256: str
    media_type: str
    parser_version: str
    pages: tuple[str, ...]
    blocks: tuple[TextBlock, ...]
    warnings: tuple[str, ...]
    index_source_sha256: str | None = None
    renderer_identity: str | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.source_sha256, str)
            or not isinstance(self.media_type, str)
            or not isinstance(self.parser_version, str)
            or (
                self.index_source_sha256 is not None
                and not isinstance(self.index_source_sha256, str)
            )
            or (self.renderer_identity is not None and not isinstance(self.renderer_identity, str))
        ):
            raise ValueError("parsed_document_identity_invalid")
        if not isinstance(self.pages, (tuple, list)) or any(
            not isinstance(page, str) for page in self.pages
        ):
            raise ValueError("parsed_document_pages_invalid")
        if not isinstance(self.blocks, (tuple, list)) or any(
            not isinstance(block, TextBlock) for block in self.blocks
        ):
            raise ValueError("parsed_document_blocks_invalid")
        if not isinstance(self.warnings, (tuple, list)) or any(
            not isinstance(warning, str) for warning in self.warnings
        ):
            raise ValueError("parsed_document_warnings_invalid")
        object.__setattr__(self, "pages", tuple(self.pages))
        object.__setattr__(self, "blocks", tuple(self.blocks))
        object.__setattr__(self, "warnings", tuple(self.warnings))

    def quote(self, block: TextBlock) -> str:
        """Resolve a block against the frozen normalized page text."""
        return self.pages[block.page - 1][block.start_offset : block.end_offset]

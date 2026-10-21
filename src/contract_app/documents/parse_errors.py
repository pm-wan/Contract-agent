"""Closed vocabulary for safe document-parse failure reporting."""

from typing import Literal, cast, get_args

ParseErrorCode = Literal[
    "document_size_invalid",
    "pdf_signature_invalid",
    "docx_signature_invalid",
    "docx_structure_invalid",
    "docx_expanded_size_invalid",
    "docx_macro_forbidden",
    "docx_archive_invalid",
    "media_type_unsupported",
    "docx_render_source_hash_mismatch",
    "pdf_encrypted",
    "pdf_parse_failed",
    "pdf_has_no_pages",
    "ocr_page_too_large",
    "ocr_coordinates_invalid",
    "ocr_image_invalid",
    "ocr_unavailable",
    "ocr_timeout",
    "ocr_failed",
    "ocr_output_too_large",
    "ocr_output_invalid",
    "docx_parse_failed",
    "docx_renderer_unavailable",
    "docx_renderer_version_unavailable",
    "docx_renderer_version_unexpected",
    "docx_renderer_environment_invalid",
    "docx_renderer_timeout",
    "docx_renderer_failed",
    "docx_renderer_output_missing",
    "docx_renderer_output_invalid",
    "parse_storage_mismatch",
    "parse_processing_error",
]

_PARSE_ERROR_CODES = frozenset(get_args(ParseErrorCode))
PARSE_ERROR_CODE_VALUES = tuple(get_args(ParseErrorCode))


def public_parse_error_code(value: object) -> ParseErrorCode | None:
    """Return a known machine code, never an arbitrary worker exception string."""
    if isinstance(value, str) and value in _PARSE_ERROR_CODES:
        return cast(ParseErrorCode, value)
    return None


def normalize_parse_error_code(value: object) -> ParseErrorCode:
    """Map unknown internal failures to the generic safe parse code."""
    return public_parse_error_code(value) or "parse_processing_error"

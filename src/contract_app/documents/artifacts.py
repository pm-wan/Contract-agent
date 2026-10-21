"""Canonical serialization and identity for parsed-document artifacts."""

import hashlib
import json
from dataclasses import asdict

from contract_app.documents.models import ParsedDocument


def serialize_parsed_document(document: ParsedDocument) -> bytes:
    """Serialize exactly as the parse worker persists immutable parse output."""
    return json.dumps(
        asdict(document), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def parsed_document_sha256(document: ParsedDocument) -> str:
    """Return the content identity recorded for the serialized parse artifact."""
    return hashlib.sha256(serialize_parsed_document(document)).hexdigest()

"""Hash development knowledge files without implying source authenticity or approval."""

from __future__ import annotations

import json
import stat
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from contract_app.bounded_files import read_bounded_file

MAX_KNOWLEDGE_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_KNOWLEDGE_SOURCE_BYTES = 16 * 1024 * 1024
KnowledgeKind = Literal["normative", "enterprise_position"]


class KnowledgeManifestInvalid(ValueError):
    """A development knowledge manifest is invalid or escapes its source directory."""


@dataclass(frozen=True, slots=True)
class KnowledgeContentBinding:
    reference: str
    kind: KnowledgeKind
    content_sha256: str


@dataclass(frozen=True, slots=True)
class DevelopmentKnowledgeManifest:
    source_sha256: str
    bindings: tuple[KnowledgeContentBinding, ...]


def load_development_knowledge_manifest(
    path: Path,
    *,
    normative_refs: tuple[str, ...],
    enterprise_position_refs: tuple[str, ...],
) -> DevelopmentKnowledgeManifest:
    """Validate a local manifest and hash referenced files without returning their content."""
    manifest_bytes = _read_bounded(path, MAX_KNOWLEDGE_MANIFEST_BYTES)
    manifest = _load_manifest_json(manifest_bytes)
    if manifest.keys() != {"manifest_version", "sources"}:
        raise KnowledgeManifestInvalid("knowledge_manifest_shape_invalid")
    if manifest.get("manifest_version") != "development-knowledge-manifest.v1":
        raise KnowledgeManifestInvalid("knowledge_manifest_version_invalid")
    sources = manifest.get("sources")
    if not isinstance(sources, list) or not sources:
        raise KnowledgeManifestInvalid("knowledge_manifest_sources_invalid")
    if set(normative_refs).intersection(enterprise_position_refs):
        raise KnowledgeManifestInvalid("knowledge_manifest_reference_ambiguous")

    expected_kinds = {
        **{reference: "normative" for reference in normative_refs},
        **{reference: "enterprise_position" for reference in enterprise_position_refs},
    }
    seen: set[str] = set()
    bindings: list[KnowledgeContentBinding] = []
    manifest_root = path.parent.resolve(strict=True)
    for source in sources:
        if (
            not isinstance(source, dict)
            or source.keys() != {"reference", "kind", "path"}
            or not _nonempty(source.get("reference"))
            or not _nonempty(source.get("path"))
        ):
            raise KnowledgeManifestInvalid("knowledge_manifest_source_invalid")
        reference = source["reference"]
        kind = source["kind"]
        expected_kind = expected_kinds.get(reference)
        if (
            reference in seen
            or expected_kind is None
            or kind != expected_kind
            or kind not in {"normative", "enterprise_position"}
        ):
            raise KnowledgeManifestInvalid("knowledge_manifest_reference_invalid")
        relative_path = _safe_relative_path(source["path"])
        source_path = _resolve_local_source(path.parent, relative_path, manifest_root)
        source_bytes = _read_bounded(source_path, MAX_KNOWLEDGE_SOURCE_BYTES)
        if not source_bytes:
            raise KnowledgeManifestInvalid("knowledge_manifest_source_empty")
        bindings.append(
            KnowledgeContentBinding(
                reference=reference,
                kind=kind,
                content_sha256=sha256(source_bytes).hexdigest(),
            )
        )
        seen.add(reference)

    bindings.sort(key=lambda binding: (binding.kind, binding.reference))
    return DevelopmentKnowledgeManifest(sha256(manifest_bytes).hexdigest(), tuple(bindings))


def _safe_relative_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if (
        not value
        or not path.parts
        or "\x00" in value
        or "\\" in value
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise KnowledgeManifestInvalid("knowledge_manifest_path_invalid")
    return path


def _resolve_local_source(root: Path, relative_path: PurePosixPath, resolved_root: Path) -> Path:
    candidate = root
    for part in relative_path.parts:
        candidate = candidate / part
        try:
            metadata = candidate.lstat()
        except OSError as exc:
            raise KnowledgeManifestInvalid("knowledge_manifest_source_unavailable") from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise KnowledgeManifestInvalid("knowledge_manifest_symlink_forbidden")
    if not stat.S_ISREG(metadata.st_mode):
        raise KnowledgeManifestInvalid("knowledge_manifest_source_invalid")
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(resolved_root)
    except (OSError, ValueError) as exc:
        raise KnowledgeManifestInvalid("knowledge_manifest_path_invalid") from exc
    return resolved


def _read_bounded(path: Path, maximum: int) -> bytes:
    try:
        content = read_bounded_file(path, maximum)
    except OSError as exc:
        raise KnowledgeManifestInvalid("knowledge_manifest_file_unavailable") from exc
    if len(content) > maximum:
        raise KnowledgeManifestInvalid("knowledge_manifest_file_too_large")
    return content


def _load_manifest_json(content: bytes) -> dict[str, Any]:
    try:
        value = json.loads(
            content.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_nonstandard_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise KnowledgeManifestInvalid("knowledge_manifest_json_invalid") from exc
    if not isinstance(value, dict):
        raise KnowledgeManifestInvalid("knowledge_manifest_shape_invalid")
    return value


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _reject_nonstandard_constant(value: str) -> Any:
    raise ValueError(f"nonstandard_json_constant:{value}")


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())

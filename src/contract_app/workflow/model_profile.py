"""Non-secret identity for a contract-review model execution profile."""

from __future__ import annotations

import json
import os
import tomllib
from hashlib import sha256
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit


class ModelProfileInvalid(ValueError):
    """Model profile is missing or contains unsafe endpoint metadata."""


def _locked_agentscope_version() -> str:
    lock_path = Path(__file__).resolve().parents[3] / "uv.lock"
    try:
        lock = tomllib.loads(lock_path.read_text(encoding="utf-8"))
        matches = [
            package["version"] for package in lock["package"] if package.get("name") == "agentscope"
        ]
    except (OSError, KeyError, TypeError, tomllib.TOMLDecodeError) as exc:
        raise ModelProfileInvalid("review_model_sdk_lock_unavailable") from exc
    if len(matches) != 1 or not isinstance(matches[0], str):
        raise ModelProfileInvalid("review_model_sdk_lock_ambiguous")
    return matches[0]


def capture_model_profile() -> dict[str, Any]:
    """Capture model identity/configuration, never credentials, from the environment."""
    model = os.environ.get("CONTRACT_REVIEW_MODEL", "").strip()
    if not model:
        raise ModelProfileInvalid("review_model_unconfigured")
    base_url = os.environ.get("CONTRACT_REVIEW_MODEL_BASE_URL")
    if base_url:
        parts = urlsplit(base_url)
        if (
            not parts.scheme
            or not parts.hostname
            or parts.username
            or parts.password
            or parts.query
            or parts.fragment
        ):
            raise ModelProfileInvalid("review_model_endpoint_invalid")
        endpoint = urlunsplit(
            (parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), "", "")
        )
    else:
        endpoint = "https://api.openai.com/v1"
    endpoint_digest = sha256(endpoint.encode("utf-8")).hexdigest()
    profile = {
        "provider": "openai-compatible",
        "model": model,
        "endpoint_sha256": endpoint_digest,
        "sdk": "agentscope",
        "sdk_version": _locked_agentscope_version(),
        "parameters": {
            "stream": False,
            "max_retries": 0,
            "retry_delay": 1.0,
            "context_size": 128000,
            "generation": {
                "max_tokens": None,
                "thinking_enable": False,
                "reasoning_effort": None,
                "temperature": None,
                "top_p": None,
                "parallel_tool_calls": True,
                "voice": None,
            },
        },
    }
    return profile


def model_profile_digest(profile: dict[str, Any]) -> str:
    canonical = json.dumps(profile, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256(canonical.encode("utf-8")).hexdigest()

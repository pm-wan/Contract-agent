"""Model-selected web research through the configured provider's official tools."""

import json
import os
from datetime import UTC, datetime
from hashlib import sha256
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

ALLOWED_DOMAINS = ("gov.cn", "npc.gov.cn", "court.gov.cn")


def legal_search_status() -> dict:
    provider = os.environ.get("LEGAL_SEARCH_PROVIDER", "auto")
    host = urlsplit(os.environ.get("CONTRACT_REVIEW_MODEL_BASE_URL", "")).hostname
    if provider == "auto":
        provider = (
            "deepseek"
            if host == "api.deepseek.com"
            else ("tavily" if os.environ.get("LEGAL_SEARCH_API_KEY") else "unconfigured")
        )
    configured = (provider == "deepseek" and host == "api.deepseek.com") or (
        provider == "tavily" and bool(os.environ.get("LEGAL_SEARCH_API_KEY"))
    )
    return {
        "provider": provider,
        "configured": bool(configured),
        "allowed_domains": list(ALLOWED_DOMAINS),
    }


def _post(url: str, api_key: str, data: dict) -> dict:
    request = Request(
        url,
        method="POST",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        data=json.dumps(data).encode(),
    )
    with urlopen(request, timeout=20) as response:
        body = response.read(2_000_001)
    if len(body) > 2_000_000:
        raise ValueError("search_response_too_large")
    return json.loads(body)


def search_legal_web(
    query: str, keywords: list[str] | None = None
) -> tuple[list[dict], str | None]:
    status = legal_search_status()
    if not status["configured"]:
        return [], "联网法律检索未配置或已停用；本次没有完成联网查证。"
    provider = status["provider"]
    if provider == "deepseek":
        api_key = os.environ.get("CONTRACT_REVIEW_MODEL_API_KEY", "").strip()
        if not api_key:
            return [], "当前模型供应商的联网检索凭据不可用。"
    else:
        api_key = os.environ.get("LEGAL_SEARCH_API_KEY", "").strip()
    try:
        if provider == "deepseek":
            # DeepSeek's official search shares the existing model credential.
            payload = _post(
                "https://api.deepseek.com/v1/tools/search",
                api_key,
                {"query": query[:116] + " site:gov.cn", "count": 5, "need_content": True},
            )
        else:
            payload = _post(
                "https://api.tavily.com/search",
                api_key,
                {
                    "query": query,
                    "include_domains": list(ALLOWED_DOMAINS),
                    "include_answer": False,
                    "include_raw_content": "text",
                    "max_results": 5,
                    "search_depth": "advanced",
                },
            )
        results = []
        fetches = 0
        for item in payload.get("results", []):
            url = item.get("url", "")
            parts = urlsplit(url)
            host = (parts.hostname or "").lower()
            if (
                parts.scheme not in {"http", "https"}
                or parts.username
                or parts.password
                or not any(
                    host == domain or host.endswith("." + domain) for domain in ALLOWED_DOMAINS
                )
            ):
                continue
            # Generated summaries and result snippets never become source evidence.
            raw = item.get("content" if provider == "deepseek" else "raw_content")
            if provider == "deepseek" and not raw and fetches < 2:
                fetches += 1
                try:
                    raw = _post(
                        "https://api.deepseek.com/v1/tools/fetch", api_key, {"url": url}
                    ).get("content")
                except Exception:
                    continue
            if not isinstance(raw, str) or not raw.strip():
                continue
            content_hash = sha256(raw.encode()).hexdigest()
            windows = [
                (sum(raw[start : start + 2400].count(term) for term in (keywords or [])), start)
                for start in range(0, min(len(raw), 500000), 2000)
            ]
            windows.sort(key=lambda pair: (-pair[0], pair[1]))
            for _, start in windows[:2]:
                text = raw[start : start + 2400]
                results.append(
                    {
                        "source_id": f"web:{content_hash[:24]}:{start}",
                        "kind": "legal",
                        "origin": "web",
                        "provider": provider,
                        "title": str(item.get("title", host))[:255],
                        "url": url,
                        "issuer": host,
                        "locator": f"网页原文 · 字符 {start}",
                        "text": text,
                        "content_sha256": content_hash,
                        "retrieved_at": datetime.now(UTC).isoformat(),
                        "validity": "有效性和适用时间需结合网页正文核实",
                    }
                )
        return results, None if results else "联网查询已执行，但没有取得可核验的权威来源原文。"
    except HTTPError as error:
        return [], f"模型供应商联网检索返回 HTTP {error.code}，本次没有完成联网查证。"
    except Exception:
        return [], "联网法律检索失败；本次结果不能视为已完成联网查证。"

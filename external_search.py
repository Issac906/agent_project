"""External web search fallback for the patent workflow."""

from __future__ import annotations

from dataclasses import dataclass
from html import unescape
import json
import os
import re
from typing import Any
from urllib.parse import quote_plus

import requests

SEARCH_TIMEOUT_SECONDS = 3
ANYSEARCH_TIMEOUT_SECONDS = int(os.getenv("ANYSEARCH_TIMEOUT", "10") or "10")
PATENT_RELEVANCE_TERMS = (
    "专利",
    "权利要求",
    "公开号",
    "公开",
    "申请",
    "patent",
    "claim",
)
PATENT_PUBLICATION_PATTERN = re.compile(r"\b(?:cn|wo|us|ep)\s?\d{4,}[a-z]?\b", re.IGNORECASE)


@dataclass
class ExternalSearchResult:
    enabled: bool
    notes: list[str]
    results: list[dict[str, str]]


def search_external_materials(
    task: str,
    enabled: bool = False,
    max_results: int = 5,
) -> ExternalSearchResult:
    if not enabled:
        return ExternalSearchResult(
            enabled=False,
            notes=[
                "外部资料搜索暂未启用。",
                "正式专利检索仍需在国家知识产权局专利检索及分析系统进行人工核对。",
            ],
            results=[],
        )

    queries = _query_variants(task)
    attempted: list[str] = []
    errors: list[str] = []
    results: list[dict[str, str]] = []
    searchers = _configured_searchers()
    for query in queries:
        for provider, searcher in searchers:
            attempted.append(f"{provider}: {query}")
            try:
                batch = searcher(query, max_results=max_results)
            except Exception as exc:  # noqa: BLE001 - search providers fail independently.
                errors.append(f"{provider} / {query}: {type(exc).__name__}: {_safe_error_message(exc)}")
                continue
            if not batch:
                errors.append(f"{provider} / {query}: 0 results")
                continue
            results = _merge_results(results, batch)
            if len(results) >= max_results:
                results = results[:max_results]
                break
        if len(results) >= max_results:
            break

    if not results:
        notes = [
            f"外部搜索未找到结果，已尝试检索式：{'；'.join(attempted)}",
            f"失败明细：{'；'.join(errors[:8])}" if errors else "失败明细：无",
            "系统会继续自动变换关键词补充检索。",
        ]
    else:
        notes = [
            f"已执行外部搜索，检索式：{'；'.join(attempted)}",
            f"部分失败明细：{'；'.join(errors[:5])}" if errors else "所有已尝试搜索源均正常。",
            "这些结果只能作为快速摸底，不能替代正式专利库检索。",
            "正式专利检索仍需在国家知识产权局专利检索及分析系统进行人工核对。",
        ]

    return ExternalSearchResult(enabled=True, notes=notes, results=results)


def _configured_searchers() -> list[tuple[str, Any]]:
    provider = os.getenv("SEARCH_PROVIDER", "duckduckgo").strip().lower()
    api_key = _search_api_key()
    searchers: list[tuple[str, Any]] = []

    if provider in {"anysearch", "any_search"} or (
        api_key and os.getenv("ANYSEARCH_BASE_URL")
    ):
        searchers.append(("AnySearch", _search_anysearch_api))

    searchers.extend(
        [
            ("GooglePatents", _search_google_patents),
            ("Bing", _search_bing_html),
        ]
    )
    return searchers


def _search_anysearch_api(query: str, max_results: int) -> list[dict[str, str]]:
    api_key = _search_api_key()
    endpoint = _anysearch_endpoint()
    if not api_key:
        raise ValueError("SEARCH_API_KEY is required for AnySearch")
    if not endpoint:
        raise ValueError("ANYSEARCH_BASE_URL or SEARCH_BASE_URL is required for AnySearch")

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        "X-API-Key": api_key,
    }
    payload = {
        "query": query,
        "q": query,
        "limit": max_results,
        "max_results": max_results,
        "num_results": max_results,
    }
    method = os.getenv("ANYSEARCH_METHOD", "POST").strip().upper()
    if method == "GET":
        response = requests.get(
            endpoint,
            params={"query": query, "q": query, "limit": max_results},
            headers=headers,
            timeout=ANYSEARCH_TIMEOUT_SECONDS,
        )
    else:
        response = requests.post(
            endpoint,
            json=payload,
            headers=headers,
            timeout=ANYSEARCH_TIMEOUT_SECONDS,
        )
        if response.status_code in {404, 405}:
            response = requests.get(
                endpoint,
                params={"query": query, "q": query, "limit": max_results},
                headers=headers,
                timeout=ANYSEARCH_TIMEOUT_SECONDS,
            )
    response.raise_for_status()
    return _parse_anysearch_results(response.json(), max_results=max_results)


def _search_api_key() -> str | None:
    value = os.getenv("SEARCH_API_KEY")
    return value.strip() if value and value.strip() else None


def _safe_error_message(exc: Exception) -> str:
    message = str(exc).strip()
    if not message:
        return "无详细信息"
    api_key = _search_api_key()
    if api_key:
        message = message.replace(api_key, "***")
    return message[:240]


def _anysearch_endpoint() -> str | None:
    endpoint = (
        os.getenv("ANYSEARCH_ENDPOINT")
        or os.getenv("ANYSEARCH_BASE_URL")
        or os.getenv("SEARCH_BASE_URL")
    )
    if not endpoint or not endpoint.strip():
        return None
    endpoint = endpoint.strip().rstrip("/")
    if endpoint.endswith(("/search", "/api/search", "/v1/search")):
        return endpoint
    return f"{endpoint}/search"


def _search_google_patents(query: str, max_results: int) -> list[dict[str, str]]:
    url = f"https://patents.google.com/xhr/query?url=q%3D{quote_plus(query)}&exp="
    response = requests.get(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36"
            )
        },
        timeout=SEARCH_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return _filter_patent_relevant(
        _parse_google_patents_results(response.text, max_results=max_results),
        max_results=max_results,
    )


def _query_variants(task: str) -> list[str]:
    base = _clean_query(task)
    compact = _compact_query(base)
    variants = [
        f"{compact} 专利 权利要求 公开号",
        f"{compact} 专利 现有技术 技术方案",
        f"{compact} site:patents.google.com",
    ]
    deduped: list[str] = []
    seen: set[str] = set()
    for query in variants:
        query = " ".join(query.split())
        if query and query not in seen:
            seen.add(query)
            deduped.append(query)
    return deduped


def _clean_query(task: str) -> str:
    text = re.sub(r"[|｜]+", " ", str(task or ""))
    text = re.sub(r"[，,；;。]+", " ", text)
    return " ".join(text.split())


def _compact_query(task: str, max_terms: int = 10) -> str:
    terms = [term for term in _clean_query(task).split() if term]
    return " ".join(terms[:max_terms])


def _merge_results(
    existing: list[dict[str, str]],
    incoming: list[dict[str, str]],
) -> list[dict[str, str]]:
    seen = {item.get("url") or item.get("title") or str(item) for item in existing}
    merged = [*existing]
    for item in incoming:
        key = item.get("url") or item.get("title") or str(item)
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged


def _search_duckduckgo_html(query: str, max_results: int) -> list[dict[str, str]]:
    url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
    response = requests.get(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36"
            )
        },
        timeout=SEARCH_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return _filter_patent_relevant(
        _parse_duckduckgo_results(response.text, max_results=max_results * 2),
        max_results=max_results,
    )


def _search_bing_html(query: str, max_results: int) -> list[dict[str, str]]:
    url = f"https://www.bing.com/search?q={quote_plus(query)}"
    response = requests.get(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36"
            )
        },
        timeout=SEARCH_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return _filter_patent_relevant(
        _parse_bing_results(response.text, max_results=max_results * 2),
        max_results=max_results,
    )


def _parse_duckduckgo_results(html: str, max_results: int) -> list[dict[str, str]]:
    pattern = re.compile(
        r'<a[^>]+class="result__a"[^>]+href="(?P<url>[^"]+)"[^>]*>(?P<title>.*?)</a>',
        re.DOTALL,
    )
    snippet_pattern = re.compile(
        r'<a[^>]+class="result__snippet"[^>]*>(?P<snippet>.*?)</a>',
        re.DOTALL,
    )
    snippets = [_clean_html(match.group("snippet")) for match in snippet_pattern.finditer(html)]

    results = []
    for index, match in enumerate(pattern.finditer(html)):
        if len(results) >= max_results:
            break
        results.append(
            {
                "title": _clean_html(match.group("title")),
                "url": unescape(match.group("url")),
                "snippet": snippets[index] if index < len(snippets) else "",
            }
        )

    return results


def _parse_bing_results(html: str, max_results: int) -> list[dict[str, str]]:
    item_pattern = re.compile(
        r'<li[^>]+class="b_algo"[^>]*>(?P<body>.*?)</li>',
        re.DOTALL,
    )
    link_pattern = re.compile(
        r'<h2[^>]*>\s*<a[^>]+href="(?P<url>[^"]+)"[^>]*>(?P<title>.*?)</a>',
        re.DOTALL,
    )
    snippet_pattern = re.compile(r"<p[^>]*>(?P<snippet>.*?)</p>", re.DOTALL)
    results: list[dict[str, str]] = []
    for item in item_pattern.finditer(html):
        if len(results) >= max_results:
            break
        body = item.group("body")
        link = link_pattern.search(body)
        if not link:
            continue
        snippet = snippet_pattern.search(body)
        results.append(
            {
                "title": _clean_html(link.group("title")),
                "url": unescape(link.group("url")),
                "snippet": _clean_html(snippet.group("snippet")) if snippet else "",
            }
        )
    return results


def _parse_google_patents_results(payload: str, max_results: int) -> list[dict[str, str]]:
    data = json.loads(payload)
    clusters = ((data.get("results") or {}).get("cluster") or [])
    results: list[dict[str, str]] = []
    for cluster in clusters:
        for item in cluster.get("result") or []:
            if len(results) >= max_results:
                return results
            patent = item.get("patent") or {}
            title = _clean_html(patent.get("title") or "")
            snippet = _clean_html(patent.get("snippet") or "")
            publication = _clean_html(
                patent.get("publication_number")
                or patent.get("application_number")
                or item.get("id")
                or ""
            )
            patent_id = str(item.get("id") or "").lstrip("/")
            url = f"https://patents.google.com/{patent_id}" if patent_id else ""
            if not title and not publication:
                continue
            label = f"{publication} {title}".strip()
            results.append(
                {
                    "title": label,
                    "url": url,
                    "snippet": snippet,
                }
            )
    return results


def _parse_anysearch_results(payload: Any, max_results: int) -> list[dict[str, str]]:
    raw_items = _extract_search_items(payload)
    results: list[dict[str, str]] = []
    for item in raw_items:
        if len(results) >= max_results:
            break
        if not isinstance(item, dict):
            continue
        title = _first_text(
            item,
            "title",
            "name",
            "headline",
            "document_title",
            "page_title",
        )
        url = _first_text(
            item,
            "url",
            "link",
            "href",
            "source_url",
            "document_url",
            "web_url",
        )
        snippet = _first_text(
            item,
            "snippet",
            "summary",
            "description",
            "content",
            "text",
            "abstract",
        )
        if not title and not snippet:
            continue
        results.append(
            {
                "title": _clean_html(title or url or "AnySearch 检索结果"),
                "url": url,
                "snippet": _clean_html(snippet),
                "source": "AnySearch",
            }
        )
    return results


def _extract_search_items(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []

    candidates = [
        payload.get("results"),
        payload.get("items"),
        payload.get("data"),
        payload.get("documents"),
        payload.get("hits"),
        payload.get("organic_results"),
    ]
    web_pages = payload.get("web_pages") or payload.get("webPages")
    if isinstance(web_pages, dict):
        candidates.append(web_pages.get("value"))

    for candidate in candidates:
        if isinstance(candidate, list):
            return candidate
        if isinstance(candidate, dict):
            nested = _extract_search_items(candidate)
            if nested:
                return nested
    return []


def _first_text(item: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = item.get(key)
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, dict):
            nested = _first_text(value, "text", "value", "raw", "html")
            if nested:
                return nested
    return ""


def _clean_html(value: str) -> str:
    text = re.sub(r"<.*?>", "", value)
    text = unescape(text)
    return " ".join(text.split())


def _filter_patent_relevant(
    results: list[dict[str, str]],
    max_results: int,
) -> list[dict[str, str]]:
    filtered: list[dict[str, str]] = []
    for item in results:
        haystack = " ".join(
            str(item.get(key, "")) for key in ("title", "url", "snippet")
        ).lower()
        if any(term in haystack for term in PATENT_RELEVANCE_TERMS) or PATENT_PUBLICATION_PATTERN.search(haystack):
            filtered.append(item)
        if len(filtered) >= max_results:
            break
    return filtered

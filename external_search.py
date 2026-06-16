"""External web search fallback for the patent workflow."""

from __future__ import annotations

from dataclasses import dataclass
from html import unescape
import re
from typing import Any
from urllib.parse import quote_plus

import requests


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

    query = f"{task} 专利 现有技术 技术方案"
    try:
        results = _search_duckduckgo_html(query, max_results=max_results)
    except requests.RequestException as exc:
        return ExternalSearchResult(
            enabled=True,
            notes=[
                f"外部搜索失败：{type(exc).__name__}",
                "请检查本机网络，或后续接入 Tavily/SerpAPI 等稳定搜索 API。",
                "正式专利检索仍需在国家知识产权局专利检索及分析系统进行人工核对。",
            ],
            results=[],
        )

    if not results:
        notes = [
            f"外部搜索未找到结果，检索式：{query}",
            "建议尝试更换关键词，或使用国家知识产权局专利检索及分析系统人工检索。",
        ]
    else:
        notes = [
            f"已执行外部搜索，检索式：{query}",
            "这些结果只能作为快速摸底，不能替代正式专利库检索。",
            "正式专利检索仍需在国家知识产权局专利检索及分析系统进行人工核对。",
        ]

    return ExternalSearchResult(enabled=True, notes=notes, results=results)


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
        timeout=15,
    )
    response.raise_for_status()
    return _parse_duckduckgo_results(response.text, max_results=max_results)


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


def _clean_html(value: str) -> str:
    text = re.sub(r"<.*?>", "", value)
    text = unescape(text)
    return " ".join(text.split())

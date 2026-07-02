"""Build citation snapshots for generated patent documents."""

from __future__ import annotations

from typing import Any

from external_search import ExternalSearchResult


def build_citation_snapshot(
    documents: dict[str, Any] | None,
    external: ExternalSearchResult | None,
    search_topic: str = "",
) -> dict[str, Any]:
    """Create a structured citation snapshot for final output and history."""
    knowledge_rows = _flatten_documents(documents or {})
    knowledge_citations = []
    for index, row in enumerate(knowledge_rows, start=1):
        knowledge_citations.append(
            {
                "id": f"K{index}",
                "document": str(row.get("file_path") or row.get("filename") or "未知文档"),
                "document_id": _document_identifier(row),
                "status": str(row.get("status") or "未知"),
                "chunks_count": int(row.get("chunks_count") or 0),
                "quoted_content": _clean_summary(row.get("content_summary") or ""),
            }
        )

    external_citations = []
    for index, result in enumerate((external.results if external else []) or [], start=1):
        external_citations.append(
            {
                "id": f"W{index}",
                "title": str(result.get("title") or "外部检索结果"),
                "url": str(result.get("url") or ""),
                "quoted_content": _clean_summary(result.get("snippet") or ""),
            }
        )

    return {
        "search_topic": search_topic,
        "knowledge": knowledge_citations,
        "external": external_citations,
        "notes": external.notes if external else [],
    }


def append_citation_section(markdown: str, citations: dict[str, Any]) -> str:
    """Append a final in-document citation section."""
    base = str(markdown or "").rstrip()
    citation_markdown = format_citation_markdown(citations).strip()
    if not citation_markdown:
        return base + "\n"
    return f"{base}\n\n{citation_markdown}\n"


def format_citation_markdown(citations: dict[str, Any]) -> str:
    """Render citations as Markdown for the final page."""
    lines: list[str] = [
        "## 九、引用说明",
        "",
        "本节列出本次生成过程中直接参考的知识库材料和外部检索材料，用于说明正文内容来源与检索依据。",
        "",
        "### 9.1 知识库引用",
    ]
    knowledge = citations.get("knowledge") or []
    if knowledge:
        for item in knowledge:
            lines.extend(
                [
                    f"- [{item.get('id')}] {item.get('document')}",
                    f"  - 文档 ID：{item.get('document_id') or '待核实'}",
                    f"  - 处理状态：{item.get('status') or '未知'}；切片数量：{item.get('chunks_count') or 0}",
                    f"  - 引用内容：{item.get('quoted_content') or '该文档参与知识库检索，但接口未返回摘要内容。'}",
                ]
            )
    else:
        lines.append("- 未记录到可用的知识库引用。")

    lines.extend(["", "### 9.2 外部检索引用"])
    topic = citations.get("search_topic") or ""
    if topic:
        lines.extend([f"- 检索主题：{topic}", ""])
    external = citations.get("external") or []
    if external:
        for item in external:
            lines.extend(
                [
                    f"- [{item.get('id')}] {item.get('title')}",
                    f"  - 链接：{item.get('url') or '待核实'}",
                    f"  - 引用内容：{item.get('quoted_content') or '外部检索结果未返回摘要。'}",
                ]
            )
    else:
        lines.append("- 未记录到可用的外部检索引用。")

    notes = citations.get("notes") or []
    if notes:
        lines.extend(["", "### 9.3 检索说明"])
        for note in notes:
            lines.append(f"- {note}")

    return "\n".join(lines)


def _flatten_documents(documents: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    statuses = documents.get("statuses")
    if isinstance(statuses, dict):
        for status, items in statuses.items():
            if not isinstance(items, list):
                continue
            for item in items:
                if isinstance(item, dict):
                    row = dict(item)
                    row.setdefault("status", status)
                    rows.append(row)
    if rows:
        return rows

    for value in documents.values():
        if isinstance(value, list):
            rows.extend(item for item in value if isinstance(item, dict))
    if not rows and isinstance(documents.get("documents"), list):
        rows = [item for item in documents["documents"] if isinstance(item, dict)]
    return rows


def _document_identifier(row: dict[str, Any]) -> str:
    for key in ("id", "doc_id", "document_id", "file_id"):
        value = row.get(key)
        if value:
            return str(value)
    return str(row.get("file_path") or row.get("filename") or "")


def _clean_summary(value: str, limit: int = 260) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."

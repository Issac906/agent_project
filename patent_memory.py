"""Compact internal memory for avoiding repeated patent ideas."""

from __future__ import annotations

from datetime import datetime
import json
import re
from pathlib import Path
from typing import Any

from runtime_paths import data_path

DEFAULT_MEMORY_PATH = data_path("outputs", "patent_memory.json")
DEFAULT_HISTORY_DIR = data_path("outputs", "history")
MAX_MEMORY_ITEMS = 300
PROMPT_MEMORY_LIMIT = 40


def load_patent_memory(path: Path = DEFAULT_MEMORY_PATH) -> list[dict[str, str]]:
    if not path.exists():
        if path != DEFAULT_MEMORY_PATH:
            return []
        records = bootstrap_patent_memory_from_history()
        if records:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(records[:MAX_MEMORY_ITEMS], ensure_ascii=False, indent=2), encoding="utf-8")
        return records[:MAX_MEMORY_ITEMS]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    records: list[dict[str, str]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        title = _clean_field(item.get("title", ""))
        topic = _clean_field(item.get("topic", ""))
        idea = _clean_field(item.get("idea", ""))
        generated_at = _clean_field(item.get("generated_at", ""))
        if title or topic or idea:
            records.append(
                {
                    "generated_at": generated_at,
                    "title": title,
                    "topic": topic,
                    "idea": idea,
                }
            )
    return records[:MAX_MEMORY_ITEMS]


def bootstrap_patent_memory_from_history(history_dir: Path = DEFAULT_HISTORY_DIR) -> list[dict[str, str]]:
    if not history_dir.exists():
        return []
    records: list[dict[str, str]] = []
    seen: set[str] = set()
    for record_path in sorted(history_dir.glob("*/record.json"), reverse=True):
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(record, dict):
            continue
        selected = record.get("selected_candidate")
        selected_candidate = selected if isinstance(selected, dict) else {}
        item = {
            "generated_at": _clean_field(record.get("completed_at") or record.get("created_at") or ""),
            "title": _clean_field(record.get("title") or selected_candidate.get("title") or "", max_length=120),
            "topic": _clean_field(_history_topic(record), max_length=180),
            "idea": _clean_field(
                selected_candidate.get("raw")
                or selected_candidate.get("summary")
                or record.get("title")
                or "",
                max_length=500,
            ),
        }
        key = _memory_key(item)
        if key in seen or not (item["title"] or item["topic"] or item["idea"]):
            continue
        seen.add(key)
        records.append(item)
        if len(records) >= MAX_MEMORY_ITEMS:
            break
    return records


def append_patent_memory(
    *,
    title: str,
    topic: str,
    idea: str,
    generated_at: str | None = None,
    path: Path = DEFAULT_MEMORY_PATH,
) -> dict[str, Any]:
    record = {
        "generated_at": generated_at or datetime.now().isoformat(timespec="seconds"),
        "title": _clean_field(title, max_length=120),
        "topic": _clean_field(topic, max_length=180),
        "idea": _clean_field(idea, max_length=500),
    }
    if not (record["title"] or record["topic"] or record["idea"]):
        return {"saved": False, "reason": "empty_record", "count": len(load_patent_memory(path))}

    records = load_patent_memory(path)
    key = _memory_key(record)
    records = [item for item in records if _memory_key(item) != key]
    records.insert(0, record)
    records = records[:MAX_MEMORY_ITEMS]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"saved": True, "path": str(path), "count": len(records), "record": record}


def format_patent_memory_for_prompt(
    records: list[dict[str, str]],
    limit: int = PROMPT_MEMORY_LIMIT,
) -> str:
    if not records:
        return "暂无历史生成记忆。"
    lines = []
    for index, item in enumerate(records[:limit], start=1):
        generated_at = item.get("generated_at") or "未知时间"
        title = item.get("title") or "未命名"
        topic = item.get("topic") or "未记录主题"
        idea = item.get("idea") or "未记录 idea"
        lines.append(f"{index}. 时间：{generated_at}；标题：{title}；主题：{topic}；idea：{idea}")
    return "\n".join(lines)


def summarize_candidate_for_memory(candidate: Any, fallback_topic: str = "") -> dict[str, str]:
    title = _clean_field(getattr(candidate, "title", ""), max_length=120)
    raw = str(getattr(candidate, "raw", "") or getattr(candidate, "summary", "") or title)
    topic = _extract_field(raw, ["主题", "技术领域", "应用场景"]) or fallback_topic or title
    core = _extract_field(raw, ["核心方案", "创新点", "新技术特征"]) or raw
    return {
        "title": title,
        "topic": _clean_field(topic, max_length=180),
        "idea": _clean_field(core, max_length=500),
    }


def _extract_field(text: str, labels: list[str]) -> str:
    for label in labels:
        pattern = rf"{re.escape(label)}\s*[:：]\s*(.+?)(?=\n\S{{1,16}}\s*[:：]|\n\n|$)"
        match = re.search(pattern, text, flags=re.S)
        if match:
            return match.group(1)
    return ""


def _clean_field(value: Any, max_length: int = 300) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) > max_length:
        return text[: max_length - 1].rstrip() + "..."
    return text


def _history_topic(record: dict[str, Any]) -> str:
    selected_knowledge_base = record.get("selected_knowledge_base")
    if isinstance(selected_knowledge_base, dict):
        kb_name = selected_knowledge_base.get("name")
    else:
        kb_name = ""
    return str(record.get("search_topic") or kb_name or record.get("title") or "")


def _memory_key(item: dict[str, str]) -> str:
    return "|".join(
        re.sub(r"\W+", "", str(item.get(key, "")).lower())
        for key in ("title", "topic", "idea")
    )

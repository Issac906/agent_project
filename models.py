"""Data models shared by the patent agent workflow."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TaskPlan:
    """Result of skill-based task routing."""

    task_type: str
    title: str
    intent: str
    output_filename: str
    required_sections: list[str]
    suggested_queries: list[str]


@dataclass
class KnowledgeBundle:
    """Knowledge base retrieval results used by the writer."""

    query_results: list[dict[str, Any]] = field(default_factory=list)
    query_data_results: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def has_context(self) -> bool:
        return any(_result_has_context(item.get("data")) for item in self.query_results)


def _result_has_context(data: Any) -> bool:
    if data in (None, "", [], {}):
        return False
    if isinstance(data, dict):
        response = str(data.get("response", ""))
        references = data.get("references") or []
        if "[no-context]" in response or "no context" in response.lower():
            return False
        return bool(response.strip() or references)
    return True

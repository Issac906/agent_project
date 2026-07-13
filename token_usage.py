"""Token usage tracking for agent text generation calls."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from datetime import datetime
import math
import re
import time
from typing import Any


@dataclass
class TokenUsageEntry:
    step_name: str
    provider: str
    model: str
    started_at: str
    finished_at: str
    duration_seconds: float
    prompt_chars: int
    response_chars: int
    prompt_tokens_est: int
    completion_tokens_est: int
    total_tokens_est: int
    prompt_tokens_actual: int | None = None
    completion_tokens_actual: int | None = None
    total_tokens_actual: int | None = None
    actual_source: str | None = None
    actual_session_file: str | None = None
    actual_cost_total: float | None = None
    estimation_method: str = "mixed_cjk_ascii_estimate"


@dataclass
class TokenUsageTracker:
    run_id: str
    entries: list[TokenUsageEntry] = field(default_factory=list)

    def record(
        self,
        *,
        step_name: str,
        provider: str,
        model: str,
        prompt: str,
        response: str,
        started_at: str,
        finished_at: str,
        duration_seconds: float,
        actual: dict[str, Any] | None = None,
    ) -> None:
        prompt_tokens = estimate_tokens(prompt)
        completion_tokens = estimate_tokens(response)
        actual = actual or {}
        self.entries.append(
            TokenUsageEntry(
                step_name=step_name,
                provider=provider,
                model=model,
                started_at=started_at,
                finished_at=finished_at,
                duration_seconds=round(duration_seconds, 3),
                prompt_chars=len(prompt or ""),
                response_chars=len(response or ""),
                prompt_tokens_est=prompt_tokens,
                completion_tokens_est=completion_tokens,
                total_tokens_est=prompt_tokens + completion_tokens,
                prompt_tokens_actual=actual.get("prompt_tokens"),
                completion_tokens_actual=actual.get("completion_tokens"),
                total_tokens_actual=actual.get("total_tokens"),
                actual_source=actual.get("source"),
                actual_session_file=actual.get("session_file"),
                actual_cost_total=actual.get("cost_total"),
            )
        )

    def to_dict(self) -> dict[str, Any]:
        prompt_est = sum(item.prompt_tokens_est for item in self.entries)
        completion_est = sum(item.completion_tokens_est for item in self.entries)
        actual_available = any(item.total_tokens_actual is not None for item in self.entries)
        return {
            "run_id": self.run_id,
            "actual_usage_available": actual_available,
            "estimation_method": "mixed_cjk_ascii_estimate",
            "summary": {
                "call_count": len(self.entries),
                "prompt_tokens_est": prompt_est,
                "completion_tokens_est": completion_est,
                "total_tokens_est": prompt_est + completion_est,
                "prompt_tokens_actual": _sum_actual("prompt_tokens_actual", self.entries),
                "completion_tokens_actual": _sum_actual("completion_tokens_actual", self.entries),
                "total_tokens_actual": _sum_actual("total_tokens_actual", self.entries),
                "cost_total_actual": _sum_actual_float("actual_cost_total", self.entries),
            },
            "entries": [asdict(item) for item in self.entries],
        }


CURRENT_TOKEN_TRACKER: ContextVar[TokenUsageTracker | None] = ContextVar("CURRENT_TOKEN_TRACKER", default=None)


def set_current_token_tracker(tracker: TokenUsageTracker | None) -> None:
    CURRENT_TOKEN_TRACKER.set(tracker)


def get_current_token_tracker() -> TokenUsageTracker | None:
    return CURRENT_TOKEN_TRACKER.get()


def record_generation_usage(
    *,
    step_name: str,
    provider: str,
    model: str,
    prompt: str,
    response: str,
    started_at: str,
    start_time: float,
    actual: dict[str, Any] | None = None,
) -> None:
    tracker = get_current_token_tracker()
    if tracker is None:
        return
    finished_at = datetime.now().isoformat(timespec="seconds")
    tracker.record(
        step_name=step_name,
        provider=provider,
        model=model,
        prompt=prompt,
        response=response,
        started_at=started_at,
        finished_at=finished_at,
        duration_seconds=time.perf_counter() - start_time,
        actual=actual,
    )


def estimate_tokens(text: str) -> int:
    """A deterministic rough estimate for mixed Chinese/English prompts."""
    text = str(text or "")
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    ascii_words = re.findall(r"[A-Za-z0-9_./:-]+", text)
    ascii_chars = sum(len(word) for word in ascii_words)
    ascii_tokens = sum(max(1, math.ceil(len(word) / 4)) for word in ascii_words)
    other = max(0, len(text) - cjk - ascii_chars)
    return int(cjk + ascii_tokens + other * 0.25)


def markdown_report(token_usage: dict[str, Any]) -> str:
    summary = token_usage.get("summary") or {}
    entries = token_usage.get("entries") or []
    lines = [
        "# Token 消耗记录",
        "",
        f"- Run ID：`{token_usage.get('run_id', '')}`",
        f"- 是否包含真实 usage：{'是' if token_usage.get('actual_usage_available') else '否'}",
        f"- 估算方法：`{token_usage.get('estimation_method', '')}`",
        "",
        "## 总览",
        "",
        "| 指标 | 数值 |",
        "|---|---:|",
        f"| 调用次数 | {summary.get('call_count', 0)} |",
        f"| 估算 prompt tokens | {summary.get('prompt_tokens_est', 0):,} |",
        f"| 估算 completion tokens | {summary.get('completion_tokens_est', 0):,} |",
        f"| 估算 total tokens | {summary.get('total_tokens_est', 0):,} |",
        f"| 真实 prompt tokens | {_format_optional_int(summary.get('prompt_tokens_actual'))} |",
        f"| 真实 completion tokens | {_format_optional_int(summary.get('completion_tokens_actual'))} |",
        f"| 真实 total tokens | {_format_optional_int(summary.get('total_tokens_actual'))} |",
        f"| 真实费用合计 | {_format_optional_float(summary.get('cost_total_actual'))} |",
        "",
        "## 每次调用",
        "",
        "| # | 步骤 | Provider | Model | 耗时 | 估算合计 | 真实输入 | 真实输出 | 真实合计 | 来源 |",
        "|---:|---|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for index, item in enumerate(entries, start=1):
        lines.append(
            "| {index} | {step} | {provider} | {model} | {duration:.1f}s | {total:,} | {actual_prompt} | {actual_completion} | {actual_total} | {source} |".format(
                index=index,
                step=str(item.get("step_name", "")).replace("|", "/"),
                provider=str(item.get("provider", "")).replace("|", "/"),
                model=str(item.get("model", "")).replace("|", "/"),
                duration=float(item.get("duration_seconds") or 0),
                total=int(item.get("total_tokens_est") or 0),
                actual_prompt=_format_optional_int(item.get("prompt_tokens_actual")),
                actual_completion=_format_optional_int(item.get("completion_tokens_actual")),
                actual_total=_format_optional_int(item.get("total_tokens_actual")),
                source=item.get("actual_source") or "未记录",
            )
        )
    if not entries:
        lines.append("| - | 暂无记录 | - | - | - | - | - | - | - |")
    lines.extend(["", "## 说明", ""])
    if token_usage.get("actual_usage_available"):
        lines.append("本报告已从 Pi agent 本地 session 记录中提取真实 token usage；估算 token 仅作为对照。")
    else:
        lines.append("本报告未找到可解析的真实 token usage，因此当前记录仍为本地估算 token。")
    lines.append("")
    return "\n".join(lines)


def _sum_actual(field_name: str, entries: list[TokenUsageEntry]) -> int | None:
    values = [getattr(item, field_name) for item in entries if getattr(item, field_name) is not None]
    if not values:
        return None
    return int(sum(values))


def _sum_actual_float(field_name: str, entries: list[TokenUsageEntry]) -> float | None:
    values = [getattr(item, field_name) for item in entries if getattr(item, field_name) is not None]
    if not values:
        return None
    return round(float(sum(values)), 8)


def _format_optional_int(value: Any) -> str:
    if value is None:
        return "未记录"
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return str(value)


def _format_optional_float(value: Any) -> str:
    if value is None:
        return "未记录"
    try:
        return f"{float(value):.8f}"
    except (TypeError, ValueError):
        return str(value)

"""Pi coding agent backed generation helper."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from typing import Any


class PiCodingAgentError(RuntimeError):
    """Raised when Pi coding agent cannot complete a generation request."""


@dataclass(frozen=True)
class PiCodingAgentResult:
    text: str
    usage: dict[str, Any] | None = None
    session_file: str | None = None


def generate_text_with_pi_coding_agent(
    prompt: str,
    project_root: Path,
    pi_command: str = "pi",
    provider: str = "deepseek",
    model: str | None = "deepseek-chat",
    skill_paths: list[Path] | None = None,
    timeout: int = 600,
    include_usage: bool = False,
) -> str | PiCodingAgentResult:
    """Run Pi coding agent non-interactively and return text output."""
    project_root = project_root.resolve()
    cmd = [_resolve_pi_command(pi_command), "--print", "--mode", "text"]

    if provider:
        cmd.extend(["--provider", provider])
    if model:
        cmd.extend(["--model", model])

    # Keep Pi as the reasoning/writing core. Project tools still run in Python.
    cmd.extend(["--tools", "read,grep,find,ls"])

    for skill_path in skill_paths or []:
        if skill_path.exists():
            cmd.extend(["--skill", str(skill_path)])

    cmd.append(prompt)
    session_started_at = time.time()

    try:
        completed = subprocess.run(
            cmd,
            cwd=project_root,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
            env=_subprocess_env(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PiCodingAgentError(str(exc)) from exc

    if completed.returncode != 0:
        stderr = completed.stderr.strip() or completed.stdout.strip()
        raise PiCodingAgentError(stderr[:1200] or f"Pi coding agent exited with {completed.returncode}")

    content = completed.stdout.strip()
    if not content:
        raise PiCodingAgentError("Pi coding agent returned empty output.")
    text = _strip_pi_noise(content)
    if not include_usage:
        return text

    usage, session_file = _extract_latest_session_usage(session_started_at)
    return PiCodingAgentResult(text=text, usage=usage, session_file=session_file)


def _resolve_pi_command(pi_command: str) -> str:
    if "/" in pi_command:
        return pi_command
    return shutil.which(pi_command, path=_subprocess_env().get("PATH")) or pi_command


def _subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    fallback_paths = [
        "/opt/homebrew/bin",
        "/opt/homebrew/sbin",
        "/usr/local/bin",
        "/usr/bin",
        "/bin",
        "/usr/sbin",
        "/sbin",
        str(Path.home() / ".local" / "bin"),
        str(Path.home() / "bin"),
    ]
    existing = [item for item in env.get("PATH", "").split(os.pathsep) if item]
    merged = []
    for item in [*fallback_paths, *existing]:
        if item and item not in merged:
            merged.append(item)
    env["PATH"] = os.pathsep.join(merged)
    return env


def _strip_pi_noise(text: str) -> str:
    lines = [
        line
        for line in text.splitlines()
        if not line.startswith("Warning: (startup session lookup")
        and not line.startswith("Warning: (runtime creation")
    ]
    return "\n".join(lines).strip()


def _extract_latest_session_usage(started_at: float) -> tuple[dict[str, Any] | None, str | None]:
    sessions_dir = Path.home() / ".pi" / "agent" / "sessions"
    if not sessions_dir.exists():
        return None, None

    candidates: list[Path] = []
    for path in sessions_dir.glob("**/*.jsonl"):
        try:
            if path.stat().st_mtime >= started_at - 1:
                candidates.append(path)
        except OSError:
            continue

    for path in sorted(candidates, key=lambda item: item.stat().st_mtime, reverse=True):
        usage = _extract_usage_from_jsonl(path)
        if usage:
            return usage, str(path)
    return None, None


def _extract_usage_from_jsonl(path: Path) -> dict[str, Any] | None:
    latest_usage: dict[str, Any] | None = None
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                for usage in _find_usage_dicts(event):
                    latest_usage = _normalize_pi_usage(usage, path)
    except OSError:
        return None
    return latest_usage


def _find_usage_dicts(value: Any) -> list[dict[str, Any]]:
    usages: list[dict[str, Any]] = []
    if isinstance(value, dict):
        usage = value.get("usage")
        if isinstance(usage, dict) and (
            "totalTokens" in usage or "input" in usage or "output" in usage
        ):
            usages.append(usage)
        for child in value.values():
            usages.extend(_find_usage_dicts(child))
    elif isinstance(value, list):
        for child in value:
            usages.extend(_find_usage_dicts(child))
    return usages


def _normalize_pi_usage(usage: dict[str, Any], path: Path) -> dict[str, Any]:
    input_tokens = _int_or_zero(usage.get("input"))
    output_tokens = _int_or_zero(usage.get("output"))
    cache_read = _int_or_zero(usage.get("cacheRead"))
    cache_write = _int_or_zero(usage.get("cacheWrite"))
    total_tokens = _int_or_none(usage.get("totalTokens"))
    prompt_tokens = input_tokens + cache_read + cache_write
    completion_tokens = output_tokens
    cost = usage.get("cost") if isinstance(usage.get("cost"), dict) else {}
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens if total_tokens is not None else prompt_tokens + completion_tokens,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read,
        "cache_write_tokens": cache_write,
        "cost_total": cost.get("total"),
        "source": "pi_session",
        "session_file": str(path),
        "raw_usage": usage,
    }


def _int_or_zero(value: Any) -> int:
    parsed = _int_or_none(value)
    return parsed if parsed is not None else 0


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None

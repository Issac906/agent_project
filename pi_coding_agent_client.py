"""Pi coding agent backed generation helper."""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess


class PiCodingAgentError(RuntimeError):
    """Raised when Pi coding agent cannot complete a generation request."""


def generate_text_with_pi_coding_agent(
    prompt: str,
    project_root: Path,
    pi_command: str = "pi",
    provider: str = "deepseek",
    model: str | None = "deepseek-chat",
    skill_paths: list[Path] | None = None,
    timeout: int = 600,
) -> str:
    """Run Pi coding agent non-interactively and return text output."""
    project_root = project_root.resolve()
    cmd = [_resolve_pi_command(pi_command), "--print", "--no-session", "--mode", "text"]

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

    try:
        completed = subprocess.run(
            cmd,
            cwd=project_root,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PiCodingAgentError(str(exc)) from exc

    if completed.returncode != 0:
        stderr = completed.stderr.strip() or completed.stdout.strip()
        raise PiCodingAgentError(stderr[:1200] or f"Pi coding agent exited with {completed.returncode}")

    content = completed.stdout.strip()
    if not content:
        raise PiCodingAgentError("Pi coding agent returned empty output.")
    return _strip_pi_noise(content)


def _resolve_pi_command(pi_command: str) -> str:
    if "/" in pi_command:
        return pi_command
    return shutil.which(pi_command) or pi_command


def _strip_pi_noise(text: str) -> str:
    lines = [
        line
        for line in text.splitlines()
        if not line.startswith("Warning: (startup session lookup")
        and not line.startswith("Warning: (runtime creation")
    ]
    return "\n".join(lines).strip()

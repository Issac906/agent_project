"""Codex CLI backed generation helper."""

from __future__ import annotations

from pathlib import Path
import glob
import shutil
import subprocess
import tempfile


class CodexCLIError(RuntimeError):
    """Raised when Codex CLI cannot complete a generation request."""


def generate_text_with_codex_cli(
    prompt: str,
    project_root: Path,
    codex_command: str = "codex",
    model: str | None = None,
    sandbox: str = "workspace-write",
    enable_search: bool = True,
    timeout: int = 600,
) -> str:
    """Run Codex CLI non-interactively and return its final message."""
    project_root = project_root.resolve()
    with tempfile.NamedTemporaryFile("w+", suffix=".txt", delete=False) as output_file:
        output_path = Path(output_file.name)

    cmd = [_resolve_codex_command(codex_command)]
    if model:
        cmd.extend(["--model", model])
    if enable_search:
        cmd.append("--search")
    cmd.extend(
        [
            "exec",
            "--cd",
            str(project_root),
            "--sandbox",
            sandbox,
            "--output-last-message",
            str(output_path),
            "--color",
            "never",
            "-",
        ]
    )

    try:
        completed = subprocess.run(
            cmd,
            input=prompt,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CodexCLIError(str(exc)) from exc

    try:
        content = output_path.read_text(encoding="utf-8").strip()
    except OSError:
        content = ""
    finally:
        try:
            output_path.unlink()
        except OSError:
            pass

    if completed.returncode != 0:
        stderr = completed.stderr.strip() or completed.stdout.strip()
        raise CodexCLIError(stderr[:1200] or f"Codex CLI exited with {completed.returncode}")

    if not content:
        content = completed.stdout.strip()
    if not content:
        raise CodexCLIError("Codex CLI returned empty output.")
    return _strip_codex_noise(content)


def _strip_codex_noise(text: str) -> str:
    lines = [
        line
        for line in text.splitlines()
        if not line.startswith("WARNING: proceeding, even though we could not create PATH aliases")
    ]
    return "\n".join(lines).strip()


def _resolve_codex_command(codex_command: str) -> str:
    if "/" in codex_command:
        return codex_command

    resolved = shutil.which(codex_command)
    if resolved:
        return resolved

    candidates = sorted(
        glob.glob(
            str(
                Path.home()
                / ".vscode"
                / "extensions"
                / "openai.chatgpt-*"
                / "bin"
                / "*"
                / codex_command
            )
        ),
        reverse=True,
    )
    if candidates:
        return candidates[0]

    return codex_command

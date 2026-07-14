"""Runtime paths for source and packaged application modes."""

from __future__ import annotations

import os
from pathlib import Path
import sys


APP_NAME = "patent_agent"


def is_packaged() -> bool:
    return bool(getattr(sys, "frozen", False))


def resource_root() -> Path:
    override = os.getenv("PATENT_AGENT_RESOURCE_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    bundled = getattr(sys, "_MEIPASS", None)
    if bundled:
        return Path(bundled).resolve()
    return Path.cwd()


def resource_path(*parts: str) -> Path:
    return resource_root().joinpath(*parts)


def data_root() -> Path:
    override = os.getenv("PATENT_AGENT_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if is_packaged():
        return Path.home() / ".patent_agent"
    return Path.cwd()


def data_path(*parts: str) -> Path:
    path = data_root().joinpath(*parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def bundled_pi_invocation() -> tuple[Path, Path] | None:
    if not is_packaged() or os.name != "nt":
        return None
    executable_dir = Path(sys.executable).resolve().parent
    runtime_roots = [
        executable_dir / "pi-runtime",
        executable_dir.parent / "pi-runtime",
    ]
    for runtime_root in runtime_roots:
        node = runtime_root / "node.exe"
        cli = runtime_root / "node_modules" / "@earendil-works" / "pi-coding-agent" / "dist" / "cli.js"
        if node.is_file() and cli.is_file():
            return node, cli
    return None

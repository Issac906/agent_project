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

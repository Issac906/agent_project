"""Discover and run the shared local Patent Agent backend."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from typing import Any

import requests


BACKEND_HOST = "127.0.0.1"
BACKEND_PORT = 5000
RUNTIME_DIR = Path.home() / ".patent_agent" / "runtime"
BACKEND_DESCRIPTOR = RUNTIME_DIR / "backend.json"
BACKEND_LOG = RUNTIME_DIR / "backend.log"


@dataclass(frozen=True)
class BackendEndpoint:
    url: str
    pid: int | None = None


def _find_available_port(host: str, preferred: int) -> int:
    for port in range(preferred, preferred + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind((host, port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"无法在 {preferred}-{preferred + 49} 范围内找到可用端口。")


def _health(url: str, timeout: float = 1.5) -> dict[str, Any] | None:
    try:
        response = requests.get(f"{url.rstrip('/')}/api/integration/health", timeout=timeout)
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError):
        return None
    return payload if isinstance(payload, dict) and payload.get("ok") else None


def discover_backend() -> BackendEndpoint | None:
    try:
        payload = json.loads(BACKEND_DESCRIPTOR.read_text(encoding="utf-8"))
        url = str(payload.get("url") or "").rstrip("/")
        pid = int(payload["pid"]) if payload.get("pid") else None
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None
    if not url or not _health(url):
        return None
    return BackendEndpoint(url=url, pid=pid)


def publish_backend(url: str) -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    temporary = BACKEND_DESCRIPTOR.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(
            {
                "url": url.rstrip("/"),
                "pid": os.getpid(),
                "updated_at": time.time(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    temporary.replace(BACKEND_DESCRIPTOR)


def _backend_command() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "--backend"]
    launcher = Path(__file__).resolve().parent / "desktop_launcher.py"
    return [sys.executable, str(launcher), "--backend"]


def start_backend_process(timeout: float = 30.0) -> BackendEndpoint:
    existing = discover_backend()
    if existing:
        return existing

    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    log = BACKEND_LOG.open("a", encoding="utf-8")
    process_root = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
    kwargs: dict[str, Any] = {
        "cwd": str(process_root),
        "stdin": subprocess.DEVNULL,
        "stdout": log,
        "stderr": subprocess.STDOUT,
        "close_fds": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0)
        )
    else:
        kwargs["start_new_session"] = True

    try:
        subprocess.Popen(_backend_command(), **kwargs)
    finally:
        log.close()

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        endpoint = discover_backend()
        if endpoint:
            return endpoint
        time.sleep(0.2)
    raise RuntimeError(f"本地后端启动超时。日志位置：{BACKEND_LOG}")


def run_backend_forever() -> None:
    from app import app

    host = os.getenv("WEB_HOST", BACKEND_HOST)
    preferred = int(os.getenv("WEB_PORT", str(BACKEND_PORT)))
    port = _find_available_port(host, preferred)
    url = f"http://{host}:{port}"
    publish_backend(url)
    app.run(host=host, port=port, debug=False, use_reloader=False, threaded=True)

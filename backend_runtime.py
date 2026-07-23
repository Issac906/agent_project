"""Discover and run the shared local Patent Agent backend."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
from typing import Any, BinaryIO

import requests


BACKEND_HOST = "127.0.0.1"
BACKEND_PORT = 5000
RUNTIME_DIR = Path.home() / ".patent_agent" / "runtime"
BACKEND_DESCRIPTOR = RUNTIME_DIR / "backend.json"
BACKEND_LOG = RUNTIME_DIR / "backend.log"
BACKEND_LOCK = RUNTIME_DIR / "backend.lock"


@dataclass(frozen=True)
class BackendEndpoint:
    url: str
    pid: int | None = None


@lru_cache(maxsize=1)
def runtime_identity() -> str:
    """Identify the exact application build serving the local backend."""

    digest = hashlib.sha256()
    if getattr(sys, "frozen", False):
        candidates = [Path(sys.executable)]
        bundle_root = Path(getattr(sys, "_MEIPASS", ""))
        candidates.extend(
            [
                bundle_root / "templates" / "index.html",
                bundle_root / "static" / "style.css",
            ]
        )
    else:
        project_root = Path(__file__).resolve().parent
        candidates = [
            project_root / "backend_runtime.py",
            project_root / "app.py",
            project_root / "templates" / "index.html",
            project_root / "static" / "style.css",
        ]

    for path in candidates:
        if not path.is_file():
            continue
        digest.update(str(path.name).encode("utf-8"))
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()[:20]


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


def _read_backend_descriptor() -> dict[str, Any] | None:
    try:
        payload = json.loads(BACKEND_DESCRIPTOR.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def discover_backend() -> BackendEndpoint | None:
    payload = _read_backend_descriptor()
    if payload is None:
        return None
    try:
        url = str(payload.get("url") or "").rstrip("/")
        pid = int(payload["pid"]) if payload.get("pid") else None
    except (ValueError, TypeError):
        return None
    if payload.get("runtime_id") != runtime_identity():
        return None
    health = _health(url)
    if not url or not health or health.get("runtime_id") != runtime_identity():
        return None
    return BackendEndpoint(url=url, pid=pid)


def retire_backend_from_other_build(timeout: float = 5.0) -> bool:
    """Stop a verified local Patent Agent backend left by an older build."""

    payload = _read_backend_descriptor()
    if not payload or payload.get("runtime_id") == runtime_identity():
        return False
    try:
        url = str(payload.get("url") or "").rstrip("/")
        pid = int(payload.get("pid") or 0)
    except (TypeError, ValueError):
        return False
    health = _health(url)
    if (
        not url
        or pid <= 0
        or not health
        or health.get("service") != "patent-agent"
        or int(health.get("pid") or 0) != pid
    ):
        return False

    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _health(url, timeout=0.3) is None:
            return True
        time.sleep(0.1)
    raise RuntimeError(f"旧版 Patent Agent 后台未能退出（PID {pid}）。请完全退出应用后重试。")


def _acquire_backend_lock() -> BinaryIO | None:
    """Acquire a process-wide lock shared by source and packaged launches."""

    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    handle = BACKEND_LOCK.open("a+b")
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            if handle.read(1) == b"":
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, ImportError):
        handle.close()
        return None
    return handle


def publish_backend(url: str) -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    temporary = BACKEND_DESCRIPTOR.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(
            {
                "url": url.rstrip("/"),
                "pid": os.getpid(),
                "runtime_id": runtime_identity(),
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
    retire_backend_from_other_build()

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
    from app import app, start_background_services

    backend_lock = _acquire_backend_lock()
    if backend_lock is None:
        raise RuntimeError("另一个 Patent Agent 后台正在运行，请先退出旧实例。")
    host = os.getenv("WEB_HOST", BACKEND_HOST)
    preferred = int(os.getenv("WEB_PORT", str(BACKEND_PORT)))
    port = _find_available_port(host, preferred)
    url = f"http://{host}:{port}"
    try:
        publish_backend(url)
        start_background_services()
        app.run(host=host, port=port, debug=False, use_reloader=False, threaded=True)
    finally:
        backend_lock.close()

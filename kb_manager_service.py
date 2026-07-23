"""Provision physically isolated LightRAG containers behind a protected API.

Run this service on the company server, not on end-user desktops. It requires
access to Docker and stores one registry entry and two bind-mounted directories
for every managed knowledge base.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hmac
import json
import os
from pathlib import Path
import re
import socket
from threading import Lock
import time
from typing import Any
from uuid import uuid4

from dotenv import dotenv_values
from flask import Flask, jsonify, request
import requests


@dataclass(frozen=True)
class ManagerConfig:
    api_key: str
    image: str
    public_host: str
    port_start: int
    port_end: int
    data_root: Path
    lightrag_env_file: Path | None
    lightrag_api_key: str | None
    docker_network: str | None
    startup_timeout: int

    @classmethod
    def from_env(cls) -> "ManagerConfig":
        api_key = os.getenv("KB_MANAGER_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("KB_MANAGER_API_KEY 必须配置。")
        env_file = os.getenv("KB_MANAGER_LIGHTRAG_ENV_FILE", "").strip()
        return cls(
            api_key=api_key,
            image=os.getenv("KB_MANAGER_LIGHTRAG_IMAGE", "ghcr.io/hkuds/lightrag:latest").strip(),
            public_host=os.getenv("KB_MANAGER_PUBLIC_HOST", "127.0.0.1").strip(),
            port_start=int(os.getenv("KB_MANAGER_PORT_START", "9622")),
            port_end=int(os.getenv("KB_MANAGER_PORT_END", "9699")),
            data_root=Path(os.getenv("KB_MANAGER_DATA_ROOT", "/var/lib/patent-agent/knowledge-bases")).expanduser(),
            lightrag_env_file=Path(env_file).expanduser() if env_file else None,
            lightrag_api_key=os.getenv("LIGHTRAG_API_KEY", "").strip() or None,
            docker_network=os.getenv("KB_MANAGER_DOCKER_NETWORK", "").strip() or None,
            startup_timeout=max(30, int(os.getenv("KB_MANAGER_STARTUP_TIMEOUT", "240"))),
        )


class LightRAGInstanceManager:
    def __init__(self, config: ManagerConfig, docker_client: Any | None = None) -> None:
        self.config = config
        self.config.data_root.mkdir(parents=True, exist_ok=True)
        self.registry_path = self.config.data_root / "registry.json"
        self.lock = Lock()
        if docker_client is None:
            try:
                import docker
            except ImportError as exc:
                raise RuntimeError("请安装 requirements-kb-manager.txt 中的 docker SDK。") from exc
            docker_client = docker.from_env()
        self.docker = docker_client

    def list_instances(self) -> list[dict[str, Any]]:
        with self.lock:
            rows = self._load_registry()
        return [self._with_runtime_status(row) for row in rows]

    def create(self, name: str, description: str = "") -> dict[str, Any]:
        clean_name = re.sub(r"\s+", " ", str(name or "")).strip()
        if not clean_name:
            raise ValueError("知识库名称不能为空。")
        if len(clean_name) > 60:
            raise ValueError("知识库名称不能超过 60 个字符。")

        with self.lock:
            rows = self._load_registry()
            if any(str(row.get("name", "")).casefold() == clean_name.casefold() for row in rows):
                raise ValueError("已经存在同名知识库。")
            instance_id = self._new_id(clean_name, rows)
            port = self._allocate_port(rows)
            root = (self.config.data_root / instance_id).resolve()
            storage_dir = root / "rag_storage"
            input_dir = root / "inputs"
            storage_dir.mkdir(parents=True, exist_ok=False)
            input_dir.mkdir(parents=True, exist_ok=False)
            row = {
                "id": instance_id,
                "name": clean_name,
                "description": str(description or "").strip(),
                "workspace": instance_id,
                "container_name": f"patent-kb-{instance_id}",
                "port": port,
                "base_url": f"http://{self.config.public_host}:{port}",
                "graph_url": (
                    f"http://{self.config.public_host}:{port}"
                    "/webui/?tab=knowledge-graph#/"
                ),
                "data_dir": str(root),
                "status": "provisioning",
                "created_at": datetime.now().isoformat(timespec="seconds"),
            }
            rows.append(row)
            self._save_registry(rows)

        try:
            environment = self._container_environment(instance_id)
            options: dict[str, Any] = {
                "image": self.config.image,
                "name": row["container_name"],
                "detach": True,
                "restart_policy": {"Name": "unless-stopped"},
                "ports": {"9621/tcp": port},
                "volumes": {
                    str(storage_dir): {"bind": "/app/data/rag_storage", "mode": "rw"},
                    str(input_dir): {"bind": "/app/data/inputs", "mode": "rw"},
                },
                "environment": environment,
                "labels": {
                    "com.patent-agent.managed": "true",
                    "com.patent-agent.knowledge-base": instance_id,
                },
            }
            if self.config.docker_network:
                options["network"] = self.config.docker_network
            self.docker.containers.run(**options)
            self._wait_until_ready(row["base_url"])
            return self._set_status(instance_id, "ready")
        except Exception as exc:
            self._set_status(instance_id, "failed", str(exc))
            raise RuntimeError(f"LightRAG 实例创建失败：{exc}") from exc

    def delete(self, instance_id: str) -> dict[str, Any]:
        clean_id = str(instance_id or "").strip()
        with self.lock:
            rows = self._load_registry()
            row = next((item for item in rows if item.get("id") == clean_id), None)
        if not row:
            raise ValueError("知识库实例不存在。")
        try:
            container = self.docker.containers.get(row["container_name"])
            container.remove(force=True)
        except Exception as exc:
            if "not found" not in str(exc).lower():
                raise RuntimeError(f"停止知识库容器失败：{exc}") from exc

        # Data is retained by default for recovery. An administrator can remove
        # the instance directory after confirming that no backup is required.
        with self.lock:
            rows = [item for item in self._load_registry() if item.get("id") != clean_id]
            self._save_registry(rows)
        return {"ok": True, "id": clean_id, "data_retained_at": row.get("data_dir")}

    def _container_environment(self, workspace: str) -> dict[str, str]:
        values: dict[str, str] = {}
        env_path = self.config.lightrag_env_file
        if env_path:
            if not env_path.exists():
                raise RuntimeError(f"LightRAG 环境文件不存在：{env_path}")
            values.update({str(k): str(v) for k, v in dotenv_values(env_path).items() if v is not None})
        values.update(
            {
                "HOST": "0.0.0.0",
                "PORT": "9621",
                "WORKSPACE": workspace,
                "WORKING_DIR": "/app/data/rag_storage",
                "INPUT_DIR": "/app/data/inputs",
            }
        )
        if self.config.lightrag_api_key:
            values["LIGHTRAG_API_KEY"] = self.config.lightrag_api_key
        return values

    def _wait_until_ready(self, base_url: str) -> None:
        deadline = time.monotonic() + self.config.startup_timeout
        headers = {}
        if self.config.lightrag_api_key:
            headers["Authorization"] = f"Bearer {self.config.lightrag_api_key}"
        last_error = ""
        while time.monotonic() < deadline:
            try:
                response = requests.get(f"{base_url}/health", headers=headers, timeout=5)
                if response.ok:
                    return
                last_error = f"HTTP {response.status_code}"
            except requests.RequestException as exc:
                last_error = str(exc)
            time.sleep(2)
        raise TimeoutError(f"实例健康检查超时：{last_error}")

    def _allocate_port(self, rows: list[dict[str, Any]]) -> int:
        used = {int(row["port"]) for row in rows if str(row.get("port", "")).isdigit()}
        for port in range(self.config.port_start, self.config.port_end + 1):
            if port not in used and self._port_available(port):
                return port
        raise RuntimeError("知识库端口池已用完。")

    @staticmethod
    def _port_available(port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("0.0.0.0", port))
            except OSError:
                return False
        return True

    @staticmethod
    def _new_id(name: str, rows: list[dict[str, Any]]) -> str:
        ascii_slug = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")[:28]
        prefix = ascii_slug or "kb"
        used = {str(row.get("id")) for row in rows}
        while True:
            candidate = f"{prefix}-{uuid4().hex[:8]}"
            if candidate not in used:
                return candidate

    def _with_runtime_status(self, row: dict[str, Any]) -> dict[str, Any]:
        result = dict(row)
        try:
            container = self.docker.containers.get(str(row.get("container_name")))
            container.reload()
            result["container_status"] = container.status
        except Exception:
            result["container_status"] = "missing"
        return result

    def _set_status(self, instance_id: str, status: str, error: str = "") -> dict[str, Any]:
        with self.lock:
            rows = self._load_registry()
            row = next(item for item in rows if item.get("id") == instance_id)
            row["status"] = status
            row["error"] = error[:1000] if error else ""
            row["updated_at"] = datetime.now().isoformat(timespec="seconds")
            self._save_registry(rows)
            return dict(row)

    def _load_registry(self) -> list[dict[str, Any]]:
        if not self.registry_path.exists():
            return []
        try:
            payload = json.loads(self.registry_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        return [row for row in payload if isinstance(row, dict)] if isinstance(payload, list) else []

    def _save_registry(self, rows: list[dict[str, Any]]) -> None:
        temporary = self.registry_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.registry_path)


def create_app(config: ManagerConfig | None = None, manager: LightRAGInstanceManager | None = None) -> Flask:
    config = config or ManagerConfig.from_env()
    manager = manager or LightRAGInstanceManager(config)
    service = Flask(__name__)

    @service.before_request
    def authenticate() -> Any:
        if request.path == "/health":
            return None
        supplied = request.headers.get("Authorization", "")
        expected = f"Bearer {config.api_key}"
        if not hmac.compare_digest(supplied, expected):
            return jsonify({"error": "Unauthorized"}), 401
        return None

    @service.get("/health")
    def health() -> Any:
        try:
            manager.docker.ping()
            docker_ready = True
        except Exception:
            docker_ready = False
        return jsonify({"ok": docker_ready, "service": "knowledge-base-manager"}), 200 if docker_ready else 503

    @service.get("/knowledge-bases")
    def list_instances() -> Any:
        return jsonify({"knowledge_bases": manager.list_instances()})

    @service.post("/knowledge-bases")
    def create_instance() -> Any:
        payload = request.get_json(silent=True) or {}
        return jsonify(manager.create(payload.get("name", ""), payload.get("description", ""))), 201

    @service.delete("/knowledge-bases/<instance_id>")
    def delete_instance(instance_id: str) -> Any:
        payload = request.get_json(silent=True) or {}
        if payload.get("confirm") is not True:
            return jsonify({"error": "必须明确确认删除。"}), 400
        return jsonify(manager.delete(instance_id))

    @service.errorhandler(ValueError)
    def value_error(exc: ValueError) -> Any:
        return jsonify({"error": str(exc)}), 400

    @service.errorhandler(Exception)
    def unexpected_error(exc: Exception) -> Any:
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 500

    return service


if __name__ == "__main__":
    cfg = ManagerConfig.from_env()
    create_app(cfg).run(
        host=os.getenv("KB_MANAGER_BIND_HOST", "0.0.0.0"),
        port=int(os.getenv("KB_MANAGER_BIND_PORT", "9700")),
    )

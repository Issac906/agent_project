"""Client for the protected LightRAG instance management service."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests


class KnowledgeBaseManagerError(RuntimeError):
    """Raised when an isolated LightRAG instance cannot be provisioned."""


@dataclass(frozen=True)
class KnowledgeBaseManagerClient:
    base_url: str
    api_key: str
    timeout: int = 240

    def __post_init__(self) -> None:
        object.__setattr__(self, "base_url", self.base_url.strip().rstrip("/"))
        if not self.base_url:
            raise ValueError("KB_MANAGER_URL 不能为空。")
        if not self.api_key:
            raise ValueError("KB_MANAGER_API_KEY 不能为空。")

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/health")

    def list_knowledge_bases(self) -> dict[str, Any]:
        return self._request("GET", "/knowledge-bases")

    def create_knowledge_base(self, name: str, description: str = "") -> dict[str, Any]:
        return self._request(
            "POST",
            "/knowledge-bases",
            json={"name": name, "description": description},
        )

    def delete_knowledge_base(self, instance_id: str) -> dict[str, Any]:
        return self._request(
            "DELETE",
            f"/knowledge-bases/{instance_id}",
            json={"confirm": True},
        )

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = requests.request(
                method,
                f"{self.base_url}{path}",
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                },
                timeout=self.timeout,
                **kwargs,
            )
            response.raise_for_status()
        except requests.ConnectionError as exc:
            raise KnowledgeBaseManagerError("无法连接知识库管理服务。") from exc
        except requests.Timeout as exc:
            raise KnowledgeBaseManagerError("知识库实例创建超时。") from exc
        except requests.HTTPError as exc:
            body = exc.response.text[:500] if exc.response is not None else ""
            status = exc.response.status_code if exc.response is not None else "unknown"
            raise KnowledgeBaseManagerError(
                f"知识库管理服务返回 HTTP {status}: {body}"
            ) from exc
        except requests.RequestException as exc:
            raise KnowledgeBaseManagerError(f"知识库管理请求失败：{exc}") from exc
        try:
            payload = response.json()
        except ValueError as exc:
            raise KnowledgeBaseManagerError("知识库管理服务返回了无效 JSON。") from exc
        if not isinstance(payload, dict):
            raise KnowledgeBaseManagerError("知识库管理服务返回格式不正确。")
        return payload

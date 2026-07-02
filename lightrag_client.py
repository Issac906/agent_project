"""Small client wrapper for the LightRAG Server API."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import os
from urllib.parse import urlsplit, urlunsplit

import requests


class LightRAGClientError(RuntimeError):
    """Raised when the LightRAG API cannot return a usable response."""


@dataclass
class LightRAGClient:
    """Client for querying a LightRAG knowledge base.

    The default base URL is normalized because the user-facing Web UI URL often
    looks like ``http://host:9621/webui/#/`` while API routes usually live at
    the server root, such as ``http://host:9621/query``.
    """

    base_url: str
    api_key: str | None = None
    timeout: int = 30
    query_mode: str = "mix"
    include_chunk_content: bool = True

    def __post_init__(self) -> None:
        self.base_url = self._normalize_base_url(self.base_url)

    def list_documents(self) -> Any:
        """Return documents currently known by the knowledge base."""
        return self._request("GET", "/documents")

    def get_status_counts(self) -> Any:
        """Return document processing status counts."""
        return self._request("GET", "/documents/status_counts")

    def upload_document(self, file_obj: Any, filename: str) -> Any:
        """Upload a document file to LightRAG input directory."""
        files = {"file": (filename, file_obj)}
        try:
            return self._request("POST", "/documents/upload", files=files)
        except LightRAGClientError as exc:
            if "HTTP 错误: 400" not in str(exc) and "HTTP 错误: 422" not in str(exc):
                raise
            file_obj.seek(0)
            return self._request("POST", "/documents/upload", files={"upload_file": (filename, file_obj)})

    def scan_documents(self) -> Any:
        """Ask LightRAG to scan input directory for new documents."""
        return self._request("POST", "/documents/scan", allow_empty=True)

    def clear_documents(self) -> Any:
        """Clear documents from the LightRAG knowledge base."""
        return self._request("DELETE", "/documents", allow_empty=True)

    def delete_documents(
        self,
        doc_ids: list[str],
        delete_file: bool = True,
        delete_llm_cache: bool = False,
    ) -> Any:
        """Delete selected documents by LightRAG document IDs."""
        if not doc_ids:
            raise LightRAGClientError("请选择至少一个要删除的文档。")
        return self._request(
            "DELETE",
            "/documents/delete_document",
            json={
                "doc_ids": doc_ids,
                "delete_file": delete_file,
                "delete_llm_cache": delete_llm_cache,
            },
            allow_empty=True,
        )

    def query(self, question: str) -> Any:
        """Ask the knowledge base a question."""
        try:
            return self._request("POST", "/query", json=self._build_query_payload(question))
        except LightRAGClientError as exc:
            if "HTTP 错误: 400" not in str(exc) and "HTTP 错误: 422" not in str(exc):
                raise
            return self._request("POST", "/query", json={"query": question})

    def query_data(self, question: str) -> Any:
        """Return lower-level retrieval context when the API supports it."""
        try:
            return self._request("POST", "/query/data", json=self._build_query_payload(question))
        except LightRAGClientError as exc:
            if "HTTP 错误: 400" not in str(exc) and "HTTP 错误: 422" not in str(exc):
                raise
            return self._request("POST", "/query/data", json={"query": question})

    def _build_query_payload(self, question: str) -> dict[str, Any]:
        """Build the request body for LightRAG query endpoints.

        Adjust this method after checking the LightRAG Swagger/OpenAPI docs if
        your server expects different field names, modes, or extra parameters.
        """
        payload: dict[str, Any] = {
            "query": question,
            "mode": self.query_mode,
            "only_need_context": False,
            "only_need_prompt": False,
            "response_type": "Multiple Paragraphs",
            "include_references": True,
            "include_chunk_content": self.include_chunk_content,
        }

        top_k = os.getenv("LIGHTRAG_TOP_K")
        if top_k:
            try:
                payload["top_k"] = int(top_k)
            except ValueError as exc:
                raise LightRAGClientError("LIGHTRAG_TOP_K 必须是整数。") from exc

        return payload

    def _request(self, method: str, path: str, allow_empty: bool = False, **kwargs: Any) -> Any:
        url = f"{self.base_url}{path}"
        headers = kwargs.pop("headers", {})
        headers.setdefault("Accept", "application/json")

        if self.api_key:
            headers.setdefault("Authorization", f"Bearer {self.api_key}")

        try:
            response = requests.request(
                method,
                url,
                headers=headers,
                timeout=self.timeout,
                **kwargs,
            )
            response.raise_for_status()
        except requests.ConnectionError as exc:
            raise LightRAGClientError(f"无法连接 LightRAG API: {url}") from exc
        except requests.Timeout as exc:
            raise LightRAGClientError(f"请求 LightRAG API 超时: {url}") from exc
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else "unknown"
            body = exc.response.text if exc.response is not None else ""
            raise LightRAGClientError(
                f"LightRAG API 返回 HTTP 错误: {status}. 响应内容: {body[:500]}"
            ) from exc
        except requests.RequestException as exc:
            raise LightRAGClientError(f"LightRAG API 请求失败: {exc}") from exc

        if not response.content:
            if allow_empty:
                return {"ok": True}
            raise LightRAGClientError("LightRAG API 返回内容为空。")

        try:
            data = response.json()
        except ValueError as exc:
            raise LightRAGClientError(
                f"LightRAG API 返回内容不是有效 JSON: {response.text[:500]}"
            ) from exc

        if data in (None, "", [], {}):
            if allow_empty:
                return {"ok": True}
            raise LightRAGClientError("LightRAG API 返回 JSON 内容为空。")

        return data

    @staticmethod
    def _normalize_base_url(base_url: str) -> str:
        cleaned = base_url.strip()
        if not cleaned:
            raise ValueError("LIGHTRAG_BASE_URL 不能为空。")

        parts = urlsplit(cleaned)
        path = parts.path.rstrip("/")
        if path.endswith("/webui"):
            path = path[: -len("/webui")]

        normalized = urlunsplit((parts.scheme, parts.netloc, path, "", "")).rstrip("/")
        if not normalized:
            raise ValueError(f"LIGHTRAG_BASE_URL 无效: {base_url}")

        return normalized

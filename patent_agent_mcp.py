"""Dependency-free STDIO MCP server for Patent Agent."""

from __future__ import annotations

import json
import sys
from typing import Any

from patent_agent_bridge import TOOLS, PatentAgentBridgeError, call_tool, json_text


SERVER_INFO = {"name": "patent-agent", "version": "1.0.0"}
PROTOCOL_VERSION = "2024-11-05"


def _write(message: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def _result(request_id: Any, result: Any) -> None:
    _write({"jsonrpc": "2.0", "id": request_id, "result": result})


def _error(request_id: Any, code: int, message: str) -> None:
    _write({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}})


def _handle(message: dict[str, Any]) -> None:
    request_id = message.get("id")
    method = message.get("method")
    params = message.get("params") or {}

    if method == "initialize":
        requested = str(params.get("protocolVersion") or PROTOCOL_VERSION)
        _result(
            request_id,
            {
                "protocolVersion": requested,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER_INFO,
                "instructions": (
                    "Use these tools to operate the installed Patent Agent. Keep run_id between calls. "
                    "Always present candidate ideas and generated sections to the user before selecting or accepting them."
                ),
            },
        )
        return
    if method in {"notifications/initialized", "notifications/cancelled"}:
        return
    if method == "ping":
        _result(request_id, {})
        return
    if method == "tools/list":
        _result(request_id, {"tools": [tool.mcp_definition() for tool in TOOLS]})
        return
    if method == "tools/call":
        name = str(params.get("name") or "")
        arguments = params.get("arguments") or {}
        try:
            value = call_tool(name, arguments)
            _result(
                request_id,
                {
                    "content": [{"type": "text", "text": json_text(value)}],
                    "structuredContent": value,
                    "isError": False,
                },
            )
        except (PatentAgentBridgeError, KeyError, TypeError, ValueError) as exc:
            _result(
                request_id,
                {
                    "content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}],
                    "isError": True,
                },
            )
        return
    if request_id is not None:
        _error(request_id, -32601, f"Method not found: {method}")


def main() -> None:
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
            if isinstance(message, dict):
                _handle(message)
        except json.JSONDecodeError as exc:
            _error(None, -32700, f"Parse error: {exc}")
        except Exception as exc:  # noqa: BLE001 - keep the MCP process alive
            request_id = message.get("id") if isinstance(locals().get("message"), dict) else None
            _error(request_id, -32603, f"Internal error: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()


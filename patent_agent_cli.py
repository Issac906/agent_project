"""JSON CLI for desktop AI clients that do not support MCP."""

from __future__ import annotations

import argparse
import json
import sys

from patent_agent_bridge import TOOLS, PatentAgentBridgeError, call_tool, json_text


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Patent Agent JSON CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("tools", help="列出可用工具")
    call = subparsers.add_parser("call", help="调用一个工具并输出 JSON")
    call.add_argument("tool", choices=[tool.name for tool in TOOLS])
    call.add_argument("arguments", nargs="?", default="{}", help="JSON 参数对象")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "tools":
        print(json_text({"tools": [tool.mcp_definition() for tool in TOOLS]}))
        return 0
    try:
        arguments = json.loads(args.arguments)
        if not isinstance(arguments, dict):
            raise ValueError("arguments 必须是 JSON 对象。")
        print(json_text(call_tool(args.tool, arguments)))
        return 0
    except (json.JSONDecodeError, PatentAgentBridgeError, ValueError) as exc:
        print(json_text({"error": f"{type(exc).__name__}: {exc}"}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())


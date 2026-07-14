"""Shared chat/CLI bridge for the Patent Agent HTTP workflow."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Callable
from urllib.parse import urljoin

import requests

from backend_runtime import BackendEndpoint, start_backend_process


class PatentAgentBridgeError(RuntimeError):
    """Raised when the shared local backend cannot complete an operation."""


class PatentAgentClient:
    def __init__(self, endpoint: BackendEndpoint | None = None) -> None:
        self.endpoint = endpoint or start_backend_process()
        self.base_url = self.endpoint.url.rstrip("/")

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        timeout: float = 900.0,
    ) -> dict[str, Any]:
        try:
            response = requests.request(
                method,
                urljoin(f"{self.base_url}/", path.lstrip("/")),
                json=payload,
                timeout=timeout,
            )
        except requests.RequestException as exc:
            raise PatentAgentBridgeError(f"无法连接专利 Agent 后端：{exc}") from exc
        try:
            data = response.json()
        except ValueError as exc:
            raise PatentAgentBridgeError(
                f"后端返回了非 JSON 内容（HTTP {response.status_code}）。"
            ) from exc
        if not response.ok:
            message = data.get("error") if isinstance(data, dict) else None
            raise PatentAgentBridgeError(message or f"后端请求失败（HTTP {response.status_code}）。")
        if not isinstance(data, dict):
            raise PatentAgentBridgeError("后端返回格式不是 JSON 对象。")
        return data

    def absolute_url(self, path: str | None) -> str | None:
        if not path:
            return None
        return urljoin(f"{self.base_url}/", path.lstrip("/"))


def _assessment_summary(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return {
        "score": value.get("score"),
        "level": value.get("level"),
        "needs_external_search": value.get("needs_external_search"),
        "dimensions": value.get("dimensions") or [],
    }


def _compact_state(client: PatentAgentClient, state: dict[str, Any]) -> dict[str, Any]:
    artifacts = {
        name: {
            "path": path,
            "download_url": client.absolute_url(path),
        }
        for name, path in (state.get("artifacts") or {}).items()
        if path
    }
    section = state.get("section") or {}
    return {
        "run_id": state.get("id"),
        "phase": state.get("phase"),
        "waiting_for": state.get("waiting_for"),
        "done": bool(state.get("done")),
        "error": state.get("error"),
        "agent_core": state.get("agent_core"),
        "innovation_level": state.get("innovation_level"),
        "innovation_level_label": state.get("innovation_level_label"),
        "knowledge_base": state.get("selected_knowledge_base"),
        "initial_assessment": _assessment_summary(state.get("initial_assessment")),
        "assessment": _assessment_summary(state.get("assessment")),
        "search_topic": state.get("search_topic"),
        "search_round": state.get("search_round"),
        "external_result_count": len((state.get("external") or {}).get("results") or []),
        "ideas": [
            {
                "index": index,
                "title": item.get("title"),
                "summary": item.get("summary"),
            }
            for index, item in enumerate(state.get("candidates") or [])
            if isinstance(item, dict)
        ],
        "selected_idea": state.get("selected_candidate"),
        "section": {
            "index": section.get("index"),
            "total": section.get("total"),
            "name": section.get("name"),
            "content": section.get("content"),
            "quality": section.get("quality"),
            "next_section_name": state.get("next_section_name"),
        }
        if section.get("name") or state.get("waiting_for") == "section"
        else None,
        "artifacts": artifacts,
        "history_record": state.get("history_record"),
        "latest_events": (state.get("events") or [])[-8:],
    }


def _continue_until_input(
    client: PatentAgentClient,
    run_id: str,
    max_steps: int = 50,
) -> dict[str, Any]:
    state = client.request("GET", f"/api/runs/{run_id}")
    steps = 0
    safety_limit_reached = False
    while not state.get("waiting_for") and not state.get("done") and not state.get("error"):
        if steps >= max_steps:
            safety_limit_reached = True
            break
        state = client.request("POST", f"/api/runs/{run_id}/advance", {})
        steps += 1
    result = _compact_state(client, state)
    result["automatic_steps"] = steps
    if safety_limit_reached:
        result["requires_attention"] = True
        result["message"] = (
            f"自动推进已达到 {max_steps} 步安全上限。运行仍被保留，"
            f"可使用 run_id={run_id} 查看状态或继续。"
        )
    return result


@dataclass(frozen=True)
class BridgeTool:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[PatentAgentClient, dict[str, Any]], dict[str, Any]]

    def mcp_definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }


OBJECT_SCHEMA = {"type": "object", "properties": {}, "additionalProperties": False}


def _system_status(client: PatentAgentClient, _args: dict[str, Any]) -> dict[str, Any]:
    health = client.request("GET", "/api/integration/health", timeout=10)
    settings = client.request("GET", "/api/settings", timeout=30)
    return {
        "backend": health,
        "agent_core": settings.get("agent_core"),
        "internal_llm": settings.get("internal_llm"),
        "skills": settings.get("skills") or [],
        "tools": settings.get("tools") or [],
        "desktop_url": client.base_url,
    }


def _list_knowledge_bases(client: PatentAgentClient, _args: dict[str, Any]) -> dict[str, Any]:
    data = client.request("GET", "/api/knowledge")
    documents = data.get("documents") or []
    total = {
        "id": "all",
        "name": "总知识库",
        "description": "使用完整知识图谱。",
        "document_count": len(documents),
        "node_count": len((data.get("graph") or {}).get("nodes") or []),
        "edge_count": len((data.get("graph") or {}).get("edges") or []),
    }
    groups = []
    for item in data.get("knowledge_bases") or []:
        if not isinstance(item, dict):
            continue
        groups.append(
            {
                key: item.get(key)
                for key in ("id", "name", "description", "document_count", "node_count", "edge_count")
            }
        )
    return {
        "knowledge_bases": [total, *groups],
        "documents": documents,
        "counts": data.get("counts") or {},
    }


def _start_run(client: PatentAgentClient, args: dict[str, Any]) -> dict[str, Any]:
    created = client.request(
        "POST",
        "/api/runs",
        {
            "innovation_level": args.get("innovation_level", "medium"),
            "knowledge_base_id": args.get("knowledge_base_id", "all"),
        },
    )
    run_id = str(created["run_id"])
    if args.get("auto_advance", True):
        return _continue_until_input(client, run_id)
    return {
        "run_id": run_id,
        "workflow_url": client.absolute_url(created.get("url")),
    }


def _get_run(client: PatentAgentClient, args: dict[str, Any]) -> dict[str, Any]:
    state = client.request("GET", f"/api/runs/{args['run_id']}")
    return _compact_state(client, state)


def _list_active_runs(client: PatentAgentClient, _args: dict[str, Any]) -> dict[str, Any]:
    data = client.request("GET", "/api/runs")
    return {"runs": data.get("runs") or []}


def _continue_run(client: PatentAgentClient, args: dict[str, Any]) -> dict[str, Any]:
    return _continue_until_input(client, str(args["run_id"]), int(args.get("max_steps", 50)))


def _retry_search(client: PatentAgentClient, args: dict[str, Any]) -> dict[str, Any]:
    state = client.request("POST", f"/api/runs/{args['run_id']}/retry-search", {})
    if args.get("auto_advance", True):
        return _continue_until_input(client, str(state["id"]))
    return _compact_state(client, state)


def _discard_run(client: PatentAgentClient, args: dict[str, Any]) -> dict[str, Any]:
    return client.request("DELETE", f"/api/runs/{args['run_id']}")


def _list_ideas(client: PatentAgentClient, args: dict[str, Any]) -> dict[str, Any]:
    state = client.request("GET", f"/api/runs/{args['run_id']}")
    return {
        "run_id": state.get("id"),
        "waiting_for": state.get("waiting_for"),
        "ideas": [
            {"index": index, **item}
            for index, item in enumerate(state.get("candidates") or [])
            if isinstance(item, dict)
        ],
        "material_strategy": state.get("material_strategy"),
        "similarity_report": {
            name: client.absolute_url(path)
            for name, path in (state.get("artifacts") or {}).items()
            if name.startswith("similarity") and path
        },
    }


def _select_idea(client: PatentAgentClient, args: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if args.get("custom_title"):
        payload["custom_title"] = args["custom_title"]
    else:
        payload["index"] = int(args["index"])
    state = client.request("POST", f"/api/runs/{args['run_id']}/select", payload)
    if args.get("auto_advance", True):
        return _continue_until_input(client, str(state["id"]))
    return _compact_state(client, state)


def _section_action(action: str) -> Callable[[PatentAgentClient, dict[str, Any]], dict[str, Any]]:
    def handler(client: PatentAgentClient, args: dict[str, Any]) -> dict[str, Any]:
        payload = {"action": action}
        if args.get("instruction"):
            payload["instruction"] = args["instruction"]
        if args.get("content"):
            payload["content"] = args["content"]
        state = client.request("POST", f"/api/runs/{args['run_id']}/section", payload)
        if args.get("auto_advance", action == "accept") and not state.get("waiting_for") and not state.get("done"):
            return _continue_until_input(client, str(state["id"]))
        return _compact_state(client, state)

    return handler


def _list_history(client: PatentAgentClient, args: dict[str, Any]) -> dict[str, Any]:
    data = client.request("GET", "/api/history")
    limit = max(1, min(int(args.get("limit", 10)), 10))
    records = []
    for item in (data.get("records") or [])[:limit]:
        if not isinstance(item, dict):
            continue
        records.append(
            {
                "record_id": item.get("id"),
                "run_id": item.get("run_id"),
                "title": item.get("title"),
                "created_at": item.get("created_at"),
                "completed_at": item.get("completed_at"),
                "knowledge_base": item.get("selected_knowledge_base"),
                "innovation_level": item.get("innovation_level"),
                "candidate_count": item.get("candidate_count"),
                "final_quality": item.get("final_quality"),
                "detail_url": client.absolute_url(item.get("detail_url")),
                "artifacts": {
                    name: client.absolute_url(path)
                    for name, path in (item.get("artifacts") or {}).items()
                    if path
                },
            }
        )
    return {"records": records}


def _get_history(client: PatentAgentClient, args: dict[str, Any]) -> dict[str, Any]:
    record = client.request("GET", f"/api/history/{args['record_id']}")
    if not args.get("include_full_process", True):
        record.pop("interactions", None)
        record.pop("accepted_sections", None)
    return record


RUN_ID_SCHEMA = {
    "type": "object",
    "properties": {"run_id": {"type": "string", "description": "运行 ID"}},
    "required": ["run_id"],
    "additionalProperties": False,
}


TOOLS = [
    BridgeTool("patent_system_status", "查看专利 Agent 后端、内核、Skills 和 Tools 状态。", OBJECT_SCHEMA, _system_status),
    BridgeTool("patent_list_knowledge_bases", "列出可选知识库、文档和知识图谱规模。", OBJECT_SCHEMA, _list_knowledge_bases),
    BridgeTool(
        "patent_start_run",
        "新建一次专利生成，并默认自动推进到需要用户选择 idea 的位置。",
        {
            "type": "object",
            "properties": {
                "knowledge_base_id": {"type": "string", "default": "all"},
                "innovation_level": {"type": "string", "enum": ["low", "medium", "high"], "default": "medium"},
                "auto_advance": {"type": "boolean", "default": True},
            },
            "additionalProperties": False,
        },
        _start_run,
    ),
    BridgeTool("patent_get_run", "查看当前专利生成进度、待交互内容和下载结果。", RUN_ID_SCHEMA, _get_run),
    BridgeTool("patent_list_active_runs", "列出当前仍保存在后端中的运行，用于恢复中断的聊天任务。", OBJECT_SCHEMA, _list_active_runs),
    BridgeTool("patent_discard_run", "清理不再需要或已经卡住的活动运行。", RUN_ID_SCHEMA, _discard_run),
    BridgeTool(
        "patent_continue_run",
        "自动推进流程，直到需要用户选择 idea、确认章节或流程完成。",
        {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "max_steps": {"type": "integer", "minimum": 1, "maximum": 100, "default": 50},
            },
            "required": ["run_id"],
            "additionalProperties": False,
        },
        _continue_run,
    ),
    BridgeTool(
        "patent_retry_search",
        "在修正搜索 API 配置或网络后，重试某次运行的外部检索并继续推进。",
        {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "auto_advance": {"type": "boolean", "default": True},
            },
            "required": ["run_id"],
            "additionalProperties": False,
        },
        _retry_search,
    ),
    BridgeTool("patent_list_ideas", "展示本次生成的候选 idea、素材组合路径和相似专利报告。", RUN_ID_SCHEMA, _list_ideas),
    BridgeTool(
        "patent_select_idea",
        "选择候选 idea 或输入自定义方向，并默认生成第一章供用户确认。",
        {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "index": {"type": "integer", "minimum": 0, "description": "候选列表中明确显示的 0-based index"},
                "custom_title": {"type": "string"},
                "auto_advance": {"type": "boolean", "default": True},
            },
            "required": ["run_id"],
            "additionalProperties": False,
        },
        _select_idea,
    ),
    BridgeTool("patent_accept_section", "接受当前章节，并默认继续生成下一章。", RUN_ID_SCHEMA, _section_action("accept")),
    BridgeTool("patent_rewrite_section", "不改变方向，重新撰写当前章节。", RUN_ID_SCHEMA, _section_action("rewrite")),
    BridgeTool(
        "patent_revise_section",
        "根据用户意见修改当前章节。",
        {
            "type": "object",
            "properties": {"run_id": {"type": "string"}, "instruction": {"type": "string"}},
            "required": ["run_id", "instruction"],
            "additionalProperties": False,
        },
        _section_action("revise"),
    ),
    BridgeTool(
        "patent_manual_edit_section",
        "用用户给出的完整内容替换当前章节。",
        {
            "type": "object",
            "properties": {"run_id": {"type": "string"}, "content": {"type": "string"}},
            "required": ["run_id", "content"],
            "additionalProperties": False,
        },
        _section_action("manual"),
    ),
    BridgeTool("patent_finish_run", "提前结束并保存当前已确认章节。", RUN_ID_SCHEMA, _section_action("quit")),
    BridgeTool(
        "patent_list_history",
        "列出最近的专利生成历史记录。",
        {
            "type": "object",
            "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 10, "default": 10}},
            "additionalProperties": False,
        },
        _list_history,
    ),
    BridgeTool(
        "patent_get_history",
        "读取某次历史记录，包括完整用户交互、idea、章节和下载产物。",
        {
            "type": "object",
            "properties": {
                "record_id": {"type": "string"},
                "include_full_process": {"type": "boolean", "default": True},
            },
            "required": ["record_id"],
            "additionalProperties": False,
        },
        _get_history,
    ),
]

TOOLS_BY_NAME = {tool.name: tool for tool in TOOLS}


def call_tool(name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    tool = TOOLS_BY_NAME.get(name)
    if not tool:
        raise PatentAgentBridgeError(f"未知工具：{name}")
    return tool.handler(PatentAgentClient(), arguments or {})


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)

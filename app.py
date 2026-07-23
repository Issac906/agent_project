"""Web UI for the patent discovery agent."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import socket
from threading import Lock
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4
from typing import Any

from dotenv import load_dotenv
from flask import Flask, abort, jsonify, render_template, request, send_from_directory
from werkzeug.exceptions import HTTPException

from agent_skill_loader import format_skills_for_prompt, load_agent_skills
from backend_runtime import runtime_identity
from config import AppConfig, load_config
from citation_report import append_citation_section, build_citation_snapshot
from docx_exporter import export_markdown_to_docx
from external_search import ExternalSearchResult, search_external_materials
from formula_utils import normalize_formula_markdown
from lightrag_client import LightRAGClient, LightRAGClientError
from knowledge_graph import build_knowledge_graph, format_knowledge_graph_for_prompt
from knowledge_base_groups import (
    create_knowledge_base,
    delete_knowledge_base_registration,
    list_knowledge_base_catalog,
    require_knowledge_base,
    update_knowledge_base_instance,
)
from kb_manager_client import KnowledgeBaseManagerClient, KnowledgeBaseManagerError
from material_strategy import build_material_strategy, innovation_index_for_level, innovation_level_label, normalize_innovation_level
from patent_memory import (
    append_patent_memory,
    format_patent_memory_for_prompt,
    load_patent_memory,
    summarize_candidate_for_memory,
)
from patent_discovery_agent import (
    WRITING_STEPS,
    MaterialAssessment,
    PatentCandidate,
    _assemble_document,
    _assess_materials,
    _flatten_documents,
    _format_search_results,
    _generate_candidates,
    _generate_section,
    _infer_search_topic,
    _load_documents,
    _clean_candidate_title,
    _revise_section,
)
from patent_quality_tool import review_document, review_section
from similar_patent_analysis import generate_similar_patent_analysis
from token_usage import TokenUsageTracker, markdown_report, set_current_token_tracker
from tool_registry import register_tool, registered_tools
from runtime_paths import data_path, resource_path, resource_root
from user_config import save_user_config, user_config_view


OUTPUT_DIR = data_path("outputs")
HISTORY_DIR = OUTPUT_DIR / "history"
HISTORY_INDEX = HISTORY_DIR / "index.json"
MAX_HISTORY_ITEMS = 10
MATERIAL_READY_SCORE = 80
MAX_SEARCH_NO_PROGRESS_ROUNDS = 3

load_dotenv()
app = Flask(
    __name__,
    template_folder=str(resource_path("templates")),
    static_folder=str(resource_path("static")),
)
RUNS: dict[str, "WebPatentRun"] = {}
RUNS_LOCK = Lock()
_FEISHU_MANAGER: Any = None
_FEISHU_MANAGER_LOCK = Lock()


@app.get("/api/integration/health")
def api_integration_health() -> Any:
    return jsonify(
        {
            "ok": True,
            "service": "patent-agent",
            "pid": os.getpid(),
            "active_runs": len(RUNS),
            "runtime_id": runtime_identity(),
        }
    )


@app.errorhandler(ValueError)
def handle_value_error(exc: ValueError) -> Any:
    return jsonify({"error": str(exc)}), 400


@app.errorhandler(LightRAGClientError)
def handle_lightrag_error(exc: LightRAGClientError) -> Any:
    return jsonify({"error": str(exc)}), 502


@app.errorhandler(KnowledgeBaseManagerError)
def handle_kb_manager_error(exc: KnowledgeBaseManagerError) -> Any:
    return jsonify({"error": str(exc)}), 502


@app.errorhandler(Exception)
def handle_unexpected_error(exc: Exception) -> Any:
    if isinstance(exc, HTTPException):
        if request.path.startswith("/api/"):
            return jsonify({"error": exc.description}), exc.code or 500
        return exc
    if request.path.startswith("/api/"):
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 500
    raise exc


@app.after_request
def disable_dynamic_cache(response: Any) -> Any:
    if request.path.startswith(("/api/", "/run/", "/history", "/settings", "/outputs/")):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


def make_client(config: AppConfig, base_url: str | None = None) -> LightRAGClient:
    return LightRAGClient(
        base_url=base_url or config.lightrag_base_url,
        api_key=config.lightrag_api_key,
        query_mode=config.lightrag_query_mode,
        include_chunk_content=config.lightrag_include_chunk_content,
    )


def make_kb_manager(config: AppConfig) -> KnowledgeBaseManagerClient | None:
    if not config.kb_manager_url or not config.kb_manager_api_key:
        return None
    return KnowledgeBaseManagerClient(
        base_url=config.kb_manager_url,
        api_key=config.kb_manager_api_key,
        timeout=config.kb_manager_timeout,
    )


def kb_manager_status(config: AppConfig) -> dict[str, Any]:
    manager = make_kb_manager(config)
    if manager is None:
        return {"configured": False, "available": False, "message": "未配置自动知识库管理服务。"}
    try:
        health = manager.health()
        return {
            "configured": True,
            "available": bool(health.get("ok")),
            "message": "自动实例管理可用。" if health.get("ok") else "管理服务暂不可用。",
        }
    except KnowledgeBaseManagerError as exc:
        return {"configured": True, "available": False, "message": str(exc)}


def client_for_knowledge_base(config: AppConfig, knowledge_base_id: str) -> LightRAGClient:
    if str(knowledge_base_id or "all") == "all":
        return make_client(config)
    item = require_knowledge_base(knowledge_base_id)
    if LightRAGClient._normalize_base_url(item["base_url"]) == LightRAGClient._normalize_base_url(config.lightrag_base_url):
        raise ValueError("独立知识库不能与总知识库使用同一个 LightRAG API 地址。")
    return make_client(config, item["base_url"])


def load_isolated_knowledge_bases(config: AppConfig) -> list[dict[str, Any]]:
    """Read every registered LightRAG instance independently."""
    groups: list[dict[str, Any]] = []
    total_url = LightRAGClient._normalize_base_url(config.lightrag_base_url)
    for item in list_knowledge_base_catalog():
        row = dict(item)
        row.update({"documents": [], "document_count": 0, "node_count": 0, "edge_count": 0, "graph": None})
        if not item.get("base_url"):
            row["status_message"] = "旧版逻辑分组，需绑定独立 LightRAG 实例后迁移素材。"
            groups.append(row)
            continue
        if LightRAGClient._normalize_base_url(item["base_url"]) == total_url:
            row["selectable"] = False
            row["isolation"] = "invalid_shared_instance"
            row["status_message"] = "该地址与总知识库相同，不构成物理隔离。"
            groups.append(row)
            continue
        try:
            client = make_client(config, item["base_url"])
            documents = _load_documents(client)
            rows = _flatten_documents(documents)
            graph = build_knowledge_graph(client, documents)
            row["documents"] = [_document_json(value) for value in rows]
            row["document_count"] = len(rows)
            row["graph"] = graph
            row["node_count"] = len((graph or {}).get("nodes", []))
            row["edge_count"] = len((graph or {}).get("edges", []))
            row["counts"] = documents.get("_counts", {})
            row["selectable"] = True
            row["isolation"] = "physical"
        except (LightRAGClientError, ValueError) as exc:
            row["selectable"] = False
            row["status_message"] = str(exc)
        groups.append(row)
    return groups


def _document_json(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "file_path": row.get("file_path", "未知"),
        "id": _document_identifier(row),
        "status": row.get("status", "未知"),
        "chunks_count": row.get("chunks_count", 0),
        "content_summary": row.get("content_summary", ""),
    }


def lightrag_graph_webui_url(base_url: str) -> str:
    """Return the LightRAG WebUI graph tab URL for a server base URL."""

    parts = urlsplit((base_url or "").strip())
    path = parts.path.rstrip("/")
    if not path.endswith("/webui"):
        path = f"{path}/webui" if path else "/webui"
    query = "&".join(value for value in (parts.query, "tab=knowledge-graph") if value)
    return urlunsplit((parts.scheme, parts.netloc, f"{path}/", query, "/"))


class WebPatentRun:
    def __init__(
        self,
        config: AppConfig,
        innovation_level: str = "medium",
        knowledge_base_id: str = "all",
        channel: str = "web",
    ) -> None:
        self.id = uuid4().hex[:12]
        self.created_at = datetime.now().isoformat(timespec="seconds")
        self.config = config
        self.innovation_level = normalize_innovation_level(innovation_level)
        self.innovation_index = innovation_index_for_level(self.innovation_level)
        self.knowledge_base_id = str(knowledge_base_id or "all")
        self.channel = str(channel or "web")
        self.client = client_for_knowledge_base(config, self.knowledge_base_id)
        self.output_dir = OUTPUT_DIR
        self.skills = load_agent_skills(resource_root())
        self.skill_prompt = format_skills_for_prompt(self.skills)
        self.token_usage = TokenUsageTracker(self.id)

        self.phase = "created"
        self.waiting_for: str | None = None
        self.error: str | None = None
        self.events: list[dict[str, str]] = []
        self.interactions: list[dict[str, Any]] = []

        self.documents: dict[str, Any] | None = None
        self.active_documents: dict[str, Any] | None = None
        self.full_knowledge_graph: dict[str, Any] | None = None
        self.knowledge_graph: dict[str, Any] | None = None
        self.knowledge_bases: list[dict[str, Any]] = []
        self.selected_knowledge_base: dict[str, Any] | None = None
        self.material_text = ""
        self.initial_assessment: MaterialAssessment | None = None
        self.assessment: MaterialAssessment | None = None
        self.search_topic = ""
        self.base_search_topic = ""
        self.search_round = 0
        self.search_no_progress_rounds = 0
        self.external: ExternalSearchResult | None = None
        self.material_strategy: dict[str, Any] | None = None
        self.compact_patent_memory: list[dict[str, str]] = []
        self.compact_patent_memory_context = ""
        self.compact_patent_memory_result: dict[str, Any] | None = None
        self.candidates: list[PatentCandidate] = []
        self.selected_candidate: PatentCandidate | None = None
        self.similarity_xlsx: Path | None = None
        self.similarity_markdown: Path | None = None

        self.section_index = 0
        self.current_section_name = ""
        self.current_section = ""
        self.current_quality_report: dict[str, Any] | None = None
        self.final_quality_report: dict[str, Any] | None = None
        self.accepted_sections: list[str] = []
        self.final_markdown = ""
        self.citation_report: dict[str, Any] | None = None
        self.output_path: Path | None = None
        self.docx_path: Path | None = None
        self.history_record: dict[str, Any] | None = None

    def add_event(self, title: str, detail: str = "") -> None:
        self.events.append(
            {
                "title": title,
                "detail": detail,
                "timestamp": datetime.now().isoformat(timespec="seconds"),
            }
        )

    def add_interaction(self, kind: str, title: str, payload: dict[str, Any] | None = None) -> None:
        self.interactions.append(
            {
                "kind": kind,
                "title": title,
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "payload": payload or {},
            }
        )

    def _refresh_runtime_config(self) -> None:
        self.config = load_config()
        self.client = client_for_knowledge_base(self.config, self.knowledge_base_id)

    def advance(self) -> None:
        self._refresh_runtime_config()
        set_current_token_tracker(self.token_usage)
        if self.waiting_for == "material":
            self.waiting_for = None
            self.phase = "searched"
            self.add_event("恢复自动检索", "已取消旧版素材暂停状态，继续自动补充检索。")
        if self.error or self.waiting_for or self.phase == "done":
            return

        try:
            if self.phase == "created":
                self.documents = _load_documents(self.client)
                self.full_knowledge_graph = build_knowledge_graph(self.client, self.documents)
                # A generation run connects only to the selected physical instance.
                self.knowledge_bases = list_knowledge_base_catalog()
                self._activate_knowledge_base_scope()
                self.material_text = self._knowledge_graph_material_text()
                self.phase = "documents_loaded"
                self.add_event("读取知识图谱", f"已选择“{self._selected_knowledge_base_name()}”，后续写作仅使用该图谱证据包。")
                self.add_interaction(
                    "knowledge",
                    "读取知识图谱",
                    {
                        "counts": self.documents.get("_counts", {}),
                        "documents": self._active_knowledge_documents_json(),
                        "all_documents": self._knowledge_documents_json(),
                        "graph": self.knowledge_graph,
                        "full_graph": self.full_knowledge_graph,
                        "knowledge_bases": self.knowledge_bases,
                        "selected_knowledge_base": self.selected_knowledge_base,
                        "material_policy": "graph_only",
                    },
                )
                return

            if self.phase == "documents_loaded":
                self.initial_assessment = _assess_materials(self._active_documents(), knowledge_graph=self.knowledge_graph)
                self.assessment = self.initial_assessment
                self.phase = "initial_assessed"
                self.add_event("素材初评", self._assessment_line(self.initial_assessment))
                self.add_interaction("assessment", "素材初评", asdict(self.initial_assessment))
                return

            if self.phase == "initial_assessed":
                self.base_search_topic = _infer_search_topic(self.config, self.material_text)
                self.search_topic = self.base_search_topic
                self.external = search_external_materials(
                    self.search_topic,
                    enabled=True,
                    max_results=6,
                )
                self.search_round = 1
                self.search_no_progress_rounds = 0 if self.external.results else 1
                self.phase = "searched"
                self.add_event(
                    "外部检索",
                    f"检索主题：{self.search_topic}；返回 {len(self.external.results)} 条结果。",
                )
                self.add_interaction(
                    "search",
                    "外部检索",
                    {
                        "topic": self.search_topic,
                        "notes": self.external.notes,
                        "results": self.external.results,
                    },
                )
                return

            if self.phase == "searched":
                self.assessment = _assess_materials(self._active_documents(), self.external, knowledge_graph=self.knowledge_graph)
                self.add_event("检索后复评", self._assessment_line(self.assessment))
                self.add_interaction("assessment", "检索后复评", asdict(self.assessment))
                if not self._materials_ready(self.assessment):
                    topic = self._next_supplement_search_topic()
                    supplement = search_external_materials(
                        topic,
                        enabled=True,
                        max_results=6,
                    )
                    self.search_round += 1
                    before_count = len(self._external().results)
                    self.external = self._merge_external_results(
                        self._external(),
                        supplement,
                    )
                    added_count = max(0, len(self.external.results) - before_count)
                    if added_count:
                        self.search_no_progress_rounds = 0
                    else:
                        self.search_no_progress_rounds += 1
                    self.search_topic = topic
                    self.add_event(
                        "自动补充检索",
                        f"第 {self.search_round} 轮，检索主题：{topic}；新增 {added_count} 条，累计 {len(self.external.results)} 条结果。",
                    )
                    self.add_interaction(
                        "search",
                        "自动补充检索",
                        {
                            "round": self.search_round,
                            "topic": topic,
                            "notes": supplement.notes,
                            "results": supplement.results,
                            "new_results": added_count,
                            "total_results": len(self.external.results),
                        },
                    )
                    if self.search_no_progress_rounds >= MAX_SEARCH_NO_PROGRESS_ROUNDS:
                        self.error = (
                            f"外部搜索连续 {self.search_no_progress_rounds} 轮未返回新结果。"
                            "请检查外部搜索 API 的地址、密钥和网络后重试本次检索。"
                        )
                        self.add_event("外部检索异常", self.error)
                        self.add_interaction(
                            "error",
                            "外部检索异常",
                            {
                                "message": self.error,
                                "round": self.search_round,
                                "topic": topic,
                                "notes": supplement.notes,
                            },
                        )
                    return
                self.phase = "reassessed"
                return

            if self.phase == "reassessed":
                self.compact_patent_memory = read_compact_patent_memory()
                self.compact_patent_memory_context = format_patent_memory_for_prompt(self.compact_patent_memory)
                self.material_strategy = build_material_strategy(
                    self._active_knowledge_documents_json(),
                    self._external(),
                    [],
                    knowledge_graph=self.knowledge_graph,
                    innovation_index=self.innovation_index,
                    innovation_level=self.innovation_level,
                )
                self.add_event("素材分层", self.material_strategy.get("summary", "已完成素材分层。"))
                self.add_interaction(
                    "material_strategy",
                    "生成 idea 前的素材分层",
                    self.material_strategy,
                )
                self.candidates = _generate_candidates(
                    self.config,
                    self.material_text,
                    self._assessment(),
                    self._external(),
                    skill_instructions=self.skill_prompt,
                    innovation_index=self.innovation_index,
                    innovation_level=self.innovation_level,
                    graph_fusion=self.material_strategy.get("graph_fusion") if self.material_strategy else None,
                    memory_context=self.compact_patent_memory_context,
                )
                self.material_strategy = build_material_strategy(
                    self._active_knowledge_documents_json(),
                    self._external(),
                    self.candidates,
                    knowledge_graph=self.knowledge_graph,
                    innovation_index=self.innovation_index,
                    innovation_level=self.innovation_level,
                )
                self.phase = "candidates_ready"
                self.add_event("候选专利方向", f"已生成 {len(self.candidates)} 个候选方向。")
                self.add_interaction(
                    "candidates",
                    "生成候选专利方向",
                    {
                        "candidates": [asdict(item) for item in self.candidates],
                        "material_strategy": self.material_strategy,
                    },
                )
                return

            if self.phase == "candidates_ready":
                xlsx, markdown, rows = generate_similar_patent_analysis(
                    candidates=self.candidates,
                    external=self._external(),
                    output_dir=self.output_dir,
                )
                self.similarity_xlsx = xlsx
                self.similarity_markdown = markdown
                self.phase = "waiting_candidate"
                self.waiting_for = "candidate"
                self.add_event("相似专利差异分析", f"已生成 {rows} 条差异分析记录。")
                self.add_interaction(
                    "analysis",
                    "相似专利差异分析",
                    {
                        "rows": rows,
                        "xlsx": self._output_url(self.similarity_xlsx),
                        "markdown": self._output_url(self.similarity_markdown),
                    },
                )
                return

            if self.phase == "selected":
                self._generate_current_section()
                return

        except Exception as exc:  # noqa: BLE001 - show recoverable error in UI
            self.error = f"{type(exc).__name__}: {exc}"
            self.add_event("执行失败", self.error)

    def select_candidate(self, index: int | None, custom_title: str | None) -> None:
        if self.waiting_for != "candidate":
            raise ValueError("当前步骤不需要选择候选专利。")
        title = (custom_title or "").strip()
        if title:
            title = _clean_candidate_title(title)
            self.selected_candidate = PatentCandidate(
                title=title,
                summary="用户在前端手动输入的专利方向。",
                raw=title,
            )
        elif index is not None and 0 <= index < len(self.candidates):
            self.selected_candidate = self.candidates[index]
        else:
            raise ValueError("请选择一个候选方向，或输入新的专利名称。")

        self.waiting_for = None
        self.phase = "selected"
        self.section_index = 0
        self.accepted_sections = []
        self.current_section = ""
        self.add_event("用户选择", self.selected_candidate.title)
        self.add_interaction(
            "selection",
            "用户选择专利方向",
            {
                "selected": asdict(self.selected_candidate),
                "source": "custom" if custom_title else "candidate",
                "candidate_index": index,
            },
        )

    def handle_section_action(self, action: str, instruction: str = "", content: str = "") -> None:
        self._refresh_runtime_config()
        set_current_token_tracker(self.token_usage)
        if self.waiting_for != "section":
            raise ValueError("当前步骤不需要确认章节。")

        if action == "accept":
            self.add_interaction(
                "section_action",
                "用户接受章节",
                self._section_interaction_payload(action),
            )
            self._accept_current_section()
            return

        if action == "rewrite":
            self.add_interaction(
                "section_action",
                "用户要求重写章节",
                self._section_interaction_payload(action),
            )
            self._generate_current_section(force=True)
            return

        if action == "revise":
            if not instruction.strip():
                raise ValueError("请输入修改意见。")
            self.current_section = _revise_section(
                config=self.config,
                candidate=self._selected_candidate(),
                section_name=self.current_section_name,
                current_section=self.current_section,
                instruction=instruction.strip(),
                material_text=self.material_text,
                external=self._external(),
                skill_instructions=self.skill_prompt,
                accepted_sections=self.accepted_sections,
            )
            self.add_event("章节修改", f"{self.current_section_name} 已根据意见重写。")
            self._review_current_section()
            self.add_interaction(
                "section_action",
                "用户提出修改意见",
                self._section_interaction_payload(
                    action,
                    instruction=instruction.strip(),
                ),
            )
            return

        if action == "manual":
            if not content.strip():
                raise ValueError("手动编辑内容不能为空。")
            self.current_section = normalize_formula_markdown(content.strip())
            self._review_current_section()
            self.add_interaction(
                "section_action",
                "用户手动编辑章节",
                self._section_interaction_payload(action),
            )
            if not (self.current_quality_report or {}).get("passed", False):
                self.add_event("手动内容质量检查", "仍有未解决项，请修改后再接受。")
                return
            self._accept_current_section()
            return

        if action == "quit":
            self.add_interaction(
                "section_action",
                "用户提前结束写作",
                self._section_interaction_payload(action),
            )
            self._finish()
            self.add_event("提前结束", "已保存当前已确认章节。")
            return

        raise ValueError("未知操作。")

    def handle_material_action(self, action: str) -> None:
        if self.waiting_for != "material":
            raise ValueError("当前步骤不需要处理素材补充。")
        if action != "refresh":
            raise ValueError("未知素材处理操作。")
        self.documents = _load_documents(self.client)
        self.full_knowledge_graph = build_knowledge_graph(self.client, self.documents)
        self.knowledge_bases = list_knowledge_base_catalog()
        self._activate_knowledge_base_scope()
        self.material_text = self._knowledge_graph_material_text()
        self.assessment = _assess_materials(self._active_documents(), self.external, knowledge_graph=self.knowledge_graph)
        self.add_event("补充后复评", self._assessment_line(self.assessment))
        self.add_interaction(
            "assessment",
            "补充后复评",
            asdict(self.assessment),
        )
        if not self._materials_ready(self.assessment):
            self.add_event(
                "素材仍未达标",
                f"{self.assessment.score}/100，仍未达到 {MATERIAL_READY_SCORE}/100，继续暂停。",
            )
            return
        self.waiting_for = None
        self.phase = "reassessed"
        self.add_event("素材已达标", f"{self.assessment.score}/100，已达到候选生成门槛。")

    def retry_external_search(self) -> None:
        if self.phase != "searched":
            raise ValueError("当前运行不在外部检索阶段。")
        self.error = None
        self.search_no_progress_rounds = 0
        self.add_event("重试外部检索", "已重新读取运行时配置并继续自动补充检索。")
        self.advance()

    def snapshot(self) -> dict[str, Any]:
        rows = _flatten_documents(self.active_documents or self.documents or {})
        all_rows = _flatten_documents(self.documents or {})
        counts = (self.documents or {}).get("_counts", {})
        return {
            "id": self.id,
            "created_at": self.created_at,
            "phase": self.phase,
            "waiting_for": self.waiting_for,
            "error": self.error,
            "agent_core": self.config.agent_core,
            "innovation_index": self.innovation_index,
            "innovation_level": self.innovation_level,
            "innovation_level_label": innovation_level_label(self.innovation_level),
            "knowledge_base_id": self.knowledge_base_id,
            "channel": self.channel,
            "selected_knowledge_base": self.selected_knowledge_base,
            "events": self.events,
            "interactions": self.interactions,
            "skills": [{"name": skill.name, "path": str(skill.path)} for skill in self.skills],
            "knowledge": {
                "counts": counts,
                "graph": self.knowledge_graph,
                "full_graph": self.full_knowledge_graph,
                "knowledge_bases": self.knowledge_bases,
                "selected_knowledge_base": self.selected_knowledge_base,
                "documents": [
                    {
                        "file_path": row.get("file_path", "未知"),
                        "id": _document_identifier(row),
                        "status": row.get("status", "未知"),
                        "chunks_count": row.get("chunks_count", 0),
                        "content_summary": row.get("content_summary", ""),
                    }
                    for row in rows
                ],
                "all_documents": [
                    {
                        "file_path": row.get("file_path", "未知"),
                        "id": _document_identifier(row),
                        "status": row.get("status", "未知"),
                        "chunks_count": row.get("chunks_count", 0),
                        "content_summary": row.get("content_summary", ""),
                    }
                    for row in all_rows
                ],
            },
            "initial_assessment": self._assessment_json(self.initial_assessment),
            "assessment": self._assessment_json(self.assessment),
            "search_topic": self.search_topic,
            "search_round": self.search_round,
            "search_no_progress_rounds": self.search_no_progress_rounds,
            "external": {
                "notes": self.external.notes if self.external else [],
                "results": self.external.results if self.external else [],
                "text": _format_search_results(self.external) if self.external else "",
            },
            "material_strategy": self.material_strategy,
            "candidates": [
                {"title": item.title, "summary": item.summary, "raw": item.raw}
                for item in self.candidates
            ],
            "selected_candidate": asdict(self.selected_candidate) if self.selected_candidate else None,
            "artifacts": {
                "similarity_xlsx": self._output_url(self.similarity_xlsx),
                "similarity_markdown": self._output_url(self.similarity_markdown),
                "draft": self._output_url(self.output_path),
                "result": self._output_url(self.output_dir / "result.md") if self.output_path else None,
                "docx": self._output_url(self.docx_path),
            },
            "history_record": self.history_record,
            "section": {
                "index": self.section_index,
                "total": len(WRITING_STEPS),
                "name": self.current_section_name,
                "content": self.current_section,
                "quality": self.current_quality_report,
            },
            "final_quality": self.final_quality_report,
            "citations": self.citation_report,
            "token_usage": self.token_usage.to_dict(),
            "last_confirmed_section": self._last_confirmed_section_json(),
            "next_section_name": self._next_section_name(),
            "done": self.phase == "done",
        }

    def _generate_current_section(self, force: bool = False) -> None:
        if self.section_index >= len(WRITING_STEPS):
            self._finish()
            return
        section_name, requirements = WRITING_STEPS[self.section_index]
        self.current_section_name = section_name
        self.current_section = _generate_section(
            config=self.config,
            candidate=self._selected_candidate(),
            section_name=section_name,
            section_requirements=requirements,
            material_text=self.material_text,
            assessment=self._assessment(),
            external=self._external(),
            accepted_sections=self.accepted_sections,
            skill_instructions=self.skill_prompt,
        )
        self.phase = "waiting_section"
        self.waiting_for = "section"
        self.add_event("章节生成" if not force else "章节重写", section_name)
        self._review_current_section()
        self.add_interaction(
            "section",
            "章节重写" if force else "章节生成",
            self._section_interaction_payload("rewrite" if force else "generate"),
        )

    def _accept_current_section(self) -> None:
        self.accepted_sections.append(self.current_section)
        self.section_index += 1
        self._save_progress()
        self.add_event("章节确认", self.current_section_name)
        self.current_section = ""
        self.current_section_name = ""
        self.current_quality_report = None
        self.waiting_for = None
        self.phase = "selected"
        if self.section_index >= len(WRITING_STEPS):
            self._finish()

    def _finish(self) -> None:
        base_markdown = _assemble_document(self._selected_candidate(), self.accepted_sections)
        try:
            self.citation_report = build_citation_snapshot(
                self.active_documents or self.documents,
                self._external(),
                self.search_topic,
            )
        except Exception as exc:  # noqa: BLE001 - citations must not block final export
            self.citation_report = {
                "knowledge": [],
                "external": [],
                "notes": [f"引用快照生成失败：{type(exc).__name__}: {exc}"],
            }
            self.add_event("引用快照生成失败", f"{type(exc).__name__}: {exc}")

        self.final_markdown = append_citation_section(base_markdown, self.citation_report)
        try:
            self.final_quality_report = review_document(
                self.final_markdown,
                evidence_text=self.material_text + "\n" + _format_search_results(self._external()),
            ).to_dict()
        except Exception as exc:  # noqa: BLE001 - quality review must not block final export
            self.final_quality_report = {
                "score": 0,
                "passed": False,
                "issues": [
                    {
                        "message": "最终质量检查失败",
                        "repair": f"{type(exc).__name__}: {exc}",
                    }
                ],
            }
            self.add_event("最终质量检查失败", f"{type(exc).__name__}: {exc}")

        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.output_path = self.output_dir / "interactive_patent_draft.md"
        result_path = self.output_dir / "result.md"
        self.docx_path = self.output_dir / "technical_disclosure.docx"
        self.output_path.write_text(self.final_markdown, encoding="utf-8")
        result_path.write_text(self.final_markdown, encoding="utf-8")
        try:
            export_markdown_to_docx(self.final_markdown, self.docx_path)
        except Exception as exc:  # noqa: BLE001 - markdown files should still be downloadable
            self.add_event("Word 导出失败", f"{type(exc).__name__}: {exc}")
            self.docx_path = None

        self.waiting_for = None
        self.phase = "done"
        quality_score = (self.final_quality_report or {}).get("score", 0)
        quality_status = "通过" if (self.final_quality_report or {}).get("passed") else "存在待处理项"
        self.add_event("最终质量检查", f"{quality_score}/100，{quality_status}。")
        saved = f"已保存 {self.output_path} 和 {result_path}"
        if self.docx_path:
            saved += f"，并导出 {self.docx_path}"
        self.add_event("保存结果", f"{saved}。")
        self.add_interaction(
            "complete",
            "完成并保存专利文档",
            {
                "title": self._selected_candidate().title,
                "final_quality": self.final_quality_report,
                "citations": self.citation_report,
                "artifacts": {
                    "draft": self._output_url(self.output_path),
                    "result": self._output_url(result_path),
                    "docx": self._output_url(self.docx_path),
                },
            },
        )

        try:
            memory_item = summarize_candidate_for_memory(
                self._selected_candidate(),
                fallback_topic=self.search_topic or self._selected_knowledge_base_name(),
            )
            self.compact_patent_memory_result = write_compact_patent_memory(
                title=memory_item["title"],
                topic=memory_item["topic"],
                idea=memory_item["idea"],
            )
        except Exception as exc:  # noqa: BLE001 - compact memory must not block downloads/history
            self.compact_patent_memory_result = {
                "saved": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
            self.add_event("精简记忆保存失败", f"{type(exc).__name__}: {exc}")

        try:
            self.history_record = save_history_record(self)
            self.add_event("历史记录已保存", self.history_record.get("detail_url", ""))
        except Exception as exc:  # noqa: BLE001 - final files should remain downloadable
            self.add_event("历史记录保存失败", f"{type(exc).__name__}: {exc}")
            self.error = f"历史记录保存失败：{type(exc).__name__}: {exc}"

    def _review_current_section(self) -> None:
        report = review_section(
            self.current_section_name,
            self.current_section,
            accepted_sections=self.accepted_sections,
            evidence_text=self.material_text + "\n" + _format_search_results(self._external()),
        )
        self.current_quality_report = report.to_dict()
        status = "通过" if report.passed else f"{len(report.issues)} 个待处理项"
        self.add_event("章节质量检查", f"{self.current_section_name}：{report.score}/100，{status}。")

    def _section_interaction_payload(
        self,
        action: str,
        instruction: str = "",
    ) -> dict[str, Any]:
        return {
            "action": action,
            "section_index": self.section_index,
            "section_name": self.current_section_name,
            "content": self.current_section,
            "instruction": instruction,
            "quality": self.current_quality_report,
        }

    def _knowledge_documents_json(self) -> list[dict[str, Any]]:
        return [
            {
                "file_path": row.get("file_path", "未知"),
                "id": _document_identifier(row),
                "status": row.get("status", "未知"),
                "chunks_count": row.get("chunks_count", 0),
                "content_summary": row.get("content_summary", ""),
            }
            for row in _flatten_documents(self.documents or {})
        ]

    def _active_knowledge_documents_json(self) -> list[dict[str, Any]]:
        source = self.active_documents if self.active_documents is not None else self.documents
        return [
            {
                "file_path": row.get("file_path", "未知"),
                "id": _document_identifier(row),
                "status": row.get("status", "未知"),
                "chunks_count": row.get("chunks_count", 0),
                "content_summary": row.get("content_summary", ""),
            }
            for row in _flatten_documents(source or {})
        ]

    def _knowledge_graph_material_text(self) -> str:
        if not self.knowledge_graph:
            return "暂无知识图谱。"
        return format_knowledge_graph_for_prompt(self.knowledge_graph)

    def _activate_knowledge_base_scope(self) -> None:
        all_documents = self._knowledge_documents_json()
        all_graph = self.full_knowledge_graph or {}
        all_option = {
            "id": "all",
            "name": "总知识库",
            "description": "使用当前 LightRAG 知识库的完整图谱，和旧版生成方式一致。",
            "documents": all_documents,
            "document_count": len(all_documents),
            "graph": all_graph,
            "node_count": len(all_graph.get("nodes", [])),
            "edge_count": len(all_graph.get("edges", [])),
        }
        if self.knowledge_base_id == "all":
            selected = all_option
        else:
            registered = require_knowledge_base(self.knowledge_base_id)
            selected = {
                **registered,
                "documents": all_documents,
                "document_count": len(all_documents),
                "graph": all_graph,
                "node_count": len(all_graph.get("nodes", [])),
                "edge_count": len(all_graph.get("edges", [])),
            }
        self.selected_knowledge_base = {
            key: selected.get(key)
            for key in ("id", "name", "description", "document_count", "node_count", "edge_count")
        }
        graph = selected.get("graph")
        self.knowledge_graph = graph if isinstance(graph, dict) else all_graph
        selected_documents = selected.get("documents")
        if not isinstance(selected_documents, list):
            selected_documents = all_documents
        self.active_documents = _documents_from_items(selected_documents, (self.documents or {}).get("_counts", {}))

    def _selected_knowledge_base_name(self) -> str:
        return str((self.selected_knowledge_base or {}).get("name") or "总知识库")


    def _save_progress(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        (self.output_dir / "interactive_patent_draft.md").write_text(
            "\n\n".join(self.accepted_sections),
            encoding="utf-8",
        )

    def _documents(self) -> dict[str, Any]:
        if self.documents is None:
            raise ValueError("知识库尚未读取。")
        return self.documents

    def _active_documents(self) -> dict[str, Any]:
        if self.active_documents is not None:
            return self.active_documents
        return self._documents()

    def _external(self) -> ExternalSearchResult:
        if self.external is None:
            raise ValueError("外部检索尚未完成。")
        return self.external

    def _next_supplement_search_topic(self) -> str:
        failed_dimensions = [
            str(dimension.get("name", ""))
            for dimension in (self.assessment.dimensions or [])
            if not dimension.get("passed")
        ] if self.assessment else []
        base = self.base_search_topic or self.search_topic or _infer_search_topic(self.config, self.material_text)
        query_templates = [
            f"{base} 实施例 实验 数据 指标 效果",
            f"{base} 技术方案 流程 算法 控制 方法",
            f"{base} 现有技术 缺陷 痛点 改进",
            f"{base} 专利 权利要求 CN Google Patents",
            f"{base} 专利 申请 公开号",
            f"{base} patent claims prior art",
            f"{base} site:patents.google.com",
        ]
        if any("实施例" in name or "效果" in name for name in failed_dimensions):
            preferred = query_templates[0]
        elif any("技术方案" in name for name in failed_dimensions):
            preferred = query_templates[1]
        elif any("创新" in name or "问题" in name for name in failed_dimensions):
            preferred = query_templates[2]
        elif any("专利" in name or "避重" in name for name in failed_dimensions):
            preferred = query_templates[3]
        else:
            preferred = query_templates[self.search_round % len(query_templates)]
        if self.search_round < len(query_templates):
            return query_templates[self.search_round]
        return preferred

    @staticmethod
    def _merge_external_results(
        existing: ExternalSearchResult,
        supplement: ExternalSearchResult,
    ) -> ExternalSearchResult:
        seen: set[str] = set()
        merged_results: list[dict[str, str]] = []
        for result in [*existing.results, *supplement.results]:
            key = result.get("url") or result.get("title") or str(result)
            if key in seen:
                continue
            seen.add(key)
            merged_results.append(result)
        return ExternalSearchResult(
            enabled=existing.enabled or supplement.enabled,
            notes=[*existing.notes, *supplement.notes],
            results=merged_results,
        )

    def _assessment(self) -> MaterialAssessment:
        if self.assessment is None:
            raise ValueError("素材评估尚未完成。")
        return self.assessment

    def _selected_candidate(self) -> PatentCandidate:
        if self.selected_candidate is None:
            raise ValueError("候选专利尚未选择。")
        return self.selected_candidate

    @staticmethod
    def _assessment_json(assessment: MaterialAssessment | None) -> dict[str, Any] | None:
        return asdict(assessment) if assessment else None

    @staticmethod
    def _assessment_line(assessment: MaterialAssessment) -> str:
        return f"{assessment.score}/100，{assessment.level}"

    @staticmethod
    def _materials_ready(assessment: MaterialAssessment) -> bool:
        return assessment.score >= MATERIAL_READY_SCORE and not assessment.needs_external_search

    def _last_confirmed_section_json(self) -> dict[str, Any] | None:
        if not self.accepted_sections or self.section_index <= 0:
            return None
        name = WRITING_STEPS[min(self.section_index - 1, len(WRITING_STEPS) - 1)][0]
        return {
            "index": self.section_index - 1,
            "total": len(WRITING_STEPS),
            "name": name,
            "content": self.accepted_sections[-1],
        }

    def _next_section_name(self) -> str | None:
        if self.section_index >= len(WRITING_STEPS):
            return None
        return WRITING_STEPS[self.section_index][0]

    @staticmethod
    def _output_url(path: Path | None) -> str | None:
        if not path:
            return None
        try:
            relative = path.relative_to(OUTPUT_DIR)
        except ValueError:
            return None
        return f"/outputs/{relative.as_posix()}"


def get_run(run_id: str) -> WebPatentRun:
    with RUNS_LOCK:
        run = RUNS.get(run_id)
    if not run:
        abort(404)
    return run


def get_feishu_manager() -> Any:
    """Create the Feishu adapter lazily to avoid import cycles during startup."""

    global _FEISHU_MANAGER
    if _FEISHU_MANAGER is not None:
        return _FEISHU_MANAGER
    with _FEISHU_MANAGER_LOCK:
        if _FEISHU_MANAGER is None:
            from feishu_agent import FeishuPatentAgent
            from feishu_integration import FeishuIntegrationManager, FeishuStateStore

            store = FeishuStateStore()
            agent = FeishuPatentAgent(store)
            manager = FeishuIntegrationManager(agent.handle, agent.start_scheduled)
            manager.store = store
            _FEISHU_MANAGER = manager
    return _FEISHU_MANAGER


def start_background_services() -> None:
    get_feishu_manager().start()


@app.route("/")
def index() -> str:
    return render_template("index.html")


@app.route("/history")
def history_page() -> str:
    return render_template("history.html")


@app.route("/history/<record_id>")
def history_detail_page(record_id: str) -> str:
    if not load_history_record(record_id):
        abort(404)
    return render_template("history_detail.html", record_id=record_id)


@app.route("/settings")
def settings_page() -> str:
    return render_template("settings.html")


@app.route("/run/<run_id>")
def run_page(run_id: str) -> str:
    get_run(run_id)
    return render_template("workflow.html", run_id=run_id)


@register_tool("read_compact_patent_memory", "读取精简专利生成记忆，供候选 idea 避免重复。", "Memory")
def read_compact_patent_memory() -> list[dict[str, str]]:
    return load_patent_memory()


@register_tool("write_compact_patent_memory", "保存本次最终专利的精简标题、主题和 idea。", "Memory")
def write_compact_patent_memory(title: str, topic: str, idea: str) -> dict[str, Any]:
    return append_patent_memory(title=title, topic=topic, idea=idea)


@app.get("/api/knowledge")
def api_knowledge() -> Any:
    config = load_config()
    client = make_client(config)
    documents = _load_documents(client)
    rows = _flatten_documents(documents)
    graph = build_knowledge_graph(client, documents)
    document_items = [
        {
            "file_path": row.get("file_path", "未知"),
            "id": _document_identifier(row),
            "status": row.get("status", "未知"),
            "chunks_count": row.get("chunks_count", 0),
            "content_summary": row.get("content_summary", ""),
        }
        for row in rows
    ]
    return jsonify(
        {
            "counts": documents.get("_counts", {}),
            "graph": graph,
            "knowledge_bases": load_isolated_knowledge_bases(config),
            "knowledge_base_catalog": list_knowledge_base_catalog(),
            "documents": document_items,
            "lightrag_graph_url": lightrag_graph_webui_url(config.lightrag_base_url),
            "kb_manager": kb_manager_status(config),
        }
    )


@app.post("/api/knowledge/upload")
@register_tool("upload_knowledge_document", "上传素材文件并触发 LightRAG 扫描处理。", "Knowledge management")
def api_upload_knowledge() -> Any:
    upload = request.files.get("file")
    if not upload or not upload.filename:
        raise ValueError("请选择要上传的文件。")

    target_mode = str(request.form.get("knowledge_base_mode") or "").strip()
    config = load_config()
    provisioned: dict[str, Any] | None = None
    if target_mode == "new":
        name = str(request.form.get("new_knowledge_base_name") or "").strip()
        description = str(request.form.get("new_knowledge_base_description") or "").strip()
        requested_base_url = str(request.form.get("new_knowledge_base_base_url") or "")
        manager = make_kb_manager(config)
        if manager is not None:
            provisioned = manager.create_knowledge_base(name, description)
            requested_base_url = str(provisioned.get("base_url") or "")
            if not requested_base_url:
                raise KnowledgeBaseManagerError("管理服务未返回 LightRAG API 地址。")
        elif not requested_base_url.strip():
            raise ValueError("自动知识库管理服务尚未配置，请由管理员填写独立 LightRAG API 地址。")
        if LightRAGClient._normalize_base_url(requested_base_url) == LightRAGClient._normalize_base_url(config.lightrag_base_url):
            raise ValueError("新知识库不能复用总知识库地址。")
        try:
            target = create_knowledge_base(
                name,
                description,
                requested_base_url,
                manager_instance_id=str((provisioned or {}).get("id") or ""),
            )
        except Exception:
            if provisioned and manager is not None and provisioned.get("id"):
                try:
                    manager.delete_knowledge_base(str(provisioned["id"]))
                except KnowledgeBaseManagerError:
                    pass
            raise
    elif target_mode == "existing":
        target = require_knowledge_base(str(request.form.get("knowledge_base_id") or ""))
    else:
        raise ValueError("请选择存入已有知识库，或新建一个知识库。")

    client = client_for_knowledge_base(config, target["id"])
    upload_result = client.upload_document(upload.stream, upload.filename)
    scan_result: Any | None = None
    scan_error: str | None = None
    try:
        scan_result = client.scan_documents()
    except LightRAGClientError as exc:
        scan_error = str(exc)

    refreshed_documents = _load_documents(client)
    graph = build_knowledge_graph(client, refreshed_documents)
    rows = _flatten_documents(refreshed_documents)
    document_items = [
        {
            "file_path": row.get("file_path", "未知"),
            "id": _document_identifier(row),
            "status": row.get("status", "未知"),
            "chunks_count": row.get("chunks_count", 0),
            "content_summary": row.get("content_summary", ""),
        }
        for row in rows
    ]
    return jsonify(
        {
            "ok": True,
            "filename": upload.filename,
            "knowledge_base": target,
            "provisioned": provisioned,
            "upload_result": upload_result,
            "scan_result": scan_result,
            "scan_error": scan_error,
            "graph": graph,
            "knowledge_bases": load_isolated_knowledge_bases(config),
            "knowledge_base_catalog": list_knowledge_base_catalog(),
        }
    )


@app.patch("/api/knowledge/bases/<knowledge_base_id>")
def api_bind_knowledge_base_instance(knowledge_base_id: str) -> Any:
    payload = request.get_json(silent=True) or {}
    config = load_config()
    requested_base_url = str(payload.get("base_url") or "")
    if LightRAGClient._normalize_base_url(requested_base_url) == LightRAGClient._normalize_base_url(config.lightrag_base_url):
        raise ValueError("该地址是总知识库地址，请为物理隔离知识库部署另一个 LightRAG workspace/实例。")
    item = update_knowledge_base_instance(knowledge_base_id, requested_base_url)
    client = client_for_knowledge_base(config, knowledge_base_id)
    client.get_status_counts()
    return jsonify({"ok": True, "knowledge_base": item})


@app.get("/api/knowledge/manager/status")
def api_knowledge_manager_status() -> Any:
    return jsonify(kb_manager_status(load_config()))


@app.delete("/api/knowledge/bases/<knowledge_base_id>")
def api_delete_managed_knowledge_base(knowledge_base_id: str) -> Any:
    payload = request.get_json(silent=True) or {}
    item = require_knowledge_base(knowledge_base_id)
    if str(payload.get("confirm_name") or "").strip() != item["name"]:
        raise ValueError("请输入完整知识库名称确认删除。")
    manager_instance_id = str(item.get("manager_instance_id") or "")
    if not manager_instance_id:
        raise ValueError("该知识库不是由应用自动创建的实例，请由服务器管理员处理。")
    manager = make_kb_manager(load_config())
    if manager is None:
        raise ValueError("自动知识库管理服务未配置，无法删除实例。")
    result = manager.delete_knowledge_base(manager_instance_id)
    deleted = delete_knowledge_base_registration(knowledge_base_id)
    return jsonify({"ok": True, "knowledge_base": deleted, "manager_result": result})


@app.delete("/api/knowledge")
@register_tool("clear_knowledge_base", "清空 LightRAG 当前知识库文档。", "Knowledge management")
def api_clear_knowledge() -> Any:
    config = load_config()
    payload = request.get_json(silent=True) or {}
    knowledge_base_id = str(payload.get("knowledge_base_id") or "all")
    client = client_for_knowledge_base(config, knowledge_base_id)
    result = client.clear_documents()
    documents = _load_documents(client)
    graph = build_knowledge_graph(client, documents)
    rows = _flatten_documents(documents)
    document_items = [
        {
            "file_path": row.get("file_path", "未知"),
            "id": _document_identifier(row),
            "status": row.get("status", "未知"),
            "chunks_count": row.get("chunks_count", 0),
            "content_summary": row.get("content_summary", ""),
        }
        for row in rows
    ]
    return jsonify(
        {
            "ok": True,
            "result": result,
            "graph": graph,
            "knowledge_bases": load_isolated_knowledge_bases(config),
            "knowledge_base_catalog": list_knowledge_base_catalog(),
        }
    )


@app.delete("/api/knowledge/documents")
@register_tool("delete_knowledge_documents", "按文档 ID 删除选中的知识库素材。", "Knowledge management")
def api_delete_knowledge_documents() -> Any:
    payload = request.get_json(silent=True) or {}
    doc_ids = payload.get("doc_ids") or []
    if not isinstance(doc_ids, list):
        raise ValueError("doc_ids 必须是列表。")
    clean_ids = [str(doc_id).strip() for doc_id in doc_ids if str(doc_id).strip()]
    if not clean_ids:
        raise ValueError("请选择至少一个要删除的文档。")

    config = load_config()
    knowledge_base_id = str(payload.get("knowledge_base_id") or "all")
    client = client_for_knowledge_base(config, knowledge_base_id)
    documents_before_delete = _load_documents(client)
    rows_before_delete = _flatten_documents(documents_before_delete)
    deleted_documents = [row for row in rows_before_delete if _document_identifier(row) in clean_ids]
    result = client.delete_documents(
        clean_ids,
        delete_file=bool(payload.get("delete_file", True)),
        delete_llm_cache=bool(payload.get("delete_llm_cache", False)),
    )
    documents = _load_documents(client)
    graph = build_knowledge_graph(client, documents)
    rows = _flatten_documents(documents)
    document_items = [
        {
            "file_path": row.get("file_path", "未知"),
            "id": _document_identifier(row),
            "status": row.get("status", "未知"),
            "chunks_count": row.get("chunks_count", 0),
            "content_summary": row.get("content_summary", ""),
        }
        for row in rows
    ]
    return jsonify(
        {
            "ok": True,
            "deleted": clean_ids,
            "result": result,
            "graph": graph,
            "knowledge_bases": load_isolated_knowledge_bases(config),
            "knowledge_base_catalog": list_knowledge_base_catalog(),
        }
    )


@app.post("/api/runs")
def api_create_run() -> Any:
    payload = request.get_json(silent=True) or {}
    innovation_level = normalize_innovation_level(payload.get("innovation_level", payload.get("innovation_index", "medium")))
    knowledge_base_id = str(payload.get("knowledge_base_id") or "all").strip() or "all"
    config = load_config()
    channel = str(payload.get("channel") or "web").strip() or "web"
    run = WebPatentRun(
        config,
        innovation_level=innovation_level,
        knowledge_base_id=knowledge_base_id,
        channel=channel,
    )
    if channel == "feishu":
        run.add_interaction(
            "channel",
            "通过飞书开始生成",
            {
                "channel": channel,
                "knowledge_base_id": knowledge_base_id,
                "innovation_level": innovation_level,
            },
        )
    with RUNS_LOCK:
        RUNS[run.id] = run
    return jsonify({"run_id": run.id, "url": f"/run/{run.id}"})


@app.get("/api/runs")
def api_list_runs() -> Any:
    with RUNS_LOCK:
        runs = list(RUNS.values())
    return jsonify(
        {
            "runs": [
                {
                    "run_id": run.id,
                    "created_at": run.created_at,
                    "phase": run.phase,
                    "waiting_for": run.waiting_for,
                    "done": run.phase == "done",
                    "error": run.error,
                    "knowledge_base": run.selected_knowledge_base,
                    "innovation_level": run.innovation_level,
                    "candidate_count": len(run.candidates),
                }
                for run in reversed(runs)
            ]
        }
    )


@app.get("/api/runs/<run_id>")
def api_get_run(run_id: str) -> Any:
    return jsonify(get_run(run_id).snapshot())


@app.delete("/api/runs/<run_id>")
def api_delete_run(run_id: str) -> Any:
    with RUNS_LOCK:
        removed = RUNS.pop(run_id, None)
    if removed is None:
        abort(404)
    return jsonify({"ok": True, "run_id": run_id})


@app.post("/api/runs/<run_id>/advance")
def api_advance(run_id: str) -> Any:
    run = get_run(run_id)
    run.advance()
    return jsonify(run.snapshot())


@app.post("/api/runs/<run_id>/retry-search")
def api_retry_search(run_id: str) -> Any:
    run = get_run(run_id)
    run.retry_external_search()
    return jsonify(run.snapshot())


@app.post("/api/runs/<run_id>/select")
def api_select(run_id: str) -> Any:
    payload = request.get_json(silent=True) or {}
    index = payload.get("index")
    run = get_run(run_id)
    run.select_candidate(
        int(index) if index not in (None, "") else None,
        payload.get("custom_title"),
    )
    return jsonify(run.snapshot())


@app.post("/api/runs/<run_id>/section")
def api_section(run_id: str) -> Any:
    payload = request.get_json(silent=True) or {}
    run = get_run(run_id)
    run.handle_section_action(
        action=str(payload.get("action", "")),
        instruction=str(payload.get("instruction", "")),
        content=str(payload.get("content", "")),
    )
    return jsonify(run.snapshot())


@app.post("/api/runs/<run_id>/material")
def api_material(run_id: str) -> Any:
    run = get_run(run_id)
    payload = request.get_json(silent=True) or {}
    run.handle_material_action(str(payload.get("action", "refresh")))
    return jsonify(run.snapshot())


@app.get("/api/history")
def api_history() -> Any:
    return jsonify({"records": load_history_records()[:MAX_HISTORY_ITEMS]})


@app.get("/api/history/<record_id>")
def api_history_detail(record_id: str) -> Any:
    record = load_history_record(record_id)
    if not record:
        abort(404)
    return jsonify(record)


@app.get("/api/settings")
def api_settings() -> Any:
    skills = load_agent_skills(resource_root())
    config = load_config()
    config_view = user_config_view()
    from patent_agent_bridge import TOOLS as integration_tools

    feishu_manager = get_feishu_manager()
    tool_rows = [tool.to_dict() for tool in registered_tools()]
    tool_rows.extend(
        {
            "name": tool.name,
            "description": tool.description,
            "category": "Desktop AI integration",
            "owner": "patent_agent_bridge",
        }
        for tool in integration_tools
    )
    return jsonify(
        {
            "agent_core": config.agent_core,
            "internal_llm": _internal_llm_label(config),
            "user_config": {
                "path": config_view.path,
                "values": config_view.values,
                "secrets": config_view.secrets,
            },
            "skills": [
                {
                    "name": skill.name,
                    "description": skill.description,
                    "path": _relative_resource_path(skill.path),
                }
                for skill in skills
            ],
            "tools": tool_rows,
            "feishu": {
                "status": feishu_manager.status(),
                "schedules": feishu_manager.schedules(),
            },
        }
    )


@app.post("/api/settings/runtime")
def api_save_runtime_settings() -> Any:
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        raise ValueError("配置内容必须是 JSON 对象。")
    config_view = save_user_config(payload)
    config = load_config()
    get_feishu_manager().refresh()
    return jsonify(
        {
            "ok": True,
            "agent_core": config.agent_core,
            "internal_llm": _internal_llm_label(config),
            "user_config": {
                "path": config_view.path,
                "values": config_view.values,
                "secrets": config_view.secrets,
            },
        }
    )


@app.get("/api/feishu/status")
def api_feishu_status() -> Any:
    manager = get_feishu_manager()
    return jsonify({"status": manager.status(), "schedules": manager.schedules()})


@app.post("/api/feishu/schedules")
def api_feishu_create_schedule() -> Any:
    payload = request.get_json(silent=True) or {}
    return jsonify({"ok": True, "schedule": get_feishu_manager().save_schedule(payload)})


@app.put("/api/feishu/schedules/<schedule_id>")
def api_feishu_update_schedule(schedule_id: str) -> Any:
    payload = request.get_json(silent=True) or {}
    return jsonify({"ok": True, "schedule": get_feishu_manager().save_schedule(payload, schedule_id)})


@app.delete("/api/feishu/schedules/<schedule_id>")
def api_feishu_delete_schedule(schedule_id: str) -> Any:
    if not get_feishu_manager().delete_schedule(schedule_id):
        abort(404)
    return jsonify({"ok": True})


@app.post("/api/feishu/test")
def api_feishu_test() -> Any:
    payload = request.get_json(silent=True) or {}
    get_feishu_manager().send_test(
        str(payload.get("target_type") or "group"),
        str(payload.get("target_id") or "").strip(),
        str(payload.get("text") or "飞书机器人连接测试成功。"),
    )
    return jsonify({"ok": True})


def _internal_llm_label(config: AppConfig) -> str:
    if config.agent_core in {"pi", "pi_coding", "pi_coding_agent", "pi-coding-agent"}:
        details = "/".join(part for part in (config.pi_provider, config.pi_model) if part)
        return f"Pi Agent / {details}" if details else "Pi Agent / 使用本机默认配置"
    if config.agent_core in {"codex", "codex_cli", "codex-cli"}:
        return f"codex/{config.codex_model or 'default'}"
    if config.llm_provider and config.llm_provider != "none":
        return f"{config.llm_provider}/{config.llm_model or 'default'}"
    return "未配置独立 LLM"


def _relative_resource_path(path: Path) -> str:
    try:
        return str(path.relative_to(resource_root()))
    except ValueError:
        return str(path)


@app.route("/outputs/<path:filename>")
def output_file(filename: str) -> Any:
    as_download = Path(filename).suffix.lower() in {".docx", ".xlsx", ".md"}
    return send_from_directory(OUTPUT_DIR, filename, as_attachment=as_download)


def _document_identifier(row: dict[str, Any]) -> str:
    for key in ("id", "doc_id", "document_id", "file_id"):
        value = row.get(key)
        if value:
            return str(value)
    return str(row.get("file_path") or row.get("filename") or "")


def load_history_records() -> list[dict[str, Any]]:
    if not HISTORY_INDEX.exists():
        return []
    try:
        data = json.loads(HISTORY_INDEX.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def load_history_record(record_id: str) -> dict[str, Any] | None:
    safe_id = Path(record_id).name
    detail_path = HISTORY_DIR / safe_id / "record.json"
    if detail_path.exists():
        try:
            data = json.loads(detail_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return _with_material_strategy(data) if isinstance(data, dict) else None
    summary = next((item for item in load_history_records() if item.get("id") == safe_id), None)
    return _with_material_strategy(summary) if summary else None


def _with_material_strategy(record: dict[str, Any]) -> dict[str, Any]:
    if record.get("material_strategy") and record["material_strategy"].get("fusion_blueprint") and record["material_strategy"].get("graph_fusion"):
        return record
    knowledge = record.get("knowledge") or {}
    external = record.get("external") or {}
    candidates = [
        PatentCandidate(
            title=str(item.get("title") or "未命名候选"),
            summary=str(item.get("summary") or ""),
            raw=str(item.get("raw") or ""),
        )
        for item in (record.get("candidates") or [])
        if isinstance(item, dict)
    ]
    record["material_strategy"] = build_material_strategy(
        documents=knowledge.get("documents") or [],
        external=ExternalSearchResult(
            enabled=True,
            notes=external.get("notes") or [],
            results=external.get("results") or [],
        ),
        candidates=candidates,
        knowledge_graph=knowledge.get("graph") if isinstance(knowledge.get("graph"), dict) else None,
        innovation_index=_int_between(record.get("innovation_index"), 50, 0, 100),
        innovation_level=normalize_innovation_level(record.get("innovation_level", record.get("innovation_index", "medium"))),
    )
    return record


@register_tool("save_generation_history", "保存最近十次生成记录及完整交互过程快照。", "History")
def save_history_record(run: WebPatentRun) -> dict[str, Any]:
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    completed_at = datetime.now().isoformat(timespec="seconds")
    record_id = f"{completed_at.replace(':', '').replace('-', '').replace('T', '_')}_{run.id}"
    record_dir = HISTORY_DIR / record_id
    record_dir.mkdir(parents=True, exist_ok=True)

    artifacts: dict[str, str | None] = {
        "draft": _copy_history_artifact(run.output_path, record_dir, "interactive_patent_draft.md"),
        "result": _copy_history_artifact(run.output_dir / "result.md", record_dir, "result.md"),
        "docx": _copy_history_artifact(run.docx_path, record_dir, "technical_disclosure.docx"),
        "similarity_markdown": _copy_history_artifact(run.similarity_markdown, record_dir, "similar_patent_analysis.md"),
        "similarity_xlsx": _copy_history_artifact(run.similarity_xlsx, record_dir, "similar_patent_analysis.xlsx"),
    }
    token_usage = run.token_usage.to_dict()
    (record_dir / "token_usage_report.md").write_text(
        markdown_report(token_usage),
        encoding="utf-8",
    )
    artifacts["token_usage_report"] = f"/outputs/history/{record_dir.name}/token_usage_report.md"
    record = {
        "id": record_id,
        "run_id": run.id,
        "channel": run.channel,
        "detail_url": f"/history/{record_id}",
        "created_at": run.created_at,
        "completed_at": completed_at,
        "title": run.selected_candidate.title if run.selected_candidate else "未命名专利方案",
        "agent_core": run.config.agent_core,
        "innovation_index": run.innovation_index,
        "innovation_level": run.innovation_level,
        "innovation_level_label": innovation_level_label(run.innovation_level),
        "knowledge_base_id": run.knowledge_base_id,
        "selected_knowledge_base": run.selected_knowledge_base,
        "assessment": asdict(run.assessment) if run.assessment else None,
        "initial_assessment": asdict(run.initial_assessment) if run.initial_assessment else None,
        "search_topic": run.search_topic,
        "candidate_count": len(run.candidates),
        "external_count": len(run.external.results) if run.external else 0,
        "artifacts": artifacts,
        "events": run.events,
        "interactions": run.interactions,
        "skills": [
            {
                "name": skill.name,
                "description": skill.description,
                "path": str(skill.path),
            }
            for skill in run.skills
        ],
        "tools": [tool.to_dict() for tool in registered_tools()],
        "knowledge": {
            "counts": (run.documents or {}).get("_counts", {}),
            "graph": run.knowledge_graph,
            "full_graph": run.full_knowledge_graph,
            "knowledge_bases": run.knowledge_bases,
            "selected_knowledge_base": run.selected_knowledge_base,
            "documents": run._active_knowledge_documents_json(),
            "all_documents": run._knowledge_documents_json(),
        },
        "external": {
            "topic": run.search_topic,
            "notes": run.external.notes if run.external else [],
            "results": run.external.results if run.external else [],
        },
        "material_strategy": run.material_strategy,
        "citations": run.citation_report,
        "candidates": [asdict(item) for item in run.candidates],
        "selected_candidate": asdict(run.selected_candidate) if run.selected_candidate else None,
        "accepted_sections": [
            {
                "index": index,
                "name": WRITING_STEPS[index][0] if index < len(WRITING_STEPS) else f"章节{index + 1}",
                "content": content,
            }
            for index, content in enumerate(run.accepted_sections)
        ],
        "final_quality": run.final_quality_report,
        "token_usage": token_usage,
    }
    (record_dir / "record.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    summary = {
        key: record[key]
        for key in (
            "id",
            "run_id",
            "channel",
            "created_at",
            "completed_at",
            "title",
            "agent_core",
            "innovation_index",
            "innovation_level",
            "innovation_level_label",
            "knowledge_base_id",
            "selected_knowledge_base",
            "assessment",
            "initial_assessment",
            "search_topic",
            "candidate_count",
            "external_count",
            "artifacts",
            "final_quality",
        )
    }
    summary["token_usage_summary"] = token_usage.get("summary", {})
    summary["detail_url"] = f"/history/{record_id}"
    records = [summary, *[item for item in load_history_records() if item.get("id") != record_id]]
    HISTORY_INDEX.write_text(
        json.dumps(records[:MAX_HISTORY_ITEMS], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return record


def _copy_history_artifact(source: Path | None, target_dir: Path, filename: str) -> str | None:
    if not source or not source.exists():
        return None
    target = target_dir / filename
    shutil.copy2(source, target)
    return f"/outputs/history/{target_dir.name}/{filename}"


def _int_between(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _documents_from_items(items: list[dict[str, Any]], counts: dict[str, Any] | None = None) -> dict[str, Any]:
    statuses: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "processed")
        row = dict(item)
        row.pop("status", None)
        statuses.setdefault(status, []).append(row)
    return {
        "statuses": statuses,
        "_counts": counts or {},
    }


def find_available_port(host: str = "127.0.0.1", preferred: int = 5000) -> int:
    for port in range(preferred, preferred + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind((host, port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"无法在 {preferred}-{preferred + 49} 范围内找到可用端口。")


if __name__ == "__main__":
    host = os.getenv("WEB_HOST", "127.0.0.1")
    preferred_port = int(os.getenv("WEB_PORT", "5000"))
    port = find_available_port(host=host, preferred=preferred_port)
    if port != preferred_port:
        print(f"Port {preferred_port} is busy, using http://{host}:{port}")
    start_background_services()
    app.run(host=host, port=port, debug=False)

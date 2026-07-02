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
from uuid import uuid4
from typing import Any

from dotenv import load_dotenv
from flask import Flask, abort, jsonify, render_template, request, send_from_directory

from agent_skill_loader import format_skills_for_prompt, load_agent_skills
from config import AppConfig, load_config
from citation_report import append_citation_section, build_citation_snapshot
from docx_exporter import export_markdown_to_docx
from external_search import ExternalSearchResult, search_external_materials
from formula_utils import normalize_formula_markdown
from lightrag_client import LightRAGClient, LightRAGClientError
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
    _summarize_documents,
)
from patent_quality_tool import review_document, review_section
from similar_patent_analysis import generate_similar_patent_analysis
from tool_registry import register_tool, registered_tools


OUTPUT_DIR = Path("outputs")
HISTORY_DIR = OUTPUT_DIR / "history"
HISTORY_INDEX = HISTORY_DIR / "index.json"
MAX_HISTORY_ITEMS = 10
MATERIAL_READY_SCORE = 80

load_dotenv()
app = Flask(__name__)
RUNS: dict[str, "WebPatentRun"] = {}
RUNS_LOCK = Lock()


@app.errorhandler(ValueError)
def handle_value_error(exc: ValueError) -> Any:
    return jsonify({"error": str(exc)}), 400


@app.errorhandler(LightRAGClientError)
def handle_lightrag_error(exc: LightRAGClientError) -> Any:
    return jsonify({"error": str(exc)}), 502


def make_client(config: AppConfig) -> LightRAGClient:
    return LightRAGClient(
        base_url=config.lightrag_base_url,
        api_key=config.lightrag_api_key,
        query_mode=config.lightrag_query_mode,
        include_chunk_content=config.lightrag_include_chunk_content,
    )


class WebPatentRun:
    def __init__(self, config: AppConfig) -> None:
        self.id = uuid4().hex[:12]
        self.created_at = datetime.now().isoformat(timespec="seconds")
        self.config = config
        self.client = make_client(config)
        self.output_dir = OUTPUT_DIR
        self.skills = load_agent_skills(Path.cwd())
        self.skill_prompt = format_skills_for_prompt(self.skills)

        self.phase = "created"
        self.waiting_for: str | None = None
        self.error: str | None = None
        self.events: list[dict[str, str]] = []
        self.interactions: list[dict[str, Any]] = []

        self.documents: dict[str, Any] | None = None
        self.material_text = ""
        self.initial_assessment: MaterialAssessment | None = None
        self.assessment: MaterialAssessment | None = None
        self.search_topic = ""
        self.base_search_topic = ""
        self.search_round = 0
        self.external: ExternalSearchResult | None = None
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

    def advance(self) -> None:
        if self.waiting_for == "material":
            self.waiting_for = None
            self.phase = "searched"
            self.add_event("恢复自动检索", "已取消旧版素材暂停状态，继续自动补充检索。")
        if self.waiting_for or self.phase == "done":
            return

        try:
            if self.phase == "created":
                self.documents = _load_documents(self.client)
                self.material_text = _summarize_documents(self.documents)
                self.phase = "documents_loaded"
                self.add_event("读取知识库", "已读取文档列表、处理状态和摘要。")
                self.add_interaction(
                    "knowledge",
                    "读取知识库",
                    {
                        "counts": self.documents.get("_counts", {}),
                        "documents": self._knowledge_documents_json(),
                    },
                )
                return

            if self.phase == "documents_loaded":
                self.initial_assessment = _assess_materials(self._documents())
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
                self.assessment = _assess_materials(self._documents(), self.external)
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
                    return
                self.phase = "reassessed"
                return

            if self.phase == "reassessed":
                self.candidates = _generate_candidates(
                    self.config,
                    self.material_text,
                    self._assessment(),
                    self._external(),
                    skill_instructions=self.skill_prompt,
                )
                self.phase = "candidates_ready"
                self.add_event("候选专利方向", f"已生成 {len(self.candidates)} 个候选方向。")
                self.add_interaction(
                    "candidates",
                    "生成候选专利方向",
                    {"candidates": [asdict(item) for item in self.candidates]},
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
        self.material_text = _summarize_documents(self.documents)
        self.assessment = _assess_materials(self._documents(), self.external)
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

    def snapshot(self) -> dict[str, Any]:
        rows = _flatten_documents(self.documents or {})
        counts = (self.documents or {}).get("_counts", {})
        return {
            "id": self.id,
            "created_at": self.created_at,
            "phase": self.phase,
            "waiting_for": self.waiting_for,
            "error": self.error,
            "agent_core": self.config.agent_core,
            "events": self.events,
            "interactions": self.interactions,
            "skills": [{"name": skill.name, "path": str(skill.path)} for skill in self.skills],
            "knowledge": {
                "counts": counts,
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
            },
            "initial_assessment": self._assessment_json(self.initial_assessment),
            "assessment": self._assessment_json(self.assessment),
            "search_topic": self.search_topic,
            "search_round": self.search_round,
            "external": {
                "notes": self.external.notes if self.external else [],
                "results": self.external.results if self.external else [],
                "text": _format_search_results(self.external) if self.external else "",
            },
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
        self.citation_report = build_citation_snapshot(
            self.documents,
            self._external(),
            self.search_topic,
        )
        self.final_markdown = append_citation_section(
            _assemble_document(self._selected_candidate(), self.accepted_sections),
            self.citation_report,
        )
        self.final_quality_report = review_document(
            self.final_markdown,
            evidence_text=self.material_text + "\n" + _format_search_results(self._external()),
        ).to_dict()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.output_path = self.output_dir / "interactive_patent_draft.md"
        result_path = self.output_dir / "result.md"
        self.docx_path = self.output_dir / "technical_disclosure.docx"
        self.output_path.write_text(self.final_markdown, encoding="utf-8")
        result_path.write_text(self.final_markdown, encoding="utf-8")
        try:
            export_markdown_to_docx(self.final_markdown, self.docx_path)
        except RuntimeError as exc:
            self.add_event("Word 导出失败", str(exc))
            self.docx_path = None
        self.waiting_for = None
        self.phase = "done"
        quality_score = (self.final_quality_report or {}).get("score", 0)
        quality_status = "通过" if (self.final_quality_report or {}).get("passed") else "存在待处理项"
        self.add_event("最终质量检查", f"{quality_score}/100，{quality_status}。")
        saved = f"已保存 {self.output_path} 和 {result_path}"
        if self.docx_path:
            saved += f"，并导出 {self.docx_path}"
        self.add_event("保存结果", f"{saved}。历史记录已保存。")
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
        self.history_record = save_history_record(self)

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


@app.get("/api/knowledge")
def api_knowledge() -> Any:
    config = load_config()
    documents = _load_documents(make_client(config))
    rows = _flatten_documents(documents)
    return jsonify(
        {
            "counts": documents.get("_counts", {}),
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
        }
    )


@app.post("/api/knowledge/upload")
@register_tool("upload_knowledge_document", "上传素材文件并触发 LightRAG 扫描处理。", "Knowledge management")
def api_upload_knowledge() -> Any:
    upload = request.files.get("file")
    if not upload or not upload.filename:
        raise ValueError("请选择要上传的文件。")

    config = load_config()
    client = make_client(config)
    upload_result = client.upload_document(upload.stream, upload.filename)
    scan_result: Any | None = None
    scan_error: str | None = None
    try:
        scan_result = client.scan_documents()
    except LightRAGClientError as exc:
        scan_error = str(exc)

    return jsonify(
        {
            "ok": True,
            "filename": upload.filename,
            "upload_result": upload_result,
            "scan_result": scan_result,
            "scan_error": scan_error,
        }
    )


@app.delete("/api/knowledge")
@register_tool("clear_knowledge_base", "清空 LightRAG 当前知识库文档。", "Knowledge management")
def api_clear_knowledge() -> Any:
    config = load_config()
    result = make_client(config).clear_documents()
    return jsonify({"ok": True, "result": result})


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
    result = make_client(config).delete_documents(
        clean_ids,
        delete_file=bool(payload.get("delete_file", True)),
        delete_llm_cache=bool(payload.get("delete_llm_cache", False)),
    )
    return jsonify({"ok": True, "deleted": clean_ids, "result": result})


@app.post("/api/runs")
def api_create_run() -> Any:
    config = load_config()
    run = WebPatentRun(config)
    with RUNS_LOCK:
        RUNS[run.id] = run
    return jsonify({"run_id": run.id, "url": f"/run/{run.id}"})


@app.get("/api/runs/<run_id>")
def api_get_run(run_id: str) -> Any:
    return jsonify(get_run(run_id).snapshot())


@app.post("/api/runs/<run_id>/advance")
def api_advance(run_id: str) -> Any:
    run = get_run(run_id)
    run.advance()
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
    skills = load_agent_skills(Path.cwd())
    config = load_config()
    return jsonify(
        {
            "agent_core": config.agent_core,
            "skills": [
                {
                    "name": skill.name,
                    "description": skill.description,
                    "path": str(skill.path.relative_to(Path.cwd())),
                }
                for skill in skills
            ],
            "tools": [tool.to_dict() for tool in registered_tools()],
        }
    )


@app.route("/outputs/<path:filename>")
def output_file(filename: str) -> Any:
    as_download = Path(filename).suffix.lower() in {".docx", ".xlsx"}
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
        return data if isinstance(data, dict) else None
    return next((item for item in load_history_records() if item.get("id") == safe_id), None)


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
    record = {
        "id": record_id,
        "run_id": run.id,
        "created_at": run.created_at,
        "completed_at": completed_at,
        "title": run.selected_candidate.title if run.selected_candidate else "未命名专利方案",
        "agent_core": run.config.agent_core,
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
            "documents": run._knowledge_documents_json(),
        },
        "external": {
            "topic": run.search_topic,
            "notes": run.external.notes if run.external else [],
            "results": run.external.results if run.external else [],
        },
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
            "created_at",
            "completed_at",
            "title",
            "agent_core",
            "assessment",
            "initial_assessment",
            "search_topic",
            "candidate_count",
            "external_count",
            "artifacts",
            "final_quality",
        )
    }
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
    app.run(host=host, port=port, debug=False)

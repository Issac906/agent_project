"""Interactive patent discovery and drafting agent."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, Callable

from agent_skill_loader import LoadedSkill, format_skills_for_prompt, load_agent_skills
from config import AppConfig
from codex_cli_client import CodexCLIError, generate_text_with_codex_cli
from external_search import ExternalSearchResult, search_external_materials
from formula_utils import normalize_formula_markdown
from lightrag_client import LightRAGClient, LightRAGClientError
from llm_writer import LLMWriterError, generate_text_with_ollama
from pi_coding_agent_client import PiCodingAgentError, generate_text_with_pi_coding_agent
from patent_quality_tool import (
    QualityReport,
    apply_deterministic_fixes,
    repair_instructions,
    review_document,
    review_section,
    strip_process_meta,
)
from similar_patent_analysis import generate_similar_patent_analysis
from tool_registry import discover_bound_tools, register_tool


FINAL_DOCUMENT_STRUCTURE = [
    "标题页",
    "一、发明名称",
    "二、技术领域",
    "三、背景技术",
    "四、发明内容",
    "五、保护范围",
    "六、附图说明",
    "七、具体实施方式",
    "八、附图",
]


FINAL_FORMAT_GUIDE = """最终文案格式：
标题页：文档标题、文档类型、申请人/单位、发明人/作者、联系电话、邮箱。文档标题必须简短，只体现发明是什么；禁止使用“基于……的……”句式。
一、发明名称：用一句话给出完整技术名称，避免把全部技术细节堆进标题；禁止使用“基于……的……”句式。
二、技术领域：所属行业领域、所属技术方向、核心方法/系统。
三、背景技术：围绕本发明要解决的行业问题写，控制在2-3个问题点，不使用“技术空白一/二/三”这类直白表述。
四、发明内容：（一）关键创新点；（二）发明目的；（三）拟解决的技术问题；（四）总体技术方案；
（五）数据/环境/对象建模；（六）输入特征/状态空间/任务上下文设计；
（七）约束机制/分配机制/控制机制；（八）评价函数/评分机制/奖励机制；
（九）核心算法/模型训练/协同框架；（十）在线部署与闭环流程；（十一）系统组成；（十二）有益效果。
五、保护范围：说明拟保护的方法、系统、装置、存储介质及核心技术特征边界。
六、附图说明：图1总体流程图、图2系统架构图、图3关键机制逻辑图、图4系统模块组成图。
七、具体实施方式：（一）数据预处理与输入构建；（二）核心算法/模型/系统运行步骤；
（三）优选实施参数；（四）落地部署与动态应用。
八、附图：图1总体流程图、图2系统架构图、图3关键机制逻辑图、图4模块组成框图。"""


WRITING_STEPS = [
    ("标题页", "文档标题、文档类型、申请人/单位、发明人/作者、联系电话、邮箱。文档标题要简短，不重复堆叠“技术交底书”，禁止使用“基于……的……”句式。"),
    ("一、发明名称", "用一句话给出完整技术名称，标题短且清楚，不堆细节，禁止使用“基于……的……”句式。"),
    ("二、技术领域", "行业领域、技术方向、核心方法或系统。"),
    ("三、背景技术", "只写本发明对应行业场景中的2-3个问题点，避免冗余行业介绍，避免“技术空白一/二/三”。"),
    ("四、发明内容", "先写关键创新点，再写发明目的；拟解决的技术问题必须逐条对应背景技术中的问题；再写总体方案、建模、特征、约束、评分、算法、闭环、系统组成和有益效果。"),
    ("五、保护范围", "概括拟保护的方法、系统、装置、存储介质和核心技术特征边界。"),
    ("六、附图说明", "图1至图4的说明。"),
    ("七、具体实施方式", "数据预处理、核心运行步骤、优选参数、落地部署。"),
    ("八、附图", "图1总体流程图、图2系统架构图、图3关键机制逻辑图、图4模块组成框图，使用 Mermaid。"),
]


@dataclass
class MaterialAssessment:
    score: int
    level: str
    reasons: list[str]
    needs_external_search: bool
    project_score: int = 0
    prior_art_score: int = 0
    dimensions: list[dict[str, Any]] | None = None
    capped_by: list[str] | None = None


@dataclass
class PatentCandidate:
    title: str
    summary: str
    raw: str


@dataclass
class AgentState:
    documents: dict[str, Any] | None = None
    material_text: str = ""
    initial_assessment: MaterialAssessment | None = None
    assessment: MaterialAssessment | None = None
    search_topic: str = ""
    external: ExternalSearchResult | None = None
    candidates: list[PatentCandidate] | None = None
    selected_candidate: PatentCandidate | None = None
    final_markdown: str = ""
    quality_review: dict[str, Any] | None = None
    similarity_xlsx: Path | None = None
    similarity_markdown: Path | None = None
    output_path: Path | None = None
    done: bool = False


@dataclass(frozen=True)
class AgentTool:
    name: str
    description: str
    handler: Callable[[], str]


class PatentWorkflowAgent:
    """Agent runner that chooses tools from state and skill instructions."""

    def __init__(self, config: AppConfig, client: LightRAGClient, output_dir: Path) -> None:
        self.config = config
        self.client = client
        self.output_dir = output_dir
        self.state = AgentState()
        self.skills = load_agent_skills(Path.cwd())
        self.skill_prompt = format_skills_for_prompt(self.skills)
        self.tools = {
            name: AgentTool(name, spec.description, handler)
            for name, (spec, handler) in discover_bound_tools(self).items()
        }

    def run(self) -> Path:
        self._print_startup()
        max_steps = 20
        for step in range(1, max_steps + 1):
            tool_name = self._choose_next_tool()
            if tool_name == "finish":
                break
            tool = self.tools[tool_name]
            print(f"\n[Agent Step {step}] 调用工具：{tool.name}")
            print(f"- {tool.description}")
            result = tool.handler()
            if result:
                print(result)
            if self.state.done:
                break

        if not self.state.output_path:
            raise RuntimeError("Agent 未能生成输出文件。")
        return self.state.output_path

    def _print_startup(self) -> None:
        print("\n=== Agent + Skills + Tools 专利发现 Agent ===")
        print("Agent 会读取 skills，自行选择工具，并在关键节点和用户交互。")
        print(f"Agent 核：{self.config.agent_core}")
        print("\n[已加载 Skills]")
        if not self.skills:
            print("- 未找到外部 skill，使用内置工具规则。")
            return
        for skill in self.skills:
            print(f"- {skill.name}: {skill.path}")

    def _choose_next_tool(self) -> str:
        valid = self._policy_next_tool()
        llm_choice = self._llm_next_tool(valid)
        if llm_choice in self.tools or llm_choice == "finish":
            if self._is_tool_allowed_now(llm_choice):
                return llm_choice
        return valid

    def _policy_next_tool(self) -> str:
        if self.state.documents is None:
            return "read_knowledge_base"
        if self.state.initial_assessment is None:
            return "assess_materials"
        if self.state.external is None:
            return "external_search"
        if self.state.assessment is self.state.initial_assessment:
            return "assess_materials"
        if not self.state.candidates:
            return "propose_candidates"
        if self.state.similarity_xlsx is None:
            return "analyze_similar_patents"
        if self.state.selected_candidate is None:
            return "select_candidate"
        if not self.state.final_markdown:
            return "draft_interactively"
        if self.state.quality_review is None:
            return "review_patent_quality"
        if self.state.output_path is None:
            return "save_outputs"
        return "finish"

    def _llm_next_tool(self, recommended: str) -> str:
        tool_list = "\n".join(
            f"- {name}: {tool.description}" for name, tool in self.tools.items()
        )
        prompt = f"""你是一个专利写作 workflow agent 的 planner。请根据 skills、当前状态和工具列表选择下一步工具。

只输出工具名，不要解释。如果不确定，输出：{recommended}

Skills:
{self.skill_prompt[:9000]}

当前状态：
{self._state_summary()}

可用工具：
{tool_list}

建议工具：{recommended}
"""
        try:
            choice = _generate_agent_text(self.config, prompt, timeout=60)
        except (LLMWriterError, CodexCLIError, PiCodingAgentError):
            return recommended
        return choice.strip().splitlines()[0].strip(" `。")

    def _is_tool_allowed_now(self, tool_name: str) -> bool:
        return tool_name == self._policy_next_tool()

    def _state_summary(self) -> str:
        return (
            f"documents_loaded={self.state.documents is not None}\n"
            f"initial_assessment={self.state.initial_assessment.score if self.state.initial_assessment else None}\n"
            f"external_results={len(self.state.external.results) if self.state.external else None}\n"
            f"assessment={self.state.assessment.score if self.state.assessment else None}\n"
            f"candidates={len(self.state.candidates) if self.state.candidates else 0}\n"
            f"similarity_report={self.state.similarity_xlsx is not None}\n"
            f"selected_candidate={self.state.selected_candidate.title if self.state.selected_candidate else None}\n"
            f"draft_ready={bool(self.state.final_markdown)}"
        )

    @register_tool("read_knowledge_base", "读取 LightRAG 文档、处理状态和材料摘要。")
    def _tool_read_knowledge_base(self) -> str:
        self.state.documents = _load_documents(self.client)
        self.state.material_text = _summarize_documents(self.state.documents)
        return "[知识库] 已读取 LightRAG 文档和状态。"

    @register_tool("assess_materials", "按多维度标准评估当前素材是否足以支撑专利写作。")
    def _tool_assess_materials(self) -> str:
        if self.state.documents is None:
            raise RuntimeError("需要先读取知识库。")
        assessment = _assess_materials(self.state.documents, self.state.external)
        if self.state.initial_assessment is None:
            self.state.initial_assessment = assessment
        self.state.assessment = assessment
        _print_assessment(assessment, self.state.documents)
        return ""

    @register_tool("external_search", "执行外部资料与相似专利搜索，用于补充材料和避重。")
    def _tool_external_search(self) -> str:
        self.state.search_topic = _infer_search_topic(self.config, self.state.material_text)
        print(f"\n[外部检索] 检索主题：{self.state.search_topic}")
        self.state.external = search_external_materials(
            self.state.search_topic,
            enabled=True,
            max_results=6,
        )
        _print_external_search(self.state.external)
        return "[外部检索] 已完成。下一步会结合检索结果重新评估素材。"

    @register_tool("propose_candidates", "结合知识库和外部检索提出多个候选专利方向。")
    def _tool_propose_candidates(self) -> str:
        if self.state.assessment is None or self.state.external is None:
            raise RuntimeError("需要先完成素材评估和外部检索。")
        self.state.candidates = _generate_candidates(
            self.config,
            self.state.material_text,
            self.state.assessment,
            self.state.external,
            skill_instructions=self.skill_prompt,
        )
        return f"[候选专利] 已生成 {len(self.state.candidates)} 个候选方向。"

    @register_tool("analyze_similar_patents", "生成候选方向与相似专利的差异分析 Excel 和 Markdown。")
    def _tool_analyze_similar_patents(self) -> str:
        if not self.state.candidates or self.state.external is None:
            raise RuntimeError("需要先生成候选专利和外部检索结果。")
        xlsx, markdown, rows = generate_similar_patent_analysis(
            candidates=self.state.candidates,
            external=self.state.external,
            output_dir=self.output_dir,
        )
        self.state.similarity_xlsx = xlsx
        self.state.similarity_markdown = markdown
        return (
            "[相似专利差异分析]\n"
            f"- Excel：{xlsx}\n"
            f"- Markdown：{markdown}\n"
            f"- 共整理 {rows} 条候选方向-相似专利差异记录。\n"
            "- 注意：该分析基于外部网页检索摘要，正式提交前仍需人工核对专利全文、权利要求和法律状态。"
        )

    @register_tool("select_candidate", "展示候选专利方向并记录用户最终选择。")
    def _tool_select_candidate(self) -> str:
        if not self.state.candidates:
            raise RuntimeError("需要先生成候选专利。")
        self.state.selected_candidate = _select_candidate(self.state.candidates)
        return f"[用户选择] {self.state.selected_candidate.title}"

    @register_tool("draft_interactively", "按章节生成专利文档，并在每章结束后与用户交互。")
    def _tool_draft_interactively(self) -> str:
        if self.state.selected_candidate is None or self.state.assessment is None or self.state.external is None:
            raise RuntimeError("需要先选择候选专利。")
        self.state.final_markdown = _interactive_write_document(
            config=self.config,
            candidate=self.state.selected_candidate,
            material_text=self.state.material_text,
            assessment=self.state.assessment,
            external=self.state.external,
            skill_instructions=self.skill_prompt,
        )
        return "[交互写作] 已完成或用户已结束当前草稿。"

    @register_tool("save_outputs", "保存最终 Markdown、Word 和相关分析产物。")
    def _tool_save_outputs(self) -> str:
        if self.state.selected_candidate is None or not self.state.final_markdown:
            raise RuntimeError("没有可保存的最终文档。")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        path = self.output_dir / "interactive_patent_draft.md"
        result_path = self.output_dir / "result.md"
        path.write_text(self.state.final_markdown, encoding="utf-8")
        result_path.write_text(self.state.final_markdown, encoding="utf-8")
        self.state.output_path = path
        self.state.done = True
        return f"已保存：{path}\n已同步更新：{result_path}"

    @register_tool("review_patent_quality", "逐章及全文检查标题、问题链、证据、保护范围和公式格式。")
    def _tool_review_patent_quality(self) -> str:
        report = review_document(
            self.state.final_markdown,
            evidence_text=self.state.material_text + "\n" + _format_search_results(self.state.external or ExternalSearchResult(False, [], [])),
        )
        self.state.quality_review = report.to_dict()
        if report.passed:
            return f"[质量验收] 最终文档通过，得分 {report.score}/100。"
        details = "\n".join(f"- {issue.message}" for issue in report.issues)
        return f"[质量验收] 最终文档得分 {report.score}/100，仍有以下问题：\n{details}"


def run_interactive_agent(
    config: AppConfig,
    client: LightRAGClient,
    output_dir: Path,
) -> Path:
    return PatentWorkflowAgent(config=config, client=client, output_dir=output_dir).run()


def _load_documents(client: LightRAGClient) -> dict[str, Any]:
    try:
        documents = client.list_documents()
        counts = client.get_status_counts()
    except LightRAGClientError as exc:
        print(f"[知识库] 读取失败：{exc}")
        return {"statuses": {}, "_counts": {}}

    if isinstance(documents, dict):
        documents["_counts"] = counts
    return documents


def _generate_agent_text(config: AppConfig, prompt: str, timeout: int = 180) -> str:
    """Generate text with the configured agent core."""
    if config.agent_core in {"pi", "pi_coding", "pi_coding_agent", "pi-coding-agent"}:
        pi_prompt = f"""你是 Pi coding agent 核。请优先利用项目内 skills 和当前项目上下文完成任务。

重要约束：
1. 本次只需要在最终回复中输出文本结果。
2. 不要修改项目文件，不要创建文件，不要执行破坏性命令。
3. 项目内 Python workflow 会负责 LightRAG、外部检索、Excel/Markdown 写入等工具执行。
4. 输出应直接可被上层 Python workflow 使用。

任务：
{prompt}
"""
        project_root = Path.cwd()
        return generate_text_with_pi_coding_agent(
            prompt=pi_prompt,
            project_root=project_root,
            pi_command=config.pi_command,
            provider=config.pi_provider,
            model=config.pi_model,
            skill_paths=[skill.path.parent for skill in load_agent_skills(project_root)],
            timeout=max(timeout, config.pi_timeout),
        )

    if config.agent_core in {"codex", "codex_cli", "codex-cli"}:
        codex_prompt = f"""你是 Codex CLI agent 核。请优先利用本地 Codex skills、当前项目上下文和可用工具完成任务。

重要约束：
1. 本次只需要在最终回复中输出文本结果。
2. 不要修改项目文件，不要创建文件，不要执行破坏性命令。
3. 如果需要联网检索，可使用 Codex CLI 的搜索能力。
4. 输出应直接可被上层 Python workflow 使用。

任务：
{prompt}
"""
        return generate_text_with_codex_cli(
            prompt=codex_prompt,
            project_root=Path.cwd(),
            codex_command=config.codex_command,
            model=config.codex_model,
            sandbox=config.codex_sandbox,
            enable_search=config.codex_enable_search,
            timeout=max(timeout, config.codex_timeout),
        )
    return generate_text_with_ollama(config, prompt, timeout=timeout)


def _flatten_documents(documents: dict[str, Any]) -> list[dict[str, Any]]:
    statuses = documents.get("statuses", {})
    rows: list[dict[str, Any]] = []
    if not isinstance(statuses, dict):
        return rows
    for status, items in statuses.items():
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict):
                rows.append({"status": status, **item})
    return rows


def _summarize_documents(documents: dict[str, Any]) -> str:
    rows = _flatten_documents(documents)
    if not rows:
        return "知识库暂无可用文档。"

    blocks = []
    for index, row in enumerate(rows, start=1):
        blocks.append(
            f"文档{index}\n"
            f"文件：{row.get('file_path', '未知')}\n"
            f"状态：{row.get('status', '未知')}\n"
            f"chunks：{row.get('chunks_count', 0)}\n"
            f"摘要：{row.get('content_summary', '')}"
        )
    return "\n\n".join(blocks)


def _assess_materials(
    documents: dict[str, Any],
    external: ExternalSearchResult | None = None,
) -> MaterialAssessment:
    rows = _flatten_documents(documents)
    processed = [row for row in rows if str(row.get("status", "")).lower() == "processed"]
    total_chunks = sum(int(row.get("chunks_count") or 0) for row in processed)
    summary_chars = sum(len(str(row.get("content_summary") or "")) for row in processed)
    corpus = "\n".join(
        f"{row.get('file_path', '')}\n{row.get('content_summary', '')}" for row in processed
    )
    external_corpus = _format_search_results(external) if external else ""
    combined_corpus = "\n".join(part for part in (corpus, external_corpus) if part)

    score = 0
    project_score = 0
    prior_art_score = 0
    capped_by: list[str] = []
    reasons: list[str] = []
    dimensions: list[dict[str, Any]] = []

    def add_dimension(
        name: str,
        score_value: int,
        max_score: int,
        passed: bool,
        evidence: str,
        suggestion: str,
        category: str = "project",
    ) -> None:
        nonlocal score, project_score, prior_art_score
        score += score_value
        if category == "prior_art":
            prior_art_score += score_value
        else:
            project_score += score_value
        dimensions.append(
            {
                "name": name,
                "score": score_value,
                "max_score": max_score,
                "passed": passed,
                "evidence": evidence,
                "suggestion": suggestion,
            }
        )
        reasons.append(evidence if passed else suggestion)

    if len(processed) >= 3:
        add_dimension("文档规模", 10, 10, True, "项目文档数量达到 3 篇以上。", "项目文档少于 3 篇，最多只能支撑初稿。")
    elif len(processed) >= 2:
        add_dimension("文档规模", 6, 10, False, "项目文档数量达到 2 篇。", "项目文档少于 3 篇，建议继续补充项目资料。")
    else:
        add_dimension("文档规模", 2, 10, False, "项目文档数量不足。", "项目文档少于 3 篇，建议至少补充到 3 篇。")

    if total_chunks >= 10:
        add_dimension("检索颗粒度", 10, 10, True, "知识库 chunk 数量达到 10 个以上。", "知识库 chunk 数量少于 10，实施细节可能不足。")
    elif total_chunks >= 5:
        add_dimension("检索颗粒度", 6, 10, False, "知识库 chunk 数量达到 5 个以上。", "知识库 chunk 数量少于 10，建议补充更完整资料。")
    else:
        add_dimension("检索颗粒度", 2, 10, False, "知识库 chunk 数量不足。", "知识库 chunk 数量少于 10，实施细节可能不足。")

    if _contains_any(combined_corpus, ["业务背景", "应用场景", "场景", "油藏", "井组", "铝电解", "电解槽", "工业"]):
        add_dimension("业务/应用背景", 12, 12, True, "知识库或外部补充材料包含业务背景或应用场景。", "材料中业务背景或应用场景不够明确。")
    else:
        add_dimension("业务/应用背景", 3, 12, False, "材料中业务背景或应用场景较弱。", "材料中业务背景或应用场景不够明确。")

    if _contains_any(combined_corpus, ["算法", "流程", "模型", "方法", "步骤", "方案", "控制", "预测", "检测", "优化"]):
        add_dimension("技术方案完整度", 18, 18, True, "知识库或外部补充材料包含技术方案、算法或流程。", "材料中技术方案、算法或流程不够明确。")
    else:
        add_dimension("技术方案完整度", 4, 18, False, "材料中技术方案描述不足。", "材料中技术方案、算法或流程不够明确。")

    if _contains_any(combined_corpus, ["输入", "指标", "变量", "特征", "参数", "数据", "温度", "电流", "电压", "浓度", "效率"]):
        add_dimension("数据/指标/变量", 12, 12, True, "知识库或外部补充材料包含输入数据、指标、变量或特征说明。", "材料中输入数据、指标、变量或特征说明不足。")
    else:
        add_dimension("数据/指标/变量", 3, 12, False, "材料中数据和指标描述较弱。", "材料中输入数据、指标、变量或特征说明不足。")

    has_examples = _contains_any(combined_corpus, ["实施例", "实验", "结果", "准确率", "评分", "贡献度", "天", "%", "分", "case", "study"])
    if has_examples:
        add_dimension("实施例/效果证据", 13, 13, True, "知识库或外部补充材料包含实施例、实验结果或效果指标线索。", "材料缺少实施例、实验结果或效果指标。")
    else:
        add_dimension("实施例/效果证据", 2, 13, False, "材料缺少可验证效果证据。", "材料缺少实施例、实验结果或效果指标。")

    if external and external.results:
        result_count = len(external.results)
        if result_count >= 8:
            add = 7
        elif result_count >= 5:
            add = 5
        else:
            add = 2
        patent_bonus = 0

        if _contains_any(_format_search_results(external), ["专利", "CN", "Google Patents", "权利要求"]):
            patent_bonus = 8
            passed = True
            evidence = f"外部检索返回 {result_count} 条结果，且包含专利相关条目。"
            suggestion = "外部检索结果包含专利相关条目。"
        else:
            passed = False
            evidence = f"外部检索返回 {result_count} 条结果，但专利相关条目不足。"
            suggestion = "外部检索结果中专利相关条目不足，建议补充正式专利库检索。"
        add_dimension("外部检索/专利避重", add + patent_bonus, 15, passed, evidence, suggestion, "prior_art")
    else:
        add_dimension("外部检索/专利避重", 0, 15, False, "尚未获得外部检索结果。", "尚未获得外部检索结果，不能判断是否与现有专利重合。", "prior_art")

    prior_art_extra = 0
    prior_art_max = 10
    prior_art_passed = True
    prior_art_evidence: list[str] = []
    prior_art_suggestions: list[str] = []
    if _contains_any(combined_corpus, ["不足", "缺陷", "局限", "痛点", "问题", "challenge", "limitation"]):
        prior_art_extra += 5
        prior_art_evidence.append("知识库或外部补充材料中能提炼现有技术缺陷。")
    else:
        prior_art_passed = False
        prior_art_suggestions.append("材料中现有技术缺陷描述不足。")

    if _contains_any(combined_corpus, ["创新", "区别", "改进", "优化", "贡献", "novel", "improvement"]):
        prior_art_extra += 5
        prior_art_evidence.append("知识库或外部补充材料中能提炼区别点或改进方向。")
    else:
        prior_art_passed = False
        prior_art_suggestions.append("材料中区别点或改进方向仍需补充。")
    add_dimension(
        "技术问题/创新入口",
        prior_art_extra,
        prior_art_max,
        prior_art_passed,
        "；".join(prior_art_evidence) or "技术问题和创新入口证据不足。",
        "；".join(prior_art_suggestions) or "技术问题和创新入口基本明确。",
        "prior_art",
    )

    score = min(score, 100)

    if len(processed) <= 2 and score > 65:
        score = 65
        capped_by.append("只有 1-2 篇项目材料，总分封顶 65。")
    if not external or not external.results:
        if score > 70:
            score = 70
        capped_by.append("没有外部专利检索结果，总分封顶 70。")
    if not has_examples and score > 75:
        score = 75
        capped_by.append("缺少实施例或数据指标，总分封顶 75。")

    if score >= 80:
        level = "充分"
    elif score >= 60:
        level = "基本可用"
    else:
        level = "不足"

    return MaterialAssessment(
        score=score,
        level=level,
        reasons=reasons,
        needs_external_search=score < 80,
        project_score=project_score,
        prior_art_score=prior_art_score,
        dimensions=dimensions,
        capped_by=capped_by,
    )


def _print_assessment(assessment: MaterialAssessment, documents: dict[str, Any]) -> None:
    counts = documents.get("_counts", {})
    print("[知识库状态]")
    print(counts)
    print(f"\n[素材充分性] {assessment.score}/100，{assessment.level}")
    print(f"- 项目材料分：{assessment.project_score}/40")
    print(f"- 专利避重/检索分：{assessment.prior_art_score}/25")
    for reason in assessment.reasons:
        print(f"- {reason}")
    for cap in assessment.capped_by or []:
        print(f"- 封顶规则：{cap}")
    if assessment.needs_external_search:
        print("- 结论：需要补充外部资料。")
    print("- 结论：无论素材是否充分，仍会进行外部专利相关检索以降低重合风险。")


def _infer_search_topic(config: AppConfig, material_text: str) -> str:
    prompt = f"""请根据以下知识库材料，提炼一个用于专利检索的中文检索主题。
要求：只输出一行，包含技术对象、核心方法、应用场景，不要解释。

知识库材料：
{material_text[:5000]}
"""
    try:
        topic = _generate_agent_text(config, prompt, timeout=120)
    except (LLMWriterError, CodexCLIError, PiCodingAgentError):
        topic = "稠油 注采井 连通性 智能分析 专利 技术方案"
    return topic.splitlines()[0].strip(" -。") or "稠油 注采井 连通性 智能分析"


def _contains_any(text: str, keywords: list[str]) -> bool:
    return any(keyword in text for keyword in keywords)


def _print_external_search(external: ExternalSearchResult) -> None:
    for note in external.notes:
        print(f"- {note}")
    for index, result in enumerate(external.results, start=1):
        print(f"{index}. {result.get('title', '')}")


def _generate_candidates(
    config: AppConfig,
    material_text: str,
    assessment: MaterialAssessment,
    external: ExternalSearchResult,
    skill_instructions: str = "",
) -> list[PatentCandidate]:
    search_text = _format_search_results(external)
    prompt = f"""你是专利选题顾问。请根据知识库材料和外部检索结果，提出 5 个可能的发明专利方向。

你必须遵循以下 agent skills：
{skill_instructions[:7000]}

要求：
1. 必须输出 5 个候选，编号必须从候选1到候选5，不能少于 5 个。
2. 每个候选必须使用如下格式：
候选1
名称：...
核心方案：...
创新点：...
避让现有技术：...
素材充分性：...
3. 不要直接写完整交底书。
4. 优先选择与知识库材料高度相关、且能避开外部检索中相近专利的方向。
5. 名称必须是短标题，直接写发明对象，不要写成长句；禁止使用“基于……的……”句式。
   错误示例：基于VMD频带能量特征与时序网络的铝电解槽阳极效应早期预警方法。
   正确示例：铝电解槽阳极效应早期预警方法。

素材充分性评估：{assessment.score}/100，{assessment.level}

知识库材料：
{material_text[:6000]}

外部检索结果：
{search_text[:4000]}
"""
    try:
        raw = _generate_agent_text(config, prompt, timeout=180)
    except (LLMWriterError, CodexCLIError, PiCodingAgentError) as exc:
        raw = f"候选1\n名称：稠油注采井连通性智能评估方法及系统\n核心方案：基于注采生产数据、滞后响应和贡献度分析评估井组连通性。\n创新点：多指标融合、最佳滞后天数、单井贡献度拆分。\n避让现有技术：强调稠油注采井连通性场景和动态闭环评估。\n素材充分性：{assessment.level}\n\n生成失败提示：{exc}"

    print("\n[候选专利方向]\n")
    print(raw)
    candidates = _parse_candidates(raw)
    return _ensure_candidate_count(candidates, material_text, external, assessment, raw, target=5)


def _parse_candidates(raw: str) -> list[PatentCandidate]:
    chunks = re.split(r"(?=候选\s*\d+)", raw)
    candidates: list[PatentCandidate] = []
    for chunk in chunks:
        if "名称" not in chunk:
            continue
        match = re.search(r"名称[:：]\s*(.+)", chunk)
        title = _clean_candidate_title(match.group(1)) if match else ""
        if title:
            candidates.append(PatentCandidate(title=title, summary=chunk[:500], raw=chunk.strip()))
    return candidates


def _ensure_candidate_count(
    candidates: list[PatentCandidate],
    material_text: str,
    external: ExternalSearchResult,
    assessment: MaterialAssessment,
    raw: str,
    target: int = 5,
) -> list[PatentCandidate]:
    deduped: list[PatentCandidate] = []
    seen: set[str] = set()
    for candidate in candidates:
        title = _clean_candidate_title(candidate.title)
        if not title or title in seen:
            continue
        seen.add(title)
        deduped.append(PatentCandidate(title=title, summary=candidate.summary, raw=candidate.raw))
        if len(deduped) >= target:
            return deduped[:target]

    for candidate in _fallback_candidates(material_text, external, assessment, raw):
        if candidate.title in seen:
            continue
        seen.add(candidate.title)
        deduped.append(candidate)
        if len(deduped) >= target:
            break
    return deduped[:target]


def _fallback_candidates(
    material_text: str,
    external: ExternalSearchResult,
    assessment: MaterialAssessment,
    raw: str,
) -> list[PatentCandidate]:
    evidence = f"{material_text}\n{_format_search_results(external)}\n{raw}"
    domain = _infer_candidate_domain(evidence)
    templates = [
        (
            f"{domain}状态预测方法",
            "融合运行数据、历史状态和关键指标，预测目标对象的异常或趋势变化。",
            "多源状态融合、趋势提前识别、阈值自适应。",
            "区别于单一监测或静态阈值方案，强调多指标联合预测。",
        ),
        (
            f"{domain}能效优化控制方法",
            "围绕能耗、效率和稳定性构建评价指标，输出可执行的优化控制策略。",
            "能效指标建模、约束校验、策略闭环修正。",
            "区别于只给出监测结果的方案，强调控制策略生成和反馈修正。",
        ),
        (
            f"{domain}异常诊断系统",
            "对采集数据进行清洗、特征构建和异常归因，形成诊断结论和处置建议。",
            "异常特征组合、原因定位、处置建议联动。",
            "区别于单点报警方案，强调诊断链路和原因解释。",
        ),
        (
            f"{domain}数字孪生监控系统",
            "建立实体对象与虚拟模型之间的数据映射，实现状态展示、仿真分析和运行评估。",
            "数据映射、虚实同步、仿真评估闭环。",
            "区别于普通可视化系统，强调孪生模型与运行反馈的联动。",
        ),
        (
            f"{domain}参数自适应校正方法",
            "根据实时数据、历史偏差和运行约束动态修正模型参数或控制参数。",
            "偏差识别、参数校正、约束保护。",
            "区别于固定参数方案，强调随工况变化的自适应修正。",
        ),
    ]
    return [
        PatentCandidate(
            title=_clean_candidate_title(title),
            summary=f"核心方案：{plan}\n创新点：{innovation}\n避让现有技术：{avoidance}\n素材充分性：{assessment.level}",
            raw=(
                f"候选{index}\n"
                f"名称：{_clean_candidate_title(title)}\n"
                f"核心方案：{plan}\n"
                f"创新点：{innovation}\n"
                f"避让现有技术：{avoidance}\n"
                f"素材充分性：{assessment.level}"
            ),
        )
        for index, (title, plan, innovation, avoidance) in enumerate(templates, start=1)
    ]


def _infer_candidate_domain(evidence: str) -> str:
    text = evidence or ""
    if "铝电解槽" in text:
        return "铝电解槽"
    if "铝电解" in text:
        return "铝电解"
    if "注采井" in text or "油井" in text:
        return "注采井"
    if "电池" in text:
        return "电池"
    if "设备" in text:
        return "工业设备"
    return "工业过程"


def _select_candidate(candidates: list[PatentCandidate]) -> PatentCandidate:
    print("\n请选择要继续写作的专利方向：")
    for index, candidate in enumerate(candidates, start=1):
        print(f"{index}. {candidate.title}")

    while True:
        value = input("输入序号，或直接输入新的专利名称：").strip()
        if value.isdigit():
            index = int(value)
            if 1 <= index <= len(candidates):
                return candidates[index - 1]
        if value:
            cleaned_value = _clean_candidate_title(value)
            return PatentCandidate(title=cleaned_value, summary="用户手动输入的专利方向。", raw=cleaned_value)
        print("请输入有效序号或专利名称。")


def _interactive_write_document(
    config: AppConfig,
    candidate: PatentCandidate,
    material_text: str,
    assessment: MaterialAssessment,
    external: ExternalSearchResult,
    skill_instructions: str = "",
) -> str:
    accepted_sections: list[str] = []
    print("\n进入分步骤写作。每一步可选择：a 接受，r 重写，e 提修改意见，m 手动编辑，q 结束。\n")

    for section_name, section_requirements in WRITING_STEPS:
        section = _generate_section(
            config=config,
            candidate=candidate,
            section_name=section_name,
            section_requirements=section_requirements,
            material_text=material_text,
            assessment=assessment,
            external=external,
            accepted_sections=accepted_sections,
            skill_instructions=skill_instructions,
        )
        while True:
            print(f"\n--- {section_name} ---\n")
            print(section)
            action = input("\n选择 [a接受 / r重写 / e提修改意见 / m手动编辑 / q结束]：").strip().lower() or "a"
            if action == "a":
                accepted_sections.append(section)
                _save_progress(accepted_sections)
                break
            if action == "r":
                section = _generate_section(
                    config=config,
                    candidate=candidate,
                    section_name=section_name,
                    section_requirements=section_requirements,
                    material_text=material_text,
                    assessment=assessment,
                    external=external,
                    accepted_sections=accepted_sections,
                    skill_instructions=skill_instructions,
                )
                continue
            if action == "e":
                instruction = input("请输入修改意见，按回车提交；直接回车则取消：").strip()
                if not instruction:
                    continue
                section = _revise_section(
                    config=config,
                    candidate=candidate,
                    section_name=section_name,
                    current_section=section,
                    instruction=instruction,
                    material_text=material_text,
                    external=external,
                    skill_instructions=skill_instructions,
                    accepted_sections=accepted_sections,
                )
                continue
            if action == "m":
                edited = _read_multiline("请输入修改后的内容，单独一行 END 结束：")
                if edited:
                    accepted_sections.append(edited)
                    _save_progress(accepted_sections)
                    break
                continue
            if action == "q":
                return _assemble_document(candidate, accepted_sections)
            print("无效选择，请重新输入。")

    return _assemble_document(candidate, accepted_sections)


def _generate_section(
    config: AppConfig,
    candidate: PatentCandidate,
    section_name: str,
    section_requirements: str,
    material_text: str,
    assessment: MaterialAssessment,
    external: ExternalSearchResult,
    accepted_sections: list[str],
    skill_instructions: str = "",
) -> str:
    prompt = f"""你是中文发明专利撰写助手。现在不要生成全文，只生成指定章节。

你必须遵循以下 agent skills：
{skill_instructions[:7000]}

候选专利：{candidate.title}
候选说明：{candidate.raw}

最终文档结构：
{FINAL_FORMAT_GUIDE}

当前章节：{section_name}
章节要求：{section_requirements}

已经确认的前文：
{chr(10).join(accepted_sections)[-3000:]}

素材充分性：{assessment.score}/100，{assessment.level}

知识库材料：
{material_text[:6000]}

外部检索结果：
{_format_search_results(external)[:3000]}

要求：
1. 只输出当前章节正文，可以使用必要标题和列表，但不要使用 ```markdown 代码围栏包裹正文。
2. 不要生成其他章节。
3. 事实不足处用“待补充”标注，不要编造申请人、发明人、联系方式。
4. 权利要求式语言仅在需要时使用，避免空泛。
5. 标题格式保持干净，例如“## 一、发明名称”，不要输出“## **一、发明名称**”或多余装饰符。
6. 标题和发明名称要短，只说明发明是什么，不把所有算法、指标、流程都塞进标题；禁止使用“基于……的……”句式，例如应写“铝电解槽阳极效应早期预警方法”，不要写“基于VMD频带能量特征与时序网络的铝电解槽阳极效应早期预警方法”。
7. 背景技术只写本发明要解决的行业问题，避免泛泛行业综述；不要使用“技术空白一/二/三”“补足技术空白”等直白表述，也不要断言整个行业完全没有某项技术。
8. “拟解决的技术问题”必须和背景技术中的问题一一对应：背景提出 a/b/c，发明内容就针对 a/b/c 解决。
9. “关键创新点”放在发明内容开头，不要在后文单独再生成“区别于现有技术的关键创新点”章节。
10. “有益效果”不得编造百分比、金额、精度提升等量化数据；只有知识库材料或外部检索中有明确依据时才可写量化结果，否则只写简洁定性效果。
11. 全文只保留一次文档类型表达，避免重复出现多个“技术交底书”标题。
12. 所有公式必须使用标准 LaTeX：行内公式使用 $...$，独立公式使用单独的 $$...$$；不得使用代码围栏、Unicode 上下标、伪公式或 JSON 转义形式。公式后必须定义变量和单位。
13. 最终文章只呈现专利主题内容，不得写入生成过程、质量审查、自查清单、修复说明、提示词、skill/tool、后端实现或“未使用某句式/使用某规则”等元信息。
"""
    try:
        section = normalize_formula_markdown(_generate_agent_text(config, prompt, timeout=180))
    except (LLMWriterError, CodexCLIError, PiCodingAgentError) as exc:
        return f"## {section_name}\n\n待补充。LLM 生成失败：{exc}"
    return _review_and_repair_section(
        config=config,
        candidate=candidate,
        section_name=section_name,
        content=section,
        material_text=material_text,
        external=external,
        accepted_sections=accepted_sections,
        skill_instructions=skill_instructions,
    )[0]


def _assemble_document(candidate: PatentCandidate, sections: list[str]) -> str:
    cleaned_sections = [strip_process_meta(section) for section in sections]
    return normalize_formula_markdown("\n\n".join(cleaned_sections).strip() + "\n")


def _revise_section(
    config: AppConfig,
    candidate: PatentCandidate,
    section_name: str,
    current_section: str,
    instruction: str,
    material_text: str,
    external: ExternalSearchResult,
    skill_instructions: str = "",
    accepted_sections: list[str] | None = None,
) -> str:
    prompt = f"""你是中文发明专利撰写助手。请只修改当前章节，不要输出其他章节。

你必须遵循以下 agent skills：
{skill_instructions[:7000]}

候选专利：{candidate.title}
当前章节：{section_name}

用户修改意见：
{instruction}

当前章节原文：
{current_section}

可参考知识库材料：
{material_text[:4000]}

可参考外部检索结果：
{_format_search_results(external)[:2000]}

要求：
1. 只输出修改后的当前章节正文，可以使用必要标题和列表，但不要使用 ```markdown 代码围栏包裹正文。
2. 不要解释修改过程。
3. 不要编造申请人、发明人、联系方式等真实身份信息。
4. 标题格式保持干净，例如“## 一、发明名称”，不要输出“## **一、发明名称**”或多余装饰符。
5. 标题和发明名称要短，只说明发明是什么，不把所有算法、指标、流程都塞进标题；禁止使用“基于……的……”句式，例如应写“铝电解槽阳极效应早期预警方法”，不要写“基于VMD频带能量特征与时序网络的铝电解槽阳极效应早期预警方法”。
6. 背景技术只写本发明要解决的行业问题，不写冗余行业综述，不使用“技术空白一/二/三”“补足技术空白”等直白表述。
7. “拟解决的技术问题”必须和背景技术中的问题一一对应。
8. “关键创新点”放在发明内容开头，不要单独再生成“区别于现有技术的关键创新点”章节。
9. “有益效果”不得编造量化数据；没有明确依据时只写简洁定性效果。
10. 避免重复出现多个“技术交底书”标题。
11. 所有公式必须使用标准 LaTeX：行内公式使用 $...$，独立公式使用单独的 $$...$$；不得使用代码围栏或乱码符号。
12. 最终文章只呈现专利主题内容，不得写入生成过程、质量审查、自查清单、修复说明、提示词、skill/tool、后端实现或“未使用某句式/使用某规则”等元信息。
"""
    try:
        section = normalize_formula_markdown(_generate_agent_text(config, prompt, timeout=180))
    except (LLMWriterError, CodexCLIError, PiCodingAgentError) as exc:
        return f"{current_section}\n\n> 修改失败：{exc}"
    return _review_and_repair_section(
        config=config,
        candidate=candidate,
        section_name=section_name,
        content=section,
        material_text=material_text,
        external=external,
        accepted_sections=accepted_sections or [],
        skill_instructions=skill_instructions,
    )[0]


def _review_and_repair_section(
    config: AppConfig,
    candidate: PatentCandidate,
    section_name: str,
    content: str,
    material_text: str,
    external: ExternalSearchResult,
    accepted_sections: list[str],
    skill_instructions: str,
    max_repairs: int = 2,
) -> tuple[str, QualityReport]:
    """Run the patent-quality-review tool and repair failed sections."""
    evidence_text = material_text + "\n" + _format_search_results(external)
    current = normalize_formula_markdown(apply_deterministic_fixes(section_name, content))
    report = review_section(
        section_name,
        current,
        accepted_sections=accepted_sections,
        evidence_text=evidence_text,
    )
    for _ in range(max_repairs):
        if report.passed:
            break
        prompt = f"""你正在执行 patent-quality-review skill 的修复步骤。

候选专利：{candidate.title}
当前章节：{section_name}

质量检查未通过：
{repair_instructions(report)}

当前章节正文：
{current}

已确认前文：
{chr(10).join(accepted_sections)[-4000:]}

知识库证据：
{material_text[:5000]}

外部检索上下文：
{_format_search_results(external)[:2500]}

必须遵循的 skills：
{skill_instructions[:9000]}

要求：
1. 只输出修复后的当前章节，不解释修复过程。
2. 不得编造材料中没有的技术事实或量化效果。
3. 逐项解决质量检查问题，同时保留已经正确的内容。
4. 最终文章只呈现专利主题内容，不得写入生成过程、质量审查、自查清单、修复说明、提示词、skill/tool、后端实现或“未使用某句式/使用某规则”等元信息。
"""
        try:
            current = normalize_formula_markdown(_generate_agent_text(config, prompt, timeout=180))
        except (LLMWriterError, CodexCLIError, PiCodingAgentError):
            break
        current = apply_deterministic_fixes(section_name, current)
        report = review_section(
            section_name,
            current,
            accepted_sections=accepted_sections,
            evidence_text=evidence_text,
        )
    return current, report


def _clean_candidate_title(title: str) -> str:
    cleaned = title.strip().strip("*").strip()
    cleaned = re.sub(r"^[「『“\"]?名称[:：]\s*", "", cleaned).strip()
    cleaned = _shorten_based_on_title(cleaned)
    return cleaned


def _shorten_based_on_title(title: str) -> str:
    """Turn long '基于...的X' patent titles into direct invention titles 'X'."""
    cleaned = title.strip(" 。；;，,")
    match = re.match(r"^基于.+?的(.+)$", cleaned)
    if match:
        cleaned = match.group(1).strip(" 。；;，,")
    return cleaned


def _save_progress(sections: list[str]) -> None:
    Path("outputs").mkdir(exist_ok=True)
    Path("outputs/interactive_patent_draft.md").write_text("\n\n".join(sections), encoding="utf-8")


def _read_multiline(prompt: str) -> str:
    print(prompt)
    lines = []
    while True:
        line = input()
        if line.strip() == "END":
            break
        lines.append(line)
    return "\n".join(lines).strip()


def _format_search_results(external: ExternalSearchResult) -> str:
    if not external.results:
        return "\n".join(external.notes)
    lines = []
    for index, result in enumerate(external.results, start=1):
        lines.append(
            f"{index}. {result.get('title', '')}\n"
            f"摘要：{result.get('snippet', '')}\n"
            f"链接：{result.get('url', '')}"
        )
    return "\n\n".join(lines)

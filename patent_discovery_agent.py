"""Interactive patent discovery and drafting agent."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re
from typing import Any

from config import AppConfig
from external_search import ExternalSearchResult, search_external_materials
from lightrag_client import LightRAGClient, LightRAGClientError
from llm_writer import LLMWriterError, generate_text_with_ollama
from similar_patent_analysis import generate_similar_patent_analysis


FINAL_DOCUMENT_STRUCTURE = [
    "标题页",
    "一、发明名称",
    "二、技术领域",
    "三、背景技术",
    "四、发明内容",
    "五、附图说明",
    "六、具体实施方式",
    "七、附图",
]


FINAL_FORMAT_GUIDE = """最终文案格式：
标题页：文档标题、文档类型、申请人/单位、发明人/作者、联系电话、邮箱。
一、发明名称：用一句话给出完整技术名称。
二、技术领域：所属行业领域、所属技术方向、核心方法/系统。
三、背景技术：（一）行业现状与技术瓶颈；（二）技术空白与行业痛点。
四、发明内容：（一）拟解决的技术问题；（二）总体技术方案；（三）数据/环境/对象建模；
（四）输入特征/状态空间/任务上下文设计；（五）约束机制/分配机制/控制机制；
（六）评价函数/评分机制/奖励机制；（七）核心算法/模型训练/协同框架；
（八）在线部署与闭环流程；（九）系统组成；（十）有益效果；（十一）区别于现有技术的关键创新点。
五、附图说明：图1总体流程图、图2系统架构图、图3关键机制逻辑图、图4系统模块组成图。
六、具体实施方式：（一）数据预处理与输入构建；（二）核心算法/模型/系统运行步骤；
（三）优选实施参数；（四）落地部署与动态应用。
七、附图：图1总体流程图、图2系统架构图、图3关键机制逻辑图、图4模块组成框图。"""


WRITING_STEPS = [
    ("标题页", "文档标题、文档类型、申请人/单位、发明人/作者、联系电话、邮箱。"),
    ("一、发明名称", "用一句话给出完整技术名称。"),
    ("二、技术领域", "行业领域、技术方向、核心方法或系统。"),
    ("三、背景技术", "行业现状与技术瓶颈；技术空白与行业痛点。"),
    ("四、发明内容", "技术问题、总体方案、建模、特征、约束、评分、算法、闭环、系统组成、有益效果和创新点。"),
    ("五、附图说明", "图1至图4的说明。"),
    ("六、具体实施方式", "数据预处理、核心运行步骤、优选参数、落地部署。"),
    ("七、附图", "图1总体流程图、图2系统架构图、图3关键机制逻辑图、图4模块组成框图，使用 Mermaid。"),
]


@dataclass
class MaterialAssessment:
    score: int
    level: str
    reasons: list[str]
    needs_external_search: bool
    project_score: int = 0
    prior_art_score: int = 0
    capped_by: list[str] | None = None


@dataclass
class PatentCandidate:
    title: str
    summary: str
    raw: str


def run_interactive_agent(
    config: AppConfig,
    client: LightRAGClient,
    output_dir: Path,
) -> Path:
    print("\n=== 专利发现 Agent ===")
    print("流程：读取知识库材料 -> 评估素材 -> 强制外部检索 -> 提出候选专利 -> 人工选择 -> 分步骤写作\n")

    documents = _load_documents(client)
    material_text = _summarize_documents(documents)
    assessment = _assess_materials(documents)
    _print_assessment(assessment, documents)

    search_topic = _infer_search_topic(config, material_text)
    print(f"\n[外部检索] 检索主题：{search_topic}")
    external = search_external_materials(search_topic, enabled=True, max_results=6)
    _print_external_search(external)
    assessment = _assess_materials(documents, external)
    print("\n[外部检索后素材评估更新]")
    _print_assessment(assessment, documents)

    candidates = _generate_candidates(config, material_text, assessment, external)
    analysis_xlsx, analysis_md, analysis_rows = generate_similar_patent_analysis(
        candidates=candidates,
        external=external,
        output_dir=output_dir,
    )
    print("\n[相似专利差异分析]")
    print(f"- 已生成 Excel：{analysis_xlsx}")
    print(f"- 已生成 Markdown：{analysis_md}")
    print(f"- 共整理 {analysis_rows} 条候选方向-相似专利差异记录。")
    print("- 注意：该分析基于外部网页检索摘要，正式提交前仍需人工核对专利全文、权利要求和法律状态。")

    selected = _select_candidate(candidates)

    final_markdown = _interactive_write_document(
        config=config,
        candidate=selected,
        material_text=material_text,
        assessment=assessment,
        external=external,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "interactive_patent_draft.md"
    result_path = output_dir / "result.md"
    path.write_text(final_markdown, encoding="utf-8")
    result_path.write_text(final_markdown, encoding="utf-8")

    print(f"\n已保存：{path}")
    print(f"已同步更新：{result_path}")
    return path


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

    score = 0
    project_score = 0
    prior_art_score = 0
    capped_by: list[str] = []
    reasons: list[str] = []

    if len(processed) >= 3:
        score += 8
        project_score += 8
        reasons.append("项目文档数量达到 3 篇以上。")
    else:
        reasons.append("项目文档少于 3 篇，最多只能支撑初稿。")

    if total_chunks >= 10:
        score += 8
        project_score += 8
        reasons.append("知识库 chunk 数量达到 10 个以上。")
    else:
        reasons.append("知识库 chunk 数量少于 10，实施细节可能不足。")

    if _contains_any(corpus, ["业务背景", "应用场景", "场景", "油藏", "井组"]):
        score += 6
        project_score += 6
        reasons.append("材料包含业务背景或应用场景。")
    else:
        reasons.append("材料中业务背景或应用场景不够明确。")

    if _contains_any(corpus, ["算法", "流程", "模型", "方法", "步骤", "方案"]):
        score += 8
        project_score += 8
        reasons.append("材料包含技术方案、算法或流程。")
    else:
        reasons.append("材料中技术方案、算法或流程不够明确。")

    if _contains_any(corpus, ["输入", "指标", "变量", "特征", "参数", "数据"]):
        score += 6
        project_score += 6
        reasons.append("材料包含输入数据、指标、变量或特征说明。")
    else:
        reasons.append("材料中输入数据、指标、变量或特征说明不足。")

    has_examples = _contains_any(corpus, ["实施例", "实验", "结果", "准确率", "评分", "贡献度", "天", "%", "分"])
    if has_examples:
        score += 4
        project_score += 4
        reasons.append("材料包含实施例、实验结果或效果指标线索。")
    else:
        reasons.append("材料缺少实施例、实验结果或效果指标。")

    if external and external.results:
        result_count = len(external.results)
        if result_count >= 8:
            add = 6
        elif result_count >= 5:
            add = 4
        else:
            add = 2
        score += add
        prior_art_score += add
        reasons.append(f"外部检索返回 {result_count} 条结果，已具备初步避重素材。")

        if _contains_any(_format_search_results(external), ["专利", "CN", "Google Patents", "权利要求"]):
            score += 5
            prior_art_score += 5
            reasons.append("外部检索结果包含专利相关条目。")
        else:
            reasons.append("外部检索结果中专利相关条目不足。")
    else:
        reasons.append("尚未获得外部检索结果，不能判断是否与现有专利重合。")

    if _contains_any(corpus, ["不足", "缺陷", "局限", "痛点", "问题"]):
        score += 5
        prior_art_score += 5
        reasons.append("材料中能提炼现有技术缺陷。")
    else:
        reasons.append("材料中现有技术缺陷描述不足。")

    if _contains_any(corpus, ["创新", "区别", "改进", "优化", "贡献"]):
        score += 6
        prior_art_score += 6
        reasons.append("材料中能提炼区别点或改进方向。")
    else:
        reasons.append("材料中区别点或改进方向仍需补充。")

    if _contains_any(corpus, ["名称", "方法", "系统"]):
        score += 3
    if _contains_any(corpus, ["技术领域", "领域", "行业"]):
        score += 3
    if _contains_any(corpus, ["背景", "痛点", "不足"]):
        score += 4
    if _contains_any(corpus, ["S1", "步骤", "流程", "算法"]):
        score += 6
    if _contains_any(corpus, ["模块", "系统", "平台"]):
        score += 4
    if _contains_any(corpus, ["权利要求", "创新点", "区别"]):
        score += 5

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
        topic = generate_text_with_ollama(config, prompt, timeout=120)
    except LLMWriterError:
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
) -> list[PatentCandidate]:
    search_text = _format_search_results(external)
    prompt = f"""你是专利选题顾问。请根据知识库材料和外部检索结果，提出 5 个可能的发明专利方向。

要求：
1. 每个候选必须使用如下格式：
候选1
名称：...
核心方案：...
创新点：...
避让现有技术：...
素材充分性：...
2. 不要直接写完整交底书。
3. 优先选择与知识库材料高度相关、且能避开外部检索中相近专利的方向。

素材充分性评估：{assessment.score}/100，{assessment.level}

知识库材料：
{material_text[:6000]}

外部检索结果：
{search_text[:4000]}
"""
    try:
        raw = generate_text_with_ollama(config, prompt, timeout=180)
    except LLMWriterError as exc:
        raw = f"候选1\n名称：基于多指标动态关联分析的稠油注采井连通性智能评估方法及系统\n核心方案：基于注采生产数据、滞后响应和贡献度分析评估井组连通性。\n创新点：多指标融合、最佳滞后天数、单井贡献度拆分。\n避让现有技术：强调稠油注采井连通性场景和动态闭环评估。\n素材充分性：{assessment.level}\n\n生成失败提示：{exc}"

    print("\n[候选专利方向]\n")
    print(raw)
    candidates = _parse_candidates(raw)
    if not candidates:
        candidates = [
            PatentCandidate(
                title="基于多指标动态关联分析的稠油注采井连通性智能评估方法及系统",
                summary="多指标融合、最佳滞后天数和单井贡献度拆分。",
                raw=raw,
            )
        ]
    return candidates


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
            return PatentCandidate(title=value, summary="用户手动输入的专利方向。", raw=value)
        print("请输入有效序号或专利名称。")


def _interactive_write_document(
    config: AppConfig,
    candidate: PatentCandidate,
    material_text: str,
    assessment: MaterialAssessment,
    external: ExternalSearchResult,
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
) -> str:
    prompt = f"""你是中文发明专利撰写助手。现在不要生成全文，只生成指定章节。

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
1. 只输出当前章节 Markdown。
2. 不要生成其他章节。
3. 事实不足处用“待补充”标注，不要编造申请人、发明人、联系方式。
4. 权利要求式语言仅在需要时使用，避免空泛。
"""
    try:
        return generate_text_with_ollama(config, prompt, timeout=180)
    except LLMWriterError as exc:
        return f"## {section_name}\n\n待补充。LLM 生成失败：{exc}"


def _assemble_document(candidate: PatentCandidate, sections: list[str]) -> str:
    header = f"<!-- Generated by interactive patent discovery agent at {datetime.now().isoformat(timespec='seconds')} -->\n"
    title = f"# {candidate.title}\n"
    return f"{header}\n{title}\n" + "\n\n".join(sections).strip() + "\n"


def _revise_section(
    config: AppConfig,
    candidate: PatentCandidate,
    section_name: str,
    current_section: str,
    instruction: str,
    material_text: str,
    external: ExternalSearchResult,
) -> str:
    prompt = f"""你是中文发明专利撰写助手。请只修改当前章节，不要输出其他章节。

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
1. 只输出修改后的当前章节 Markdown。
2. 不要解释修改过程。
3. 不要编造申请人、发明人、联系方式等真实身份信息。
"""
    try:
        return generate_text_with_ollama(config, prompt, timeout=180)
    except LLMWriterError as exc:
        return f"{current_section}\n\n> 修改失败：{exc}"


def _clean_candidate_title(title: str) -> str:
    return title.strip().strip("*").strip()


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

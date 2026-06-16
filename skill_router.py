"""Rule-based adapter for the patent-writing skill."""

from __future__ import annotations

from models import TaskPlan


TECH_DISCLOSURE_SECTIONS = [
    "封面信息",
    "一、发明名称",
    "二、技术领域",
    "三、背景技术",
    "四、发明内容",
    "五、附图说明",
    "六、具体实施方式",
    "七、权利要求书",
    "附录：融合创新要素说明",
]

TOPIC_ANALYSIS_SECTIONS = [
    "任务理解",
    "项目基础素材",
    "行业检索结论",
    "创新点候选",
    "可专利性评估",
    "推荐选题",
    "后续资料清单",
]

FIGURE_SECTIONS = [
    "附图清单",
    "图1 总体流程图",
    "图2 系统模块图",
    "图3 数据处理流程图",
]


def route_task(user_task: str) -> TaskPlan:
    """Choose the writing workflow branch from the patent skill."""
    text = user_task.lower()

    if any(keyword in text for keyword in ("附图", "mermaid", "流程图", "系统图")):
        return TaskPlan(
            task_type="figures",
            title="专利附图全集",
            intent="生成专利附图清单和 Mermaid 草稿",
            output_filename="patent_figures.md",
            required_sections=FIGURE_SECTIONS,
            suggested_queries=_queries(user_task, ["附图", "流程图", "系统架构"]),
        )

    if any(keyword in text for keyword in ("选题", "创新点", "可专利", "检索", "授权")):
        return TaskPlan(
            task_type="topic_analysis",
            title="专利选题分析",
            intent="进行行业检索、创新点挖掘与可专利性评估",
            output_filename="topic_analysis.md",
            required_sections=TOPIC_ANALYSIS_SECTIONS,
            suggested_queries=_queries(user_task, ["现有技术", "创新点", "可专利性"]),
        )

    return TaskPlan(
        task_type="technical_disclosure",
        title="专利技术交底书",
        intent="撰写发明专利技术交底书初稿",
        output_filename="technical_disclosure.md",
        required_sections=TECH_DISCLOSURE_SECTIONS,
        suggested_queries=_queries(user_task, ["技术方案", "背景技术", "实施例", "权利要求"]),
    )


def _queries(user_task: str, suffixes: list[str]) -> list[str]:
    return [user_task, *[f"{user_task} {suffix}" for suffix in suffixes]]

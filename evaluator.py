"""Compare generated patent documents with demo expectations."""

from __future__ import annotations

from dataclasses import dataclass

from models import TaskPlan


@dataclass
class EvaluationResult:
    passed: bool
    missing_sections: list[str]
    warnings: list[str]


def evaluate_markdown(markdown: str, plan: TaskPlan) -> EvaluationResult:
    missing = [section for section in plan.required_sections if section not in markdown]
    warnings = []

    if "[no-context]" in markdown:
        warnings.append("知识库未检索到可用上下文，当前文档主要是结构化草稿，缺少知识库素材支撑。")

    if "暂无 references" in markdown:
        warnings.append("没有可引用的知识库 references，后续需要补充来源或调整检索参数。")

    if "国家知识产权局专利检索及分析系统" not in markdown:
        warnings.append("缺少正式专利库人工检索提醒。")

    return EvaluationResult(
        passed=not missing,
        missing_sections=missing,
        warnings=warnings,
    )


def build_evaluation_markdown(result: EvaluationResult) -> str:
    status = "通过" if result.passed else "未通过"
    missing = "\n".join(f"- {item}" for item in result.missing_sections) or "- 无"
    warnings = "\n".join(f"- {item}" for item in result.warnings) or "- 无"

    return f"""## 对标检查

检查结论：{status}

### 缺失章节

{missing}

### 风险提示

{warnings}
"""

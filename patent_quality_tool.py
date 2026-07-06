"""Deterministic quality gates for patent drafting."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Any

from formula_utils import formula_issues

@dataclass(frozen=True)
class QualityIssue:
    code: str
    message: str
    repair: str
    severity: str = "error"


@dataclass(frozen=True)
class QualityReport:
    section_name: str
    score: int
    passed: bool
    issues: list[QualityIssue]

    def to_dict(self) -> dict[str, Any]:
        return {
            "section_name": self.section_name,
            "score": self.score,
            "passed": self.passed,
            "issues": [asdict(issue) for issue in self.issues],
        }


REQUIRED_DOCUMENT_HEADINGS = [
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

FORBIDDEN_GAP_PHRASES = [
    "技术空白一",
    "技术空白二",
    "技术空白三",
    "补足技术空白",
    "填补技术空白",
    "行业尚无",
    "业内尚无",
]

GENERIC_PLACEHOLDERS = ["目标行业", "目标对象", "某行业", "某场景", "通用行业"]

PROCESS_META_PATTERNS = [
    r"质量审查",
    r"自查",
    r"待补充项",
    r"修改说明",
    r"修复说明",
    r"修复过程",
    r"生成说明",
    r"提示词",
    r"\bprompt\b",
    r"\bagent\b",
    r"\bskill\b",
    r"\btool\b",
    r"工具调用",
    r"背后",
    r"后端(?:实现|代码|逻辑|做法|处理)",
    r"我已(?:经)?(?:按照|根据|使用|调用)",
    r"本(?:章节|文档)已(?:经)?(?:通过|遵循|按照)",
    r"未使用[“\"].+?[”\"]句式",
    r"使用了.+?(?:写作规则|规则|格式|模板|句式)",
    r"遵循了.+?(?:规则|要求|skill|Skill)",
]

PROCESS_META_RE = re.compile("|".join(PROCESS_META_PATTERNS), re.IGNORECASE)

UNSUPPORTED_QUANTIFIED_EFFECT = re.compile(
    r"(?:提升|提高|降低|减少|节省|缩短|达到|增加)"
    r"[^。\n；]{0,20}?"
    r"(\d+(?:\.\d+)?)\s*(%|％|万元|元|倍|小时|天)"
)


def review_section(
    section_name: str,
    content: str,
    accepted_sections: list[str] | None = None,
    evidence_text: str = "",
) -> QualityReport:
    """Review one generated section against reusable patent-writing gates."""
    accepted_sections = accepted_sections or []
    issues: list[QualityIssue] = []
    text = content.strip()

    for line in _process_meta_lines(text):
        issues.append(
            QualityIssue(
                "process_meta_leak",
                f"正文包含生成过程或质量检查说明：{line[:60]}",
                "删除关于提示词、skill/tool、质量自查、修复过程和写作规则的说明，只保留专利主题正文。",
            )
        )

    for message in formula_issues(text):
        issues.append(
            QualityIssue(
                "malformed_formula",
                message,
                "把公式改为标准 LaTeX：行内公式使用成对单美元定界符，独立公式使用成对双美元定界符，并在公式后定义变量。",
            )
        )

    if not text or text in {"待补充", "## 待补充"}:
        issues.append(
            QualityIssue(
                "empty_section",
                "章节没有形成有效正文。",
                "根据现有材料写出可确认的正文；确实缺失的事实单独标记“待补充”。",
            )
        )

    if section_name in {"标题页", "一、发明名称"}:
        if re.search(r"基于.{2,80}的.{2,60}", text):
            issues.append(
                QualityIssue(
                    "long_based_on_title",
                    "标题仍使用“基于……的……”长句式。",
                    "删除算法和特征前缀，直接保留发明对象，例如“铝电解槽阳极效应早期预警方法”。",
                )
            )
        title = _extract_title(text)
        if len(title) > 30:
            issues.append(
                QualityIssue(
                    "title_too_long",
                    f"标题长度为 {len(title)} 个字符，仍然堆叠了过多细节。",
                    "标题控制在30个汉字以内，算法、特征和流程放入发明内容。",
                )
            )

    if section_name == "三、背景技术":
        if len(text) > 1600:
            issues.append(
                QualityIssue(
                    "background_too_long",
                    f"背景技术约 {len(text)} 字，超出聚焦范围。",
                    "压缩为2-3个与本发明直接相关的行业问题，删除泛泛行业综述。",
                )
            )
        for phrase in FORBIDDEN_GAP_PHRASES:
            if phrase in text:
                issues.append(
                    QualityIssue(
                        "absolute_technology_gap",
                        f"背景技术使用了不稳妥表述“{phrase}”。",
                        "改写为现有方案在具体场景、数据、约束或闭环能力上的不足。",
                    )
                )
        if _count_problem_points(text) < 2:
            issues.append(
                QualityIssue(
                    "background_problem_points",
                    "背景技术没有清晰归纳至少2个待解决问题。",
                    "用编号列表归纳2-3个具体行业问题，供发明内容逐项对应。",
                )
            )

    if section_name == "四、发明内容":
        required = ["关键创新点", "发明目的", "拟解决的技术问题", "总体技术方案", "有益效果"]
        for heading in required:
            if heading not in text:
                issues.append(
                    QualityIssue(
                        f"missing_{heading}",
                        f"发明内容缺少“{heading}”。",
                        f"补充“{heading}”，并保持关键创新点在发明内容开头。",
                    )
                )
        if "区别于现有技术的关键创新点" in text:
            issues.append(
                QualityIssue(
                    "duplicated_innovation_section",
                    "仍然单独生成了“区别于现有技术的关键创新点”。",
                    "把内容合并到发明内容开头的“关键创新点”，删除重复小节。",
                )
            )

        background = _find_section(accepted_sections, "三、背景技术")
        background_count = _count_problem_points(background)
        problem_part = _subsection(text, "拟解决的技术问题", "总体技术方案")
        solution_count = _count_problem_points(problem_part)
        if background_count >= 2 and solution_count < background_count:
            issues.append(
                QualityIssue(
                    "problem_solution_mismatch",
                    f"背景技术归纳了约 {background_count} 个问题，但拟解决技术问题只明确对应 {solution_count} 个。",
                    "按照背景问题的顺序逐项写出对应解决目标，保持a/b/c一一对应。",
                )
            )

        for match in UNSUPPORTED_QUANTIFIED_EFFECT.finditer(text):
            evidence = match.group(0)
            if evidence not in evidence_text:
                issues.append(
                    QualityIssue(
                        "unsupported_benefit_number",
                        f"有益效果出现缺少材料依据的量化表述“{evidence}”。",
                        "删除该数值，改为简洁定性效果；只有材料明确给出时才保留并说明来源。",
                    )
                )

    if section_name == "五、保护范围":
        missing = [term for term in ("方法", "系统") if term not in text]
        if not any(term in text for term in ("装置", "设备", "存储介质")):
            missing.append("装置/设备/存储介质")
        if missing:
            issues.append(
                QualityIssue(
                    "incomplete_protection_scope",
                    f"保护范围未覆盖：{'、'.join(missing)}。",
                    "围绕核心技术特征分别说明方法、系统以及装置/设备/存储介质的保护边界。",
                )
            )

    if section_name not in {"标题页"}:
        generic = [term for term in GENERIC_PLACEHOLDERS if term in text]
        if generic:
            issues.append(
                QualityIssue(
                    "generic_placeholder",
                    f"正文仍包含泛化占位词：{'、'.join(generic)}。",
                    "替换为当前知识库材料中的具体行业、对象和应用场景；材料缺失时标记待补充。",
                )
            )

    return _make_report(section_name, issues)


def review_document(markdown: str, evidence_text: str = "") -> QualityReport:
    """Review the assembled document before final saving."""
    issues: list[QualityIssue] = []
    reviewed_sections: list[str] = []
    for heading in REQUIRED_DOCUMENT_HEADINGS:
        if heading not in markdown:
            issues.append(
                QualityIssue(
                    "missing_document_section",
                    f"最终文档缺少“{heading}”。",
                    "补齐标准章节后再导出最终文档。",
                )
            )
            continue
        section_text = _document_section(markdown, heading)
        section_report = review_section(
            heading,
            section_text,
            accepted_sections=reviewed_sections,
            evidence_text=evidence_text,
        )
        issues.extend(section_report.issues)
        reviewed_sections.append(section_text)
    if markdown.count("技术交底书") > 1:
        issues.append(
            QualityIssue(
                "duplicated_document_title",
                "最终文档重复出现“技术交底书”标题。",
                "只保留标题页中的一次文档类型表达。",
            )
        )
    if re.search(r"基于.{2,80}的.{2,60}", _first_title_block(markdown)):
        issues.append(
            QualityIssue(
                "long_based_on_title",
                "最终标题仍使用“基于……的……”句式。",
                "直接使用发明对象名称。",
            )
        )
    for match in UNSUPPORTED_QUANTIFIED_EFFECT.finditer(markdown):
        evidence = match.group(0)
        if evidence not in evidence_text:
            issues.append(
                QualityIssue(
                    "unsupported_benefit_number",
                    f"最终文档存在无材料依据的量化效果“{evidence}”。",
                    "删除无依据数值或补充来源。",
                )
            )
    unique_issues: list[QualityIssue] = []
    seen: set[tuple[str, str]] = set()
    for issue in issues:
        key = (issue.code, issue.message)
        if key in seen:
            continue
        seen.add(key)
        unique_issues.append(issue)
    return _make_report("最终文档", unique_issues)


def repair_instructions(report: QualityReport) -> str:
    """Convert a quality report into precise repair instructions for the agent core."""
    if report.passed:
        return "当前章节已通过质量检查，无需修改。"
    return "\n".join(
        f"{index}. {issue.message} 修复要求：{issue.repair}"
        for index, issue in enumerate(report.issues, start=1)
    )


def apply_deterministic_fixes(section_name: str, content: str) -> str:
    """Apply safe fixes that should not depend on model compliance."""
    content = strip_process_meta(content)
    if section_name not in {"标题页", "一、发明名称"}:
        return content
    return re.sub(
        r"基于[^|\n。]{2,90}的([^|\n。]{2,50}(?:方法及系统|方法|系统|装置|设备|平台))",
        r"\1",
        content,
    )


def strip_process_meta(content: str) -> str:
    """Remove process-facing notes that should never appear in the patent body."""
    lines = content.splitlines()
    kept: list[str] = []
    skipping = False
    for line in lines:
        stripped = line.strip()
        is_heading = bool(re.match(r"^#{1,4}\s+", stripped))
        if skipping and is_heading and not PROCESS_META_RE.search(stripped):
            skipping = False
        if PROCESS_META_RE.search(stripped):
            skipping = True
            continue
        if not skipping:
            kept.append(line)
    return "\n".join(kept).strip()


def _process_meta_lines(text: str) -> list[str]:
    lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip().strip("-* >")
        if not stripped:
            continue
        if PROCESS_META_RE.search(stripped):
            lines.append(stripped)
    return lines


def _make_report(section_name: str, issues: list[QualityIssue]) -> QualityReport:
    penalty = sum(15 if issue.severity == "error" else 5 for issue in issues)
    score = max(0, 100 - penalty)
    return QualityReport(section_name, score, not issues, issues)


def _extract_title(text: str) -> str:
    for line in text.splitlines():
        cleaned = line.strip().strip("#").strip()
        if not cleaned or cleaned in {"标题页", "一、发明名称"}:
            continue
        if "|" in cleaned:
            cells = [cell.strip() for cell in cleaned.strip("|").split("|")]
            if len(cells) >= 2 and cells[0] in {"文档标题", "发明名称"}:
                return cells[1]
            continue
        label_match = re.match(r"^(?:文档标题|发明名称)[:：]\s*(.+)$", cleaned)
        if label_match:
            return label_match.group(1).strip()
        return cleaned
    return ""


def _count_problem_points(text: str) -> int:
    if not text:
        return 0
    numbered = re.findall(r"(?m)^\s*(?:\d+[.、]|[-*])\s+.+$", text)
    if numbered:
        return min(len(numbered), 6)
    return len(re.findall(r"(?:问题|不足|缺陷)[一二三四五六\d]?", text))


def _find_section(sections: list[str], heading: str) -> str:
    return next((section for section in sections if heading in section), "")


def _subsection(text: str, start: str, end: str) -> str:
    if start not in text:
        return ""
    part = text.split(start, 1)[1]
    if end in part:
        part = part.split(end, 1)[0]
    return part


def _first_title_block(markdown: str) -> str:
    return "\n".join(markdown.splitlines()[:20])


def _document_section(markdown: str, heading: str) -> str:
    start = markdown.find(heading)
    if start < 0:
        return ""
    end = len(markdown)
    for next_heading in REQUIRED_DOCUMENT_HEADINGS:
        if next_heading == heading:
            continue
        position = markdown.find(next_heading, start + len(heading))
        if position >= 0:
            end = min(end, position)
    return markdown[start:end].strip()

"""Normalize, validate, and convert patent formulas."""

from __future__ import annotations

import re
from typing import Any
from xml.etree import ElementTree as ET

from tool_registry import register_tool


MATHML_NS = "http://www.w3.org/1998/Math/MathML"


@register_tool(
    "normalize_patent_formulas",
    "把公式统一为标准 LaTeX，检查定界符，并供网页 MathJax 与 Word 原生公式使用。",
    "Document production",
)
def normalize_formula_markdown(markdown: str) -> str:
    """Normalize common model outputs to $...$ and $$...$$ LaTeX."""
    text = str(markdown or "").replace("\r\n", "\n")
    text = re.sub(r"\\\[\s*", "$$\n", text)
    text = re.sub(r"\s*\\\]", "\n$$", text)
    text = re.sub(r"\\\(\s*", "$", text)
    text = re.sub(r"\s*\\\)", "$", text)
    text = re.sub(
        r"```(?:latex|tex|math)\s*\n(.*?)\n```",
        lambda match: f"$$\n{match.group(1).strip()}\n$$",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    text = re.sub(
        r"\\\\(frac|sum|sqrt|mathbf|mathrm|begin|end|left|right|hat|lambda|theta|eta|leq|geq)",
        r"\\\1",
        text,
    )
    text = text.replace("```mathjax", "```math")
    return text


def formula_issues(markdown: str) -> list[str]:
    """Return reader-facing issues for malformed formula source."""
    text = str(markdown or "")
    issues: list[str] = []
    if "\ufffd" in text:
        issues.append("公式包含 Unicode 替换字符，可能已经发生编码损坏。")
    display_count = text.count("$$")
    if display_count % 2:
        issues.append("块级公式的双美元定界符数量不成对。")
    without_display = re.sub(r"\$\$.*?\$\$", "", text, flags=re.DOTALL)
    inline_count = len(re.findall(r"(?<!\\)\$", without_display))
    if inline_count % 2:
        issues.append("行内公式的单美元定界符数量不成对。")
    if re.search(r"```(?:latex|tex|math)", text, flags=re.IGNORECASE):
        issues.append("公式不应放在 Markdown 代码围栏中。")
    if re.search(r"(?<![$\\])\\(?:frac|sum|sqrt|mathbf|mathrm|begin)\b", without_display):
        issues.append("检测到未被公式定界符包裹的 LaTeX 公式。")
    return issues


def append_latex_omml(paragraph: Any, latex: str) -> bool:
    """Append a native Word OMML equation to a paragraph."""
    try:
        from latex2mathml.converter import convert
        from docx.oxml import OxmlElement
    except ImportError:
        return False

    try:
        mathml = convert(latex.strip())
        root = ET.fromstring(mathml)
        equation = OxmlElement("m:oMath")
        _append_mathml_children(equation, root)
        paragraph._p.append(equation)
        return True
    except Exception:
        return False


def append_formula_fallback(paragraph: Any, latex: str) -> None:
    """Use a readable Cambria Math fallback when native conversion is unavailable."""
    from docx.oxml.ns import qn
    from docx.shared import Pt

    run = paragraph.add_run(latex.strip())
    run.font.name = "Cambria Math"
    run.font.size = Pt(11)
    run._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), "Cambria Math")


def _append_mathml_children(parent: Any, element: ET.Element) -> None:
    tag = _local_name(element.tag)
    if tag in {"math", "mrow", "semantics"}:
        for child in list(element):
            if _local_name(child.tag) not in {"annotation", "annotation-xml"}:
                _append_mathml_children(parent, child)
        return

    if tag in {"mi", "mn", "mo", "mtext", "ms"}:
        _append_run(parent, "".join(element.itertext()))
        return

    if tag == "mfrac":
        fraction = _omml(parent, "m:f")
        numerator = _omml(fraction, "m:num")
        denominator = _omml(fraction, "m:den")
        children = list(element)
        if children:
            _append_mathml_children(numerator, children[0])
        if len(children) > 1:
            _append_mathml_children(denominator, children[1])
        return

    if tag in {"msup", "msub", "msubsup"}:
        kind = {"msup": "m:sSup", "msub": "m:sSub", "msubsup": "m:sSubSup"}[tag]
        script = _omml(parent, kind)
        children = list(element)
        if children:
            base = _omml(script, "m:e")
            _append_mathml_children(base, children[0])
        if tag in {"msub", "msubsup"} and len(children) > 1:
            sub = _omml(script, "m:sub")
            _append_mathml_children(sub, children[1])
        if tag == "msup" and len(children) > 1:
            sup = _omml(script, "m:sup")
            _append_mathml_children(sup, children[1])
        if tag == "msubsup" and len(children) > 2:
            sup = _omml(script, "m:sup")
            _append_mathml_children(sup, children[2])
        return

    if tag in {"msqrt", "mroot"}:
        radical = _omml(parent, "m:rad")
        children = list(element)
        if tag == "msqrt":
            properties = _omml(radical, "m:radPr")
            degree_hide = _omml(properties, "m:degHide")
            degree_hide.set("{http://schemas.openxmlformats.org/officeDocument/2006/math}val", "1")
            body = _omml(radical, "m:e")
            for child in children:
                _append_mathml_children(body, child)
        else:
            body = _omml(radical, "m:e")
            degree = _omml(radical, "m:deg")
            if children:
                _append_mathml_children(body, children[0])
            if len(children) > 1:
                _append_mathml_children(degree, children[1])
        return

    if tag == "mfenced":
        delimiter = _omml(parent, "m:d")
        properties = _omml(delimiter, "m:dPr")
        begin = _omml(properties, "m:begChr")
        begin.set("{http://schemas.openxmlformats.org/officeDocument/2006/math}val", element.attrib.get("open", "("))
        end = _omml(properties, "m:endChr")
        end.set("{http://schemas.openxmlformats.org/officeDocument/2006/math}val", element.attrib.get("close", ")"))
        body = _omml(delimiter, "m:e")
        for child in list(element):
            _append_mathml_children(body, child)
        return

    if tag == "mtable":
        equation_array = _omml(parent, "m:eqArr")
        for row in list(element):
            equation = _omml(equation_array, "m:e")
            _append_mathml_children(equation, row)
        return

    if tag in {"mtr", "mtd"}:
        for child in list(element):
            _append_mathml_children(parent, child)
        return

    if tag in {"mover", "munder", "munderover"}:
        children = list(element)
        kind = {"mover": "m:sSup", "munder": "m:sSub", "munderover": "m:sSubSup"}[tag]
        script = _omml(parent, kind)
        base = _omml(script, "m:e")
        if children:
            _append_mathml_children(base, children[0])
        if tag in {"munder", "munderover"} and len(children) > 1:
            sub = _omml(script, "m:sub")
            _append_mathml_children(sub, children[1])
        if tag == "mover" and len(children) > 1:
            sup = _omml(script, "m:sup")
            _append_mathml_children(sup, children[1])
        if tag == "munderover" and len(children) > 2:
            sup = _omml(script, "m:sup")
            _append_mathml_children(sup, children[2])
        return

    for child in list(element):
        _append_mathml_children(parent, child)
    if not list(element) and element.text:
        _append_run(parent, element.text)


def _omml(parent: Any, tag: str) -> Any:
    from docx.oxml import OxmlElement

    child = OxmlElement(tag)
    parent.append(child)
    return child


def _append_run(parent: Any, text: str) -> None:
    if not text:
        return
    run = _omml(parent, "m:r")
    run_properties = _omml(run, "m:rPr")
    normal = _omml(run_properties, "m:nor")
    normal.set("{http://schemas.openxmlformats.org/officeDocument/2006/math}val", "1")
    value = _omml(run, "m:t")
    value.text = text


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]

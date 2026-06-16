"""Generate similar-patent difference analysis outputs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile
from xml.sax.saxutils import escape

from external_search import ExternalSearchResult


HEADERS = ["公开号", "申请号", "申请日", "发明名称", "申请人", "摘要", "差异点"]


@dataclass
class SimilarPatentRow:
    publication_no: str
    application_no: str
    application_date: str
    title: str
    applicant: str
    abstract: str
    difference: str

    def as_list(self) -> list[str]:
        return [
            self.publication_no,
            self.application_no,
            self.application_date,
            self.title,
            self.applicant,
            self.abstract,
            self.difference,
        ]


def generate_similar_patent_analysis(
    candidates: list[Any],
    external: ExternalSearchResult,
    output_dir: Path,
) -> tuple[Path, Path, int]:
    """Create xlsx and markdown reports for similar patent differences."""
    output_dir.mkdir(parents=True, exist_ok=True)
    sheets = _build_candidate_sheets(candidates, external)

    xlsx_path = output_dir / "similar_patent_analysis.xlsx"
    markdown_path = output_dir / "similar_patent_analysis.md"
    _write_xlsx(xlsx_path, sheets)
    _write_markdown(markdown_path, sheets)
    row_count = sum(len(rows) for rows in sheets.values())
    return xlsx_path, markdown_path, row_count


def _build_candidate_sheets(
    candidates: list[Any],
    external: ExternalSearchResult,
) -> dict[str, list[SimilarPatentRow]]:
    if not candidates:
        candidates = [_SimpleCandidate("待选择专利方向", "暂无候选专利方向。")]

    results = external.results or [
        {
            "title": "未获得外部相似专利检索结果",
            "snippet": "外部检索结果为空，需后续通过国家知识产权局专利检索及分析系统人工补充。",
            "url": "",
        }
    ]

    sheets: dict[str, list[SimilarPatentRow]] = {}
    used_sheet_names: set[str] = set()
    for index, candidate in enumerate(candidates, start=1):
        title = str(getattr(candidate, "title", "") or f"候选专利方向{index}")
        summary = str(getattr(candidate, "summary", "") or getattr(candidate, "raw", ""))
        sheet_name = _unique_sheet_name(f"候选{index}-{title}", used_sheet_names)
        rows = []
        for result in results:
            source_title = str(result.get("title", "")).strip() or "待核实相似专利"
            snippet = str(result.get("snippet", "")).strip()
            source_text = f"{source_title}\n{snippet}\n{result.get('url', '')}"
            rows.append(
                SimilarPatentRow(
                    publication_no=_extract_publication_no(source_text),
                    application_no=_extract_application_no(source_text),
                    application_date=_extract_date(source_text),
                    title=source_title,
                    applicant=_extract_applicant(source_text),
                    abstract=snippet or "待通过正式专利库补充摘要。",
                    difference=_build_difference(candidate_title=title, candidate_summary=summary, source_title=source_title, snippet=snippet),
                )
            )
        sheets[sheet_name] = rows
    return sheets


def _build_difference(
    candidate_title: str,
    candidate_summary: str,
    source_title: str,
    snippet: str,
) -> str:
    candidate_focus = _compact(candidate_summary, 180) or candidate_title
    source_focus = _compact(snippet, 160) or source_title
    return (
        f"相似专利侧重于“{source_focus}”。本候选方向为“{candidate_title}”，"
        f"核心构思侧重于“{candidate_focus}”。初步差异可从应用场景、输入数据、核心指标、"
        "约束/评价机制、闭环部署方式和权利要求落点进行展开。该判断仅基于网页检索摘要，"
        "公开号、申请号、权利要求范围和法律状态仍需在正式专利库中核验。"
    )


def _write_markdown(path: Path, sheets: dict[str, list[SimilarPatentRow]]) -> None:
    parts = ["# 相似专利差异分析", ""]
    parts.append("> 说明：本表由外部网页检索结果自动整理，只能作为初筛，不能替代正式专利检索。")
    for sheet_name, rows in sheets.items():
        parts.extend(["", f"## {sheet_name}", ""])
        parts.append("| " + " | ".join(HEADERS) + " |")
        parts.append("| " + " | ".join(["---"] * len(HEADERS)) + " |")
        for row in rows:
            parts.append("| " + " | ".join(_md_cell(value) for value in row.as_list()) + " |")
    path.write_text("\n".join(parts).strip() + "\n", encoding="utf-8")


def _write_xlsx(path: Path, sheets: dict[str, list[SimilarPatentRow]]) -> None:
    sheet_items = list(sheets.items()) or [("相似专利差异分析", [])]
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _content_types(len(sheet_items)))
        zf.writestr("_rels/.rels", _root_rels())
        zf.writestr("xl/workbook.xml", _workbook_xml(sheet_items))
        zf.writestr("xl/_rels/workbook.xml.rels", _workbook_rels(len(sheet_items)))
        zf.writestr("xl/styles.xml", _styles_xml())
        for index, (_, rows) in enumerate(sheet_items, start=1):
            zf.writestr(f"xl/worksheets/sheet{index}.xml", _sheet_xml(rows))


def _sheet_xml(rows: list[SimilarPatentRow]) -> str:
    table = [HEADERS, *[row.as_list() for row in rows]]
    row_xml = []
    for row_index, values in enumerate(table, start=1):
        cells = []
        for col_index, value in enumerate(values, start=1):
            style = 1 if row_index == 1 else 2
            cells.append(
                f'<c r="{_cell_ref(row_index, col_index)}" t="inlineStr" s="{style}">'
                f"<is><t>{escape(str(value))}</t></is></c>"
            )
        row_xml.append(f'<row r="{row_index}">{"".join(cells)}</row>')
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>
  <cols>
    <col min="1" max="1" width="16" customWidth="1"/>
    <col min="2" max="2" width="18" customWidth="1"/>
    <col min="3" max="3" width="14" customWidth="1"/>
    <col min="4" max="4" width="42" customWidth="1"/>
    <col min="5" max="5" width="22" customWidth="1"/>
    <col min="6" max="6" width="58" customWidth="1"/>
    <col min="7" max="7" width="82" customWidth="1"/>
  </cols>
  <sheetData>{"".join(row_xml)}</sheetData>
  <autoFilter ref="A1:G{max(1, len(table))}"/>
</worksheet>"""


def _styles_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <fonts count="2">
    <font><sz val="11"/><name val="Arial"/></font>
    <font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Arial"/></font>
  </fonts>
  <fills count="3">
    <fill><patternFill patternType="none"/></fill>
    <fill><patternFill patternType="gray125"/></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FF2F75B5"/><bgColor indexed="64"/></patternFill></fill>
  </fills>
  <borders count="2">
    <border><left/><right/><top/><bottom/><diagonal/></border>
    <border><left style="thin"/><right style="thin"/><top style="thin"/><bottom style="thin"/><diagonal/></border>
  </borders>
  <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
  <cellXfs count="3">
    <xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
    <xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf>
    <xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>
  </cellXfs>
  <cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>"""


def _content_types(sheet_count: int) -> str:
    overrides = "\n".join(
        f'<Override PartName="/xl/worksheets/sheet{index}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for index in range(1, sheet_count + 1)
    )
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
  {overrides}
</Types>"""


def _root_rels() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""


def _workbook_xml(sheet_items: list[tuple[str, list[SimilarPatentRow]]]) -> str:
    sheets = "\n".join(
        f'<sheet name="{_xml_attr(name)}" sheetId="{index}" r:id="rId{index}"/>'
        for index, (name, _) in enumerate(sheet_items, start=1)
    )
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>{sheets}</sheets>
</workbook>"""


def _workbook_rels(sheet_count: int) -> str:
    rels = [
        f'<Relationship Id="rId{index}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{index}.xml"/>'
        for index in range(1, sheet_count + 1)
    ]
    rels.append(
        f'<Relationship Id="rId{sheet_count + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
    )
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  {"".join(rels)}
</Relationships>"""


def _cell_ref(row: int, col: int) -> str:
    letters = ""
    while col:
        col, remainder = divmod(col - 1, 26)
        letters = chr(65 + remainder) + letters
    return f"{letters}{row}"


def _extract_publication_no(text: str) -> str:
    match = re.search(r"\bCN[\s-]?\d{6,}[A-Z0-9]*\b", text, flags=re.IGNORECASE)
    return match.group(0).replace(" ", "") if match else "待核实"


def _extract_application_no(text: str) -> str:
    match = re.search(r"(?:申请号|申请公布号)[:：\s]*([A-Z0-9.\-]+)", text, flags=re.IGNORECASE)
    return match.group(1) if match else "待核实"


def _extract_date(text: str) -> str:
    match = re.search(r"(20\d{2})[年\-/.](\d{1,2})(?:[月\-/.](\d{1,2})日?)?", text)
    if not match:
        return "待核实"
    year, month, day = match.group(1), match.group(2).zfill(2), (match.group(3) or "01").zfill(2)
    return f"{year}-{month}-{day}"


def _extract_applicant(text: str) -> str:
    match = re.search(r"(?:申请人|专利权人)[:：\s]*([^，。;\n|]+)", text)
    return match.group(1).strip() if match else "待核实"


def _unique_sheet_name(name: str, used: set[str]) -> str:
    base = re.sub(r"[\[\]:*?/\\]", " ", name)
    base = " ".join(base.split())[:28] or "Sheet"
    candidate = base
    index = 2
    while candidate in used:
        suffix = f"_{index}"
        candidate = f"{base[:31 - len(suffix)]}{suffix}"
        index += 1
    used.add(candidate)
    return candidate


def _compact(text: str, limit: int) -> str:
    value = " ".join(str(text).split())
    return value[:limit].rstrip()


def _md_cell(value: str) -> str:
    return str(value).replace("|", "\\|").replace("\n", "<br>")


def _xml_attr(value: str) -> str:
    return escape(value, {'"': "&quot;"})


class _SimpleCandidate:
    def __init__(self, title: str, summary: str) -> None:
        self.title = title
        self.summary = summary

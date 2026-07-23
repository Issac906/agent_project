"""Render patent Markdown into Feishu message-card content and real images."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
import math
import re
import tempfile
from typing import Callable

from PIL import Image, ImageDraw, ImageFont


IMAGE_TOKEN = "FEISHU_IMAGE_TOKEN_{}"


@dataclass
class RenderedPatentMessage:
    markdown: str
    images: list[tuple[str, bytes]]


def render_patent_markdown(text: str) -> RenderedPatentMessage:
    """Turn formulas, Mermaid blocks and local images into uploadable PNG assets."""
    value = str(text or "").replace("\r\n", "\n")
    images: list[tuple[str, bytes]] = []

    def add_image(name: str, payload: bytes) -> str:
        index = len(images)
        images.append((name, payload))
        return IMAGE_TOKEN.format(index)

    def mermaid_replacement(match: re.Match[str]) -> str:
        code = match.group(1).strip()
        return f"\n\n{add_image('patent-diagram.png', render_mermaid_png(code))}\n\n"

    value = re.sub(r"```mermaid\s*\n(.*?)```", mermaid_replacement, value, flags=re.I | re.S)

    def block_formula(match: re.Match[str]) -> str:
        latex = (match.group(1) or match.group(2) or "").strip()
        if not latex:
            return ""
        return f"\n\n{add_image('formula.png', render_formula_png(latex))}\n\n"

    value = re.sub(r"\$\$(.+?)\$\$|\\\[(.+?)\\\]", block_formula, value, flags=re.S)

    def inline_formula(match: re.Match[str]) -> str:
        latex = (match.group(1) or match.group(2) or "").strip()
        if not latex:
            return ""
        # Feishu scales Markdown images to the card width. Rendering an inline
        # formula as an image therefore breaks the sentence and creates a large
        # blank area. Keep it inline as readable mathematical Unicode instead.
        return _latex_to_display(latex)

    value = re.sub(r"(?<!\$)\$([^$\n]+?)\$(?!\$)|\\\((.+?)\\\)", inline_formula, value)

    def local_image(match: re.Match[str]) -> str:
        alt, source = match.group(1), match.group(2).strip()
        path = _resolve_local_image(source)
        if not path:
            return match.group(0)
        return add_image(_safe_name(path.name), path.read_bytes())

    value = re.sub(r"!\[([^\]]*)]\(([^)]+)\)", local_image, value)
    value = _card_markdown(value)
    return RenderedPatentMessage(markdown=value.strip(), images=images)


def build_feishu_cards(
    title: str,
    text: str,
    upload_image: Callable[[str, bytes], str],
    limit: int = 24000,
) -> list[dict]:
    """Build ordered Feishu cards with native text and image components."""
    rendered = render_patent_markdown(text)
    elements = _build_card_elements(rendered, upload_image, limit)
    pages = _paginate_elements(elements, limit)
    cards: list[dict] = []
    for index, page in enumerate(pages, start=1):
        card_title = title if len(pages) == 1 else f"{title}（{index}/{len(pages)}）"
        cards.append(
            {
                "config": {"wide_screen_mode": True},
                "header": {
                    "template": "green",
                    "title": {"tag": "plain_text", "content": card_title},
                },
                "elements": page,
            }
        )
    return cards


def _build_card_elements(
    rendered: RenderedPatentMessage,
    upload_image: Callable[[str, bytes], str],
    limit: int,
) -> list[dict]:
    """Preserve the exact text/media order using Feishu native components."""

    token_pattern = re.compile(r"FEISHU_IMAGE_TOKEN_(\d+)")
    elements: list[dict] = []
    cursor = 0
    for match in token_pattern.finditer(rendered.markdown):
        _append_markdown_elements(elements, rendered.markdown[cursor:match.start()], limit)
        image_index = int(match.group(1))
        if image_index < len(rendered.images):
            name, payload = rendered.images[image_index]
            image_key = upload_image(name, payload)
            elements.append(
                {
                    "tag": "img",
                    "img_key": image_key,
                    "alt": {"tag": "plain_text", "content": _image_alt(name)},
                    "mode": "fit_horizontal",
                    "preview": True,
                }
            )
        cursor = match.end()
    _append_markdown_elements(elements, rendered.markdown[cursor:], limit)
    return elements or [{"tag": "markdown", "content": " "}]


def _append_markdown_elements(elements: list[dict], value: str, limit: int) -> None:
    text = str(value or "").strip()
    if not text:
        return
    for chunk in _split_blocks(text, limit):
        elements.append({"tag": "markdown", "content": chunk.strip() or " "})


def _paginate_elements(elements: list[dict], limit: int, max_elements: int = 18) -> list[list[dict]]:
    pages: list[list[dict]] = []
    page: list[dict] = []
    characters = 0
    for element in elements:
        element_chars = len(str(element.get("content") or ""))
        if page and (len(page) >= max_elements or characters + element_chars > limit):
            pages.append(page)
            page = []
            characters = 0
        page.append(element)
        characters += element_chars
    if page:
        pages.append(page)
    return pages or [[{"tag": "markdown", "content": " "}]]


def _image_alt(name: str) -> str:
    if name == "formula.png":
        return "数学公式"
    if name == "patent-diagram.png":
        return "专利附图"
    return Path(name).stem or "图片"


def render_formula_png(latex: str, compact: bool = False) -> bytes:
    """Render formula runs with real baseline, subscript and superscript layout."""
    base_size = 34 if compact else 42
    script_size = 23 if compact else 28
    lines = _formula_layout_lines(latex)
    padding_x, padding_y = 38, 24
    probe = Image.new("RGB", (10, 10), "white")
    probe_draw = ImageDraw.Draw(probe)

    measured: list[tuple[list[tuple[str, int]], int]] = []
    for runs in lines:
        width = 0
        for text, level in runs:
            font = _formula_font(text, script_size if level else base_size)
            box = probe_draw.textbbox((0, 0), text, font=font)
            width += max(0, box[2] - box[0])
        measured.append((runs, width))

    natural_width = max((width for _, width in measured), default=300) + padding_x * 2
    width = max(760 if compact else 1100, min(natural_width, 1900))
    line_height = 72 if compact else 88
    height = max(110, padding_y * 2 + line_height * len(measured))
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    for line_index, (runs, line_width) in enumerate(measured):
        x = max(padding_x, (width - min(line_width, width - padding_x * 2)) / 2)
        baseline_top = padding_y + line_index * line_height + (10 if compact else 8)
        for text, level in runs:
            font_size = script_size if level else base_size
            font = _formula_font(text, font_size)
            box = draw.textbbox((0, 0), text, font=font)
            run_width = max(0, box[2] - box[0])
            if level < 0:
                y = baseline_top + base_size * 0.48
            elif level > 0:
                y = baseline_top - base_size * 0.20
            else:
                y = baseline_top
            draw.text((x, y), text, fill="#101915", font=font)
            x += run_width
    return _png_bytes(image)


def render_mermaid_png(code: str) -> bytes:
    """Render common Mermaid flowcharts as a clean static diagram for Feishu."""
    nodes, edges = _parse_mermaid(code)
    if not nodes:
        nodes = [(f"n{i}", line.strip()) for i, line in enumerate(code.splitlines()) if line.strip()][:12]
    node_map = dict(nodes)
    ids = list(node_map)
    levels = _mermaid_levels(ids, edges)
    level_groups: dict[int, list[str]] = {}
    for node_id in ids:
        level_groups.setdefault(levels.get(node_id, 0), []).append(node_id)
    ordered_levels = sorted(level_groups)
    columns = max(1, max((len(level_groups[level]) for level in ordered_levels), default=1))
    rows = max(1, len(ordered_levels))
    box_w, box_h, gap_x, gap_y = 360, 118, 90, 86
    margin = 70
    width = margin * 2 + columns * box_w + (columns - 1) * gap_x
    height = margin * 2 + rows * box_h + (rows - 1) * gap_y
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    font = _font(23)
    positions: dict[str, tuple[int, int]] = {}
    for row, level in enumerate(ordered_levels):
        group = level_groups[level]
        group_width = len(group) * box_w + max(0, len(group) - 1) * gap_x
        start_x = (width - group_width) / 2
        for column, node_id in enumerate(group):
            x = int(start_x + column * (box_w + gap_x))
            y = margin + row * (box_h + gap_y)
            positions[node_id] = (x, y)

    for source, target, label in edges:
        if source not in positions or target not in positions:
            continue
        sx, sy = positions[source]
        tx, ty = positions[target]
        if ty >= sy:
            start = (sx + box_w / 2, sy + box_h)
            end = (tx + box_w / 2, ty)
        else:
            start = (sx + box_w, sy + box_h / 2)
            end = (tx + box_w, ty + box_h / 2)
        draw.line((start, end), fill="#8aa099", width=4)
        _arrow_head(draw, start, end)
        if label:
            mx, my = (start[0] + end[0]) / 2, (start[1] + end[1]) / 2
            draw.text((mx + 5, my - 24), _clean_mermaid_label(label)[:18], fill="#52635d", font=_font(17))

    palette = ["#e7f4ef", "#eef2ff", "#fff5df"]
    borders = ["#147a62", "#4355b9", "#9a6500"]
    for index, node_id in enumerate(ids):
        x, y = positions[node_id]
        draw.rounded_rectangle((x, y, x + box_w, y + box_h), radius=10, fill=palette[index % 3], outline=borders[index % 3], width=3)
        lines = _wrap_label(_clean_mermaid_label(node_map[node_id]), 22)[:3]
        line_height = 31
        text_y = y + (box_h - len(lines) * line_height) / 2
        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=font)
            draw.text((x + (box_w - (bbox[2] - bbox[0])) / 2, text_y), line, fill="#101915", font=font)
            text_y += line_height
    return _png_bytes(image)


def _card_markdown(value: str) -> str:
    value = re.sub(r"^\s{0,3}#{1,6}\s+(.+)$", r"**\1**", value, flags=re.M)
    value = value.replace("```markdown", "```")
    value = re.sub(r"\n{4,}", "\n\n", value)
    return value


def _split_blocks(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    blocks = re.split(r"(\n\n+)", text)
    chunks: list[str] = []
    current = ""
    for block in blocks:
        if len(current) + len(block) <= limit:
            current += block
            continue
        if current:
            chunks.append(current)
            current = ""
        while len(block) > limit:
            chunks.append(block[:limit])
            block = block[limit:]
        current = block
    if current:
        chunks.append(current)
    return chunks or [""]


def _latex_to_display(value: str) -> str:
    text = value.strip()
    blackboard = {
        r"\mathbb{R}": "ℝ",
        r"\mathbb{N}": "ℕ",
        r"\mathbb{Z}": "ℤ",
        r"\mathbb{Q}": "ℚ",
        r"\mathbb{C}": "ℂ",
    }
    for source, target in blackboard.items():
        text = text.replace(source, target)
    replacements = {
        r"\times": "×", r"\cdot": "·", r"\leq": "≤", r"\geq": "≥", r"\neq": "≠",
        r"\approx": "≈", r"\infty": "∞", r"\sum": "Σ", r"\prod": "Π", r"\int": "∫",
        r"\alpha": "α", r"\beta": "β", r"\gamma": "γ", r"\delta": "δ", r"\theta": "θ",
        r"\lambda": "λ", r"\mu": "μ", r"\sigma": "σ", r"\omega": "ω", r"\Delta": "Δ",
        r"\rightarrow": "→", r"\leftarrow": "←", r"\pm": "±", r"\in": "∈",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    for _ in range(5):
        updated = re.sub(r"\\frac\s*\{([^{}]+)}\s*\{([^{}]+)}", r"(\1)/(\2)", text)
        updated = re.sub(r"\\sqrt\s*\{([^{}]+)}", r"√(\1)", updated)
        if updated == text:
            break
        text = updated
    text = re.sub(r"\\(?:mathrm|mathbf|mathit|mathsf|mathtt|mathcal|mathbb|text|operatorname)\s*\{([^{}]+)}", r"\1", text)
    text = re.sub(r"_\{([^{}]+)}", lambda match: _script_text(match.group(1), subscript=True), text)
    text = re.sub(r"\^\{([^{}]+)}", lambda match: _script_text(match.group(1), subscript=False), text)
    text = re.sub(r"_([A-Za-z0-9+-])", lambda match: _script_text(match.group(1), subscript=True), text)
    text = re.sub(r"\^([A-Za-z0-9+-])", lambda match: _script_text(match.group(1), subscript=False), text)
    text = text.replace(r"\left", "").replace(r"\right", "")
    text = text.replace("{", "").replace("}", "")
    text = re.sub(r"\\[A-Za-z]+", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _script_text(value: str, subscript: bool) -> str:
    superscript = str.maketrans("0123456789+-=()in", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁱⁿ")
    subscript_map = str.maketrans("0123456789+-=()aeioruvx", "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑᵢₒᵣᵤᵥₓ")
    table = subscript_map if subscript else superscript
    converted = value.translate(table)
    # Unicode only defines a small subset of alphabetic script characters.
    # Mixing converted and baseline letters produces broken-looking formulas
    # and missing-glyph boxes on Feishu clients. Keep unsupported scripts in a
    # clear textual notation for inline formulas; block formulas use real
    # positioned script runs in render_formula_png().
    if any(char.translate(table) == char and char.isalpha() for char in value):
        return f"_({value})" if subscript else f"^({value})"
    return converted


def _formula_layout_lines(value: str) -> list[list[tuple[str, int]]]:
    source = str(value or "").strip()
    # Some model/JSON paths leave LaTeX commands double-escaped. Collapse only
    # backslashes followed by a command name; keep ``\\`` equation line breaks.
    command_names = (
        "alpha|beta|gamma|delta|theta|lambda|mu|sigma|omega|Delta|times|cdot|"
        "leq|geq|neq|approx|infty|sum|prod|int|rightarrow|leftarrow|pm|in|"
        "frac|sqrt|mathrm|mathbf|mathit|mathsf|mathtt|mathcal|mathbb|text|"
        "operatorname|left|right|begin|end"
    )
    source = re.sub(rf"\\\\(?=(?:{command_names})\b)", r"\\", source)
    source = re.sub(r"\\begin\{(?:aligned|align|gathered|array)\}(?:\{[^}]*\})?", "", source)
    source = re.sub(r"\\end\{(?:aligned|align|gathered|array)\}", "", source)
    source = source.replace("&", "")
    raw_lines = [part.strip() for part in re.split(r"\\\\(?:\s*\[[^]]+\])?|\n+", source) if part.strip()]
    return [_formula_runs(line) for line in raw_lines] or [[("", 0)]]


def _formula_runs(value: str) -> list[tuple[str, int]]:
    runs: list[tuple[str, int]] = []
    cursor = 0
    normal_start = 0
    while cursor < len(value):
        if value[cursor] not in "_^":
            cursor += 1
            continue
        if normal_start < cursor:
            text = _latex_to_display(value[normal_start:cursor])
            if text:
                runs.append((text, 0))
        level = -1 if value[cursor] == "_" else 1
        cursor += 1
        if cursor < len(value) and value[cursor] == "{":
            end = _matching_brace(value, cursor)
            content = value[cursor + 1:end]
            cursor = end + 1
        elif cursor < len(value):
            content = value[cursor]
            cursor += 1
        else:
            content = ""
        text = _latex_to_display(content)
        if text:
            runs.append((text, level))
        normal_start = cursor
    if normal_start < len(value):
        text = _latex_to_display(value[normal_start:])
        if text:
            runs.append((text, 0))
    return runs or [(_latex_to_display(value), 0)]


def _matching_brace(value: str, start: int) -> int:
    depth = 0
    for index in range(start, len(value)):
        if value[index] == "{":
            depth += 1
        elif value[index] == "}":
            depth -= 1
            if depth == 0:
                return index
    return len(value) - 1


def _parse_mermaid(code: str) -> tuple[list[tuple[str, str]], list[tuple[str, str, str]]]:
    nodes: dict[str, str] = {}
    edges: list[tuple[str, str, str]] = []
    node_pattern = r"([A-Za-z0-9_\u4e00-\u9fff-]+)(?:\[([^]]+)\]|\(([^)]+)\)|\{([^}]+)\})?"
    edge_pattern = re.compile(
        node_pattern
        + r"\s*(?:-->|---|==>|-.->)(?:\|([^|\n]+)\|)?\s*"
        + node_pattern
    )
    lines = [line.strip().rstrip(";") for line in code.splitlines()]

    # Collect declarations first. An edge such as ``B --> C`` must not
    # overwrite labels previously declared as ``B[中文名称]``.
    for line in lines:
        if not line or re.match(r"^(?:flowchart|graph|subgraph|end|direction|style|classDef|class|linkStyle)\b", line, re.I):
            continue
        for match in re.finditer(node_pattern, line):
            node_id = match.group(1)
            label = next((v for v in match.group(2, 3, 4) if v), "")
            if label:
                nodes[node_id] = label

    for line in lines:
        if not line or re.match(r"^(?:flowchart|graph|subgraph|end|direction|style|classDef|class|linkStyle)\b", line, re.I):
            continue
        match = edge_pattern.search(line)
        if match:
            source, source_label = match.group(1), next((v for v in match.group(2, 3, 4) if v), match.group(1))
            label = match.group(5) or ""
            target, target_label = match.group(6), next((v for v in match.group(7, 8, 9) if v), match.group(6))
            if source not in nodes or source_label != source:
                nodes[source] = source_label
            if target not in nodes or target_label != target:
                nodes[target] = target_label
            edges.append((source, target, label.strip()))
            continue
        match = re.search(node_pattern, line)
        if match:
            nodes[match.group(1)] = next((v for v in match.group(2, 3, 4) if v), match.group(1))
    return list(nodes.items())[:32], edges[:64]


def _mermaid_levels(ids: list[str], edges: list[tuple[str, str, str]]) -> dict[str, int]:
    """Assign shortest-path levels so feedback edges do not collapse layout."""
    incoming = {node_id: 0 for node_id in ids}
    outgoing: dict[str, list[str]] = {node_id: [] for node_id in ids}
    for source, target, _label in edges:
        if source in outgoing and target in incoming and source != target:
            outgoing[source].append(target)
            incoming[target] += 1
    roots = [node_id for node_id in ids if incoming[node_id] == 0]
    if not roots and ids:
        roots = [ids[0]]
    levels: dict[str, int] = {node_id: 0 for node_id in roots}
    queue = list(roots)
    while queue:
        source = queue.pop(0)
        for target in outgoing.get(source, []):
            proposed = min(9, levels[source] + 1)
            if target not in levels or proposed < levels[target]:
                levels[target] = proposed
                queue.append(target)
    for node_id in ids:
        levels.setdefault(node_id, 0)
    return levels


def _wrap_label(value: str, width: int) -> list[str]:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        return [""]
    chunks: list[str] = []
    while text:
        if len(text) <= width:
            chunks.append(text)
            break
        split_at = max(text.rfind(" ", 0, width + 1), text.rfind("/", 0, width + 1))
        if split_at < width // 2:
            split_at = width
        chunks.append(text[:split_at].strip())
        text = text[split_at:].lstrip(" /")
    return chunks


def _arrow_head(draw: ImageDraw.ImageDraw, start: tuple[float, float], end: tuple[float, float]) -> None:
    angle = math.atan2(end[1] - start[1], end[0] - start[0])
    size = 14
    points = [
        end,
        (end[0] - size * math.cos(angle - 0.55), end[1] - size * math.sin(angle - 0.55)),
        (end[0] - size * math.cos(angle + 0.55), end[1] - size * math.sin(angle + 0.55)),
    ]
    draw.polygon(points, fill="#8aa099")


def _clean_mermaid_label(value: str) -> str:
    text = re.sub(r"<br\s*/?>", " ", str(value or ""), flags=re.I)
    return re.sub(r"[\[\]{}\"']", "", text).strip()


def _font(size: int, math_font: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    math_candidates = [
        "/System/Library/Fonts/STIXTwoText.ttf",
        "/System/Library/Fonts/Supplemental/STIXTwoText.ttf",
        "/System/Library/Fonts/Supplemental/STIXGeneral.otf",
        "C:/Windows/Fonts/cambria.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuMathTeXGyre.ttf",
    ]
    text_candidates = [
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "DejaVuSans.ttf",
    ]
    candidates = math_candidates + text_candidates if math_font else text_candidates
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _formula_font(text: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    return _font(size, math_font=not bool(re.search(r"[\u3400-\u9fff]", text)))


def _wrap_text(value: str, width: int) -> list[str]:
    text = str(value or "").strip()
    return [text[index:index + width] for index in range(0, len(text), width)] or [""]


def _png_bytes(image: Image.Image) -> bytes:
    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


def _resolve_local_image(source: str) -> Path | None:
    source = source.split("#", 1)[0].strip().strip("<>")
    if source.startswith(("http://", "https://", "data:")):
        return None
    candidates = [Path(source), Path.cwd() / source, Path.cwd() / "outputs" / Path(source).name]
    return next((path.resolve() for path in candidates if path.is_file()), None)


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value) or "image.png"

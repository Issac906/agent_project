from __future__ import annotations

import unittest
from io import BytesIO

from PIL import Image

from feishu_rendering import (
    _formula_layout_lines,
    _latex_to_display,
    _parse_mermaid,
    build_feishu_cards,
    render_patent_markdown,
)


class FeishuRenderingTests(unittest.TestCase):
    def test_formula_and_mermaid_are_rendered_as_real_images(self) -> None:
        rendered = render_patent_markdown(
            "## 技术方案\n\n目标函数：$$J=\\sum_i x_i^2$$\n\n"
            "```mermaid\nflowchart TD\nA[采集数据] --> B[输出方案]\n```"
        )

        self.assertNotIn("```mermaid", rendered.markdown)
        self.assertNotIn("\\sum", rendered.markdown)
        self.assertEqual(2, len(rendered.images))
        self.assertTrue(all(payload.startswith(b"\x89PNG") for _, payload in rendered.images))

    def test_card_uses_uploaded_image_keys_and_preserves_format(self) -> None:
        uploaded: list[str] = []

        def upload(name: str, payload: bytes) -> str:
            uploaded.append(name)
            return f"img_key_{len(uploaded)}"

        cards = build_feishu_cards("章节", "# 标题\n\n公式 $E=mc^2$", upload)
        content = cards[0]["elements"][0]["content"]

        self.assertIn("**标题**", content)
        self.assertIn("公式 E=mc²", content)
        self.assertEqual([], uploaded)

    def test_block_formula_is_a_native_image_between_continuous_text(self) -> None:
        cards = build_feishu_cards(
            "章节",
            "公式定义如下：\n\n$$\\mu \\in \\mathbb{R}^{1536}$$\n\n其中 μ 为特征向量。",
            lambda _name, _payload: "formula_key",
        )

        elements = cards[0]["elements"]
        self.assertEqual(["markdown", "img", "markdown"], [row["tag"] for row in elements])
        self.assertIn("公式定义如下", elements[0]["content"])
        self.assertEqual("formula_key", elements[1]["img_key"])
        self.assertEqual("fit_horizontal", elements[1]["mode"])
        self.assertIn("其中 μ 为特征向量", elements[2]["content"])

    def test_formula_image_uses_wide_compact_canvas(self) -> None:
        from feishu_rendering import render_formula_png

        image = Image.open(BytesIO(render_formula_png(r"\\mu \\in \\mathbb{R}^{1536}")))
        self.assertGreaterEqual(image.width, 1100)
        self.assertLess(image.height, image.width / 5)

    def test_formula_layout_keeps_named_subscripts_as_positioned_runs(self) -> None:
        lines = _formula_layout_lines(
            r"S_{geo}=f_{geo}(G_{printed},G_{target})\\S_{mat}=f_{mat}(M_{printed},M_{standard})"
        )

        self.assertEqual(2, len(lines))
        self.assertIn(("geo", -1), lines[0])
        self.assertIn(("printed", -1), lines[0])
        self.assertNotIn("□", "".join(text for line in lines for text, _level in line))

    def test_inline_named_subscript_uses_readable_fallback(self) -> None:
        self.assertEqual("S_(geo)", _latex_to_display(r"S_{geo}"))

    def test_mermaid_labels_survive_later_bare_edge_references(self) -> None:
        nodes, edges = _parse_mermaid(
            """flowchart TB
            A[工业相机图像采集]
            B[实时缺陷检测]
            C[数字孪生状态更新]
            A --> B
            B --> C
            C -->|反馈校正| B
            """
        )

        labels = dict(nodes)
        self.assertEqual("工业相机图像采集", labels["A"])
        self.assertEqual("实时缺陷检测", labels["B"])
        self.assertEqual("数字孪生状态更新", labels["C"])
        self.assertIn(("C", "B", "反馈校正"), edges)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from docx_exporter import export_markdown_to_docx


@unittest.skipUnless(importlib.util.find_spec("latex2mathml"), "latex2mathml is not installed")
class DocxFormulaTests(unittest.TestCase):
    def test_exports_native_word_equation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "formula.docx"
            export_markdown_to_docx(
                r"""# 公式测试

状态变量为 $x_t$。

$$
\eta=\frac{Q_{\mathrm{effective}}}{Q_{\mathrm{input}}}
$$
""",
                path,
            )
            with ZipFile(path) as archive:
                xml = archive.read("word/document.xml").decode("utf-8")
            self.assertIn("<m:oMath", xml)
            self.assertIn("<m:f>", xml)
            self.assertIn("<m:sSub>", xml)
            self.assertNotIn("\\frac", xml)


if __name__ == "__main__":
    unittest.main()

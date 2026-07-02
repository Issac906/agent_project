from __future__ import annotations

import unittest

from formula_utils import formula_issues, normalize_formula_markdown


class FormulaUtilsTests(unittest.TestCase):
    def test_normalizes_bracket_delimiters(self) -> None:
        source = r"变量 \(x_t\)，公式：\[J=\frac{a}{b}\]"
        normalized = normalize_formula_markdown(source)
        self.assertIn("$x_t$", normalized)
        self.assertIn("$$", normalized)
        self.assertIn(r"\frac{a}{b}", normalized)
        self.assertEqual([], formula_issues(normalized))

    def test_normalizes_formula_code_fence(self) -> None:
        source = "```latex\nJ=\\sum_{i=1}^{n}x_i\n```"
        normalized = normalize_formula_markdown(source)
        self.assertNotIn("```", normalized)
        self.assertTrue(normalized.startswith("$$"))

    def test_reports_unbalanced_formula(self) -> None:
        issues = formula_issues("目标函数为 $J(x)。")
        self.assertTrue(any("不成对" in issue for issue in issues))

    def test_repairs_double_escaped_latex_command(self) -> None:
        normalized = normalize_formula_markdown(r"$$\\frac{a}{b}$$")
        self.assertIn(r"\frac{a}{b}", normalized)
        self.assertNotIn(r"\\frac", normalized)


if __name__ == "__main__":
    unittest.main()

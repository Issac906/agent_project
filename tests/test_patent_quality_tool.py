from __future__ import annotations

import unittest

from patent_quality_tool import (
    apply_deterministic_fixes,
    review_document,
    review_section,
    strip_process_meta,
)


class PatentQualityToolTests(unittest.TestCase):
    def test_based_on_title_is_rejected_and_fixed(self) -> None:
        content = (
            "## 一、发明名称\n\n"
            "基于VMD频带能量特征与时序网络的铝电解槽阳极效应早期预警方法"
        )
        report = review_section("一、发明名称", content)
        self.assertFalse(report.passed)
        self.assertIn("long_based_on_title", [issue.code for issue in report.issues])
        self.assertIn(
            "铝电解槽阳极效应早期预警方法",
            apply_deterministic_fixes("一、发明名称", content),
        )

    def test_background_rejects_absolute_technology_gap(self) -> None:
        report = review_section(
            "三、背景技术",
            "## 三、背景技术\n\n技术空白一：行业尚无相关能力。",
        )
        self.assertFalse(report.passed)
        self.assertIn("absolute_technology_gap", [issue.code for issue in report.issues])

    def test_invention_content_requires_problem_mapping(self) -> None:
        background = "## 三、背景技术\n1. 问题A\n2. 问题B\n3. 问题C"
        content = """## 四、发明内容
### 4.1 关键创新点
1. 创新点
### 4.2 发明目的
解决实际问题。
### 4.3 拟解决的技术问题
1. 解决问题A。
### 4.4 总体技术方案
执行技术步骤。
### 4.12 有益效果
改善运行稳定性。
"""
        report = review_section(
            "四、发明内容",
            content,
            accepted_sections=[background],
        )
        self.assertIn("problem_solution_mismatch", [issue.code for issue in report.issues])

    def test_unsupported_quantified_effect_is_rejected(self) -> None:
        content = """## 四、发明内容
### 4.1 关键创新点
1. 创新点
### 4.2 发明目的
解决问题。
### 4.3 拟解决的技术问题
1. 解决问题。
### 4.4 总体技术方案
执行步骤。
### 4.12 有益效果
运行效率提升30%。
"""
        report = review_section("四、发明内容", content, evidence_text="")
        self.assertIn("unsupported_benefit_number", [issue.code for issue in report.issues])

    def test_protection_scope_requires_multiple_forms(self) -> None:
        report = review_section("五、保护范围", "## 五、保护范围\n保护一种方法。")
        self.assertIn("incomplete_protection_scope", [issue.code for issue in report.issues])

    def test_malformed_formula_is_rejected(self) -> None:
        report = review_section(
            "七、具体实施方式",
            r"## 七、具体实施方式\n目标函数为 \frac{a}{b}。",
        )
        self.assertIn("malformed_formula", [issue.code for issue in report.issues])

    def test_process_metadata_is_rejected_and_stripped(self) -> None:
        content = """## 二、技术领域
本发明属于铝电解过程监测技术领域。

---

**质量审查（自查）：**
- 未使用“基于……的……”句式。
- 本章节已按照 skill 规则处理。
"""
        report = review_section("二、技术领域", content)
        self.assertIn("process_meta_leak", [issue.code for issue in report.issues])
        stripped = strip_process_meta(content)
        self.assertIn("本发明属于铝电解过程监测技术领域", stripped)
        self.assertNotIn("质量审查", stripped)
        self.assertNotIn("未使用", stripped)

    def test_process_metadata_does_not_reject_technical_use_statement(self) -> None:
        content = "## 七、具体实施方式\n系统使用了异常检测方法识别电流分布波动。"
        report = review_section("七、具体实施方式", content)
        self.assertNotIn("process_meta_leak", [issue.code for issue in report.issues])

    def test_complete_document_structure_passes(self) -> None:
        markdown = """## 标题页
文档标题：铝电解槽阳极效应早期预警方法
## 一、发明名称
铝电解槽阳极效应早期预警方法
## 二、技术领域
本发明涉及铝电解生产预警技术领域。
## 三、背景技术
1. 阳极效应早期征兆难以及时识别。
2. 多源运行数据缺少统一分析。
## 四、发明内容
### 4.1 关键创新点
1. 构建多源状态关联机制。
### 4.2 发明目的
实现阳极效应早期预警。
### 4.3 拟解决的技术问题
1. 解决早期征兆识别问题。
2. 解决多源数据联合分析问题。
### 4.4 总体技术方案
采集运行数据并生成预警结果。
### 4.12 有益效果
提高预警过程的一致性。
## 五、保护范围
保护所述预警方法、预警系统以及执行该方法的设备和存储介质。
## 六、附图说明
图1为总体流程图。
## 七、具体实施方式
采集槽电压及相关运行数据，完成处理和预警。
## 八、附图
图1为总体流程图。
"""
        self.assertTrue(review_document(markdown).passed)


if __name__ == "__main__":
    unittest.main()

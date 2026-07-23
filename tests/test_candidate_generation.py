import unittest

from external_search import ExternalSearchResult
from patent_discovery_agent import (
    MaterialAssessment,
    PatentCandidate,
    _ensure_candidate_count,
    _parse_candidates,
)


class CandidateGenerationTests(unittest.TestCase):
    def test_candidate_without_name_is_preserved_with_derived_title(self) -> None:
        raw = """候选1
名称：工业设备状态预测方法
核心方案：预测设备状态。
创新点：融合多源状态变量。

候选2
核心方案：评估数字孪生模型的一致性。
创新点：提出数字孪生的六维一致性评估体系，将拓扑、状态和行为统一校验。
新技术特征：六维一致性指标。
"""

        candidates = _parse_candidates(raw)

        self.assertEqual(2, len(candidates))
        self.assertEqual("工业设备状态预测方法", candidates[0].title)
        self.assertEqual("数字孪生六维一致性评估方法", candidates[1].title)
        self.assertIn("名称：数字孪生六维一致性评估方法", candidates[1].raw)

    def test_single_llm_candidate_is_padded_to_five(self) -> None:
        candidates = _ensure_candidate_count(
            candidates=[
                PatentCandidate(
                    title="铝电解槽阳极效应早期预警方法",
                    summary="核心方案：状态预测。",
                    raw="候选1\n名称：铝电解槽阳极效应早期预警方法",
                )
            ],
            material_text="铝电解槽 数字孪生 电流效率",
            external=ExternalSearchResult(
                enabled=True,
                notes=[],
                results=[{"title": "CN111850609B 铝电解管控系统", "url": "", "snippet": ""}],
            ),
            assessment=MaterialAssessment(
                score=80,
                level="可用",
                reasons=[],
                needs_external_search=False,
            ),
            raw="候选1\n名称：铝电解槽阳极效应早期预警方法",
            target=5,
        )

        self.assertEqual(5, len(candidates))
        self.assertEqual("铝电解槽阳极效应早期预警方法", candidates[0].title)
        self.assertTrue(all(candidate.title for candidate in candidates))
        fallback_raw = candidates[1].raw
        self.assertIn("未复用已有技术特征", fallback_raw)
        self.assertIn("新技术特征", fallback_raw)
        self.assertIn("技术效果来源", fallback_raw)
        self.assertIn("重合风险", fallback_raw)
        self.assertIn("人工确认点", fallback_raw)


if __name__ == "__main__":
    unittest.main()

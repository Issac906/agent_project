import unittest

from external_search import ExternalSearchResult
from patent_discovery_agent import (
    MaterialAssessment,
    PatentCandidate,
    _ensure_candidate_count,
)


class CandidateGenerationTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()

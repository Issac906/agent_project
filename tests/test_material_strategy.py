import unittest

from external_search import ExternalSearchResult
from material_strategy import build_material_strategy
from patent_discovery_agent import PatentCandidate


class MaterialStrategyTests(unittest.TestCase):
    def test_builds_layers_and_candidate_paths(self) -> None:
        strategy = build_material_strategy(
            documents=[
                {
                    "file_path": "aluminium_process.pdf",
                    "chunks_count": 12,
                    "content_summary": "铝电解槽生产场景存在电流效率和能耗问题。",
                },
                {
                    "file_path": "model_report.html",
                    "chunks_count": 8,
                    "content_summary": "数字孪生模型使用温度、电流、电压等数据指标进行预测控制。",
                },
            ],
            external=ExternalSearchResult(
                enabled=True,
                notes=[],
                results=[
                    {
                        "title": "CN111850609B 铝电解管控系统",
                        "url": "https://patents.google.com/patent/CN111850609B/zh",
                        "snippet": "公开了一种基于数字孪生的铝电解管控系统。",
                    }
                ],
            ),
            candidates=[
                PatentCandidate(
                    title="铝电解槽状态预测方法",
                    summary="核心方案：融合数据指标进行预测。",
                    raw="候选1\n名称：铝电解槽状态预测方法",
                )
            ],
        )

        self.assertIn("本次读取 2 份知识库材料", strategy["summary"])
        self.assertIn("不作为候选专利的核心创新来源", strategy["external_usage_boundary"])
        self.assertGreaterEqual(len(strategy["layers"]), 5)
        self.assertEqual(1, len(strategy["candidate_paths"]))
        self.assertTrue(strategy["candidate_paths"][0]["source_layers"])
        self.assertIn("不把外部已有方案作为创新来源", strategy["candidate_paths"][0]["idea_rationale"])


if __name__ == "__main__":
    unittest.main()

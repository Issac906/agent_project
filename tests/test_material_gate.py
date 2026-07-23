from __future__ import annotations

import unittest
from unittest.mock import patch

from app import MAX_SEARCH_NO_PROGRESS_ROUNDS, MATERIAL_READY_SCORE, WebPatentRun
from config import AppConfig
from external_search import ExternalSearchResult
from patent_discovery_agent import MaterialAssessment


def _config() -> AppConfig:
    return AppConfig(
        lightrag_base_url="http://example.test",
        lightrag_api_key=None,
        lightrag_query_mode="mix",
        lightrag_top_k=None,
        lightrag_include_chunk_content=True,
        kb_manager_url=None,
        kb_manager_api_key=None,
        kb_manager_timeout=240,
        llm_provider="none",
        llm_api_key=None,
        llm_base_url=None,
        llm_model=None,
        search_provider="duckduckgo",
        search_api_key=None,
        agent_core="pi_coding_agent",
        codex_command="codex",
        codex_model=None,
        codex_enable_search=True,
        codex_sandbox="read-only",
        codex_timeout=600,
        pi_command="pi",
        pi_provider="deepseek",
        pi_model="deepseek-chat",
        pi_timeout=600,
    )


class MaterialGateTests(unittest.TestCase):
    def test_reassessment_below_threshold_runs_supplement_search_first(self) -> None:
        run = WebPatentRun(_config())
        run.phase = "searched"
        run.search_round = 1
        run.search_topic = "铝电解槽状态检测"
        run.documents = {"_counts": {}, "rows": []}
        run.external = ExternalSearchResult(
            enabled=True,
            notes=[],
            results=[{"title": "相似专利", "snippet": "专利 技术方案", "url": "https://example.test"}],
        )
        low_assessment = MaterialAssessment(
            score=MATERIAL_READY_SCORE - 1,
            level="基本可用",
            reasons=["仍需补充"],
            needs_external_search=True,
            project_score=50,
            prior_art_score=20,
            dimensions=[],
            capped_by=[],
        )

        with patch("app._assess_materials", return_value=low_assessment), patch(
            "app._generate_candidates"
        ) as generate_candidates, patch("app.search_external_materials") as search:
            search.return_value = ExternalSearchResult(
                enabled=True,
                notes=["补充检索"],
                results=[{"title": "补充资料", "snippet": "实验 数据 指标", "url": "https://example.test/2"}],
            )
            run.advance()

        self.assertEqual("searched", run.phase)
        self.assertIsNone(run.waiting_for)
        self.assertEqual(2, run.search_round)
        self.assertEqual(2, len(run.external.results))
        generate_candidates.assert_not_called()

    def test_reassessment_below_threshold_keeps_searching_after_many_rounds(self) -> None:
        run = WebPatentRun(_config())
        run.phase = "searched"
        run.search_round = 20
        run.search_topic = "铝电解槽状态检测"
        run.base_search_topic = "铝电解槽状态检测"
        run.documents = {"_counts": {}, "rows": []}
        run.external = ExternalSearchResult(
            enabled=True,
            notes=[],
            results=[{"title": "相似专利", "snippet": "专利 技术方案", "url": "https://example.test"}],
        )
        low_assessment = MaterialAssessment(
            score=MATERIAL_READY_SCORE - 1,
            level="基本可用",
            reasons=["仍需补充"],
            needs_external_search=True,
            project_score=50,
            prior_art_score=20,
            dimensions=[],
            capped_by=[],
        )

        with patch("app._assess_materials", return_value=low_assessment), patch(
            "app._generate_candidates"
        ) as generate_candidates, patch("app.search_external_materials") as search:
            search.return_value = ExternalSearchResult(
                enabled=True,
                notes=["继续检索"],
                results=[{"title": "更多资料", "snippet": "权利要求 CN", "url": "https://example.test/3"}],
            )
            run.advance()

        self.assertEqual("searched", run.phase)
        self.assertIsNone(run.waiting_for)
        self.assertEqual(21, run.search_round)
        search.assert_called_once()
        generate_candidates.assert_not_called()

    def test_repeated_empty_search_stops_with_recoverable_error(self) -> None:
        run = WebPatentRun(_config())
        run.phase = "searched"
        run.search_round = 2
        run.search_no_progress_rounds = MAX_SEARCH_NO_PROGRESS_ROUNDS - 1
        run.search_topic = "铝电解槽状态检测"
        run.documents = {"_counts": {}, "rows": []}
        run.external = ExternalSearchResult(enabled=True, notes=[], results=[])
        low_assessment = MaterialAssessment(
            score=MATERIAL_READY_SCORE - 1,
            level="不足",
            reasons=["仍需补充"],
            needs_external_search=True,
            project_score=50,
            prior_art_score=0,
            dimensions=[],
            capped_by=[],
        )

        with patch("app._assess_materials", return_value=low_assessment), patch(
            "app.search_external_materials",
            return_value=ExternalSearchResult(
                enabled=True,
                notes=["搜索服务未返回结果"],
                results=[],
            ),
        ):
            run.advance()

        self.assertIn("连续", run.error or "")
        self.assertEqual(MAX_SEARCH_NO_PROGRESS_ROUNDS, run.search_no_progress_rounds)
        self.assertEqual("searched", run.phase)


if __name__ == "__main__":
    unittest.main()

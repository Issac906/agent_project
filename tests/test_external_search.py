import json
import os
import unittest
from unittest.mock import patch

from external_search import (
    _configured_searchers,
    _filter_patent_relevant,
    _parse_anysearch_results,
    _parse_google_patents_results,
)


class ExternalSearchTests(unittest.TestCase):
    def test_parse_google_patents_results(self) -> None:
        payload = json.dumps(
            {
                "results": {
                    "cluster": [
                        {
                            "result": [
                                {
                                    "id": "patent/CN123456A/zh",
                                    "patent": {
                                        "title": "铝电解槽控制方法",
                                        "snippet": "涉及电流效率和状态预测。",
                                        "publication_number": "CN123456A",
                                    },
                                }
                            ]
                        }
                    ]
                }
            }
        )

        results = _parse_google_patents_results(payload, max_results=5)

        self.assertEqual(1, len(results))
        self.assertIn("CN123456A", results[0]["title"])
        self.assertEqual("https://patents.google.com/patent/CN123456A/zh", results[0]["url"])

    def test_filter_patent_relevant_results(self) -> None:
        results = _filter_patent_relevant(
            [
                {"title": "铝（金属元素）_百度百科", "url": "https://baike.baidu.com", "snippet": ""},
                {"title": "铝电解槽控制方法专利", "url": "https://example.com", "snippet": "权利要求"},
            ],
            max_results=5,
        )

        self.assertEqual(1, len(results))
        self.assertIn("专利", results[0]["title"])

    def test_configured_searchers_prioritize_anysearch(self) -> None:
        with patch.dict(
            os.environ,
            {
                "SEARCH_PROVIDER": "anysearch",
                "SEARCH_API_KEY": "test-key",
                "ANYSEARCH_BASE_URL": "https://search.example/api",
            },
            clear=False,
        ):
            providers = [name for name, _ in _configured_searchers()]

        self.assertEqual("AnySearch", providers[0])
        self.assertIn("GooglePatents", providers)
        self.assertIn("Bing", providers)

    def test_parse_anysearch_common_results_shape(self) -> None:
        payload = {
            "results": [
                {
                    "title": "铝电解槽阳极效应预测专利",
                    "url": "https://example.com/patent",
                    "snippet": "公开了一种预测方法。",
                }
            ]
        }

        results = _parse_anysearch_results(payload, max_results=5)

        self.assertEqual(1, len(results))
        self.assertEqual("AnySearch", results[0]["source"])
        self.assertIn("阳极效应", results[0]["title"])


if __name__ == "__main__":
    unittest.main()

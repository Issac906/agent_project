from __future__ import annotations

import unittest

from citation_report import append_citation_section, build_citation_snapshot
from external_search import ExternalSearchResult


class CitationReportTests(unittest.TestCase):
    def test_builds_knowledge_and_external_citations(self) -> None:
        citations = build_citation_snapshot(
            {
                "documents": [
                    {
                        "id": "doc-1",
                        "file_path": "example.pdf",
                        "status": "processed",
                        "chunks_count": 8,
                        "content_summary": "介绍电解槽温度、电流效率和控制方案。",
                    }
                ]
            },
            ExternalSearchResult(
                enabled=True,
                notes=["已执行外部搜索"],
                results=[
                    {
                        "title": "相似专利",
                        "url": "https://example.test/patent",
                        "snippet": "公开了一种电解槽状态检测技术方案。",
                    }
                ],
            ),
            "铝电解槽 专利",
        )

        self.assertEqual("K1", citations["knowledge"][0]["id"])
        self.assertEqual("example.pdf", citations["knowledge"][0]["document"])
        self.assertIn("电解槽温度", citations["knowledge"][0]["quoted_content"])
        self.assertEqual("W1", citations["external"][0]["id"])
        self.assertIn("相似专利", citations["external"][0]["title"])

    def test_builds_knowledge_citations_from_lightrag_statuses(self) -> None:
        citations = build_citation_snapshot(
            {
                "statuses": {
                    "processed": [
                        {
                            "id": "doc-a",
                            "file_path": "aluminium.pdf",
                            "chunks_count": 12,
                            "content_summary": "介绍铝电解槽运行状态和电流效率。",
                        }
                    ]
                },
                "_counts": {"status_counts": {"processed": 1}},
            },
            ExternalSearchResult(enabled=True, notes=[], results=[]),
            "铝电解槽",
        )

        self.assertEqual(1, len(citations["knowledge"]))
        self.assertEqual("processed", citations["knowledge"][0]["status"])
        self.assertEqual("aluminium.pdf", citations["knowledge"][0]["document"])
        self.assertIn("电流效率", citations["knowledge"][0]["quoted_content"])

    def test_appends_citation_section_to_final_markdown(self) -> None:
        citations = {
            "search_topic": "铝电解槽 专利",
            "knowledge": [
                {
                    "id": "K1",
                    "document": "example.pdf",
                    "document_id": "doc-1",
                    "status": "processed",
                    "chunks_count": 8,
                    "quoted_content": "电解槽温度、电流效率和控制方案。",
                }
            ],
            "external": [
                {
                    "id": "W1",
                    "title": "相似专利",
                    "url": "https://example.test/patent",
                    "quoted_content": "电解槽状态检测技术方案。",
                }
            ],
            "notes": [],
        }
        markdown = append_citation_section("# 正文\n\n专利内容。", citations)

        self.assertIn("## 九、引用说明", markdown)
        self.assertIn("[K1] example.pdf", markdown)
        self.assertIn("[W1] 相似专利", markdown)


if __name__ == "__main__":
    unittest.main()

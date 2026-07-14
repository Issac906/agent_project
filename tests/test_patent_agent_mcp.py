import json
import unittest
from unittest.mock import patch

from patent_agent_bridge import (
    BackendEndpoint,
    PatentAgentClient,
    TOOLS_BY_NAME,
    _compact_state,
    _continue_until_input,
    _list_history,
)
from patent_agent_mcp import _handle


class PatentAgentBridgeTests(unittest.TestCase):
    def test_required_chat_tools_are_registered(self) -> None:
        expected = {
            "patent_list_knowledge_bases",
            "patent_list_active_runs",
            "patent_discard_run",
            "patent_start_run",
            "patent_retry_search",
            "patent_list_ideas",
            "patent_select_idea",
            "patent_accept_section",
            "patent_rewrite_section",
            "patent_revise_section",
            "patent_manual_edit_section",
            "patent_finish_run",
            "patent_list_history",
            "patent_get_history",
        }
        self.assertTrue(expected.issubset(TOOLS_BY_NAME))

    def test_safety_limit_preserves_run_id(self) -> None:
        client = PatentAgentClient(BackendEndpoint("http://127.0.0.1:5001"))
        client.request = lambda *_args, **_kwargs: {
            "id": "recoverable-run",
            "phase": "searched",
            "waiting_for": None,
            "done": False,
            "error": None,
            "events": [],
            "candidates": [],
            "section": {},
        }
        result = _continue_until_input(client, "recoverable-run", max_steps=2)
        self.assertEqual("recoverable-run", result["run_id"])
        self.assertTrue(result["requires_attention"])
        self.assertEqual(2, result["automatic_steps"])

    def test_compact_state_exposes_download_urls(self) -> None:
        client = PatentAgentClient(BackendEndpoint("http://127.0.0.1:5001"))
        state = {
            "id": "run1",
            "phase": "done",
            "done": True,
            "artifacts": {"docx": "/outputs/result.docx"},
            "candidates": [],
            "events": [],
            "section": {},
        }
        compact = _compact_state(client, state)
        self.assertEqual(
            compact["artifacts"]["docx"]["download_url"],
            "http://127.0.0.1:5001/outputs/result.docx",
        )

    def test_history_list_is_compact_and_links_are_absolute(self) -> None:
        client = PatentAgentClient(BackendEndpoint("http://127.0.0.1:5001"))
        client.request = lambda *_args, **_kwargs: {
            "records": [
                {
                    "id": "record1",
                    "run_id": "run1",
                    "title": "测试专利",
                    "assessment": {"dimensions": ["large payload"]},
                    "detail_url": "/history/record1",
                    "artifacts": {"docx": "/outputs/history/record1/result.docx"},
                }
            ]
        }
        result = _list_history(client, {"limit": 10})
        record = result["records"][0]
        self.assertNotIn("assessment", record)
        self.assertEqual(record["detail_url"], "http://127.0.0.1:5001/history/record1")
        self.assertEqual(
            record["artifacts"]["docx"],
            "http://127.0.0.1:5001/outputs/history/record1/result.docx",
        )

    @patch("patent_agent_mcp._write")
    def test_mcp_tools_list(self, write) -> None:
        _handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        payload = write.call_args.args[0]
        self.assertEqual(payload["id"], 2)
        names = {item["name"] for item in payload["result"]["tools"]}
        self.assertIn("patent_start_run", names)


if __name__ == "__main__":
    unittest.main()

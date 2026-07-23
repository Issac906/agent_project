from pathlib import Path
import tempfile
import unittest

from feishu_agent import FeishuPatentAgent
from feishu_integration import FeishuIncoming, FeishuStateStore


class RecordingSink:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def markdown(self, title: str, text: str) -> None:
        self.messages.append((title, text))


class FakeWorkflowAPI:
    def __init__(self) -> None:
        self.phase = "documents_loaded"
        self.waiting_for = None
        self.section_number = 0
        self.section_actions: list[str] = []
        self.run_payloads: list[dict] = []
        self.selected_indexes: list[int] = []

    def __call__(self, method: str, path: str, payload=None):
        if path == "/api/knowledge":
            return {
                "documents": [{"id": "doc-1"}],
                "knowledge_bases": [{"id": "kb-1", "name": "工业知识库", "document_count": 1}],
            }
        if path == "/api/runs" and method == "POST":
            self.run_payloads.append(dict(payload or {}))
            return {"run_id": "run-1"}
        if path.endswith("/select"):
            self.selected_indexes.append(payload["index"])
            self.phase = "selected"
            self.waiting_for = None
            return self.state()
        if path.endswith("/section"):
            self.section_actions.append(payload["action"])
            if payload["action"] == "accept":
                self.section_number += 1
                self.phase = "selected"
                self.waiting_for = None
            return self.state()
        if path.endswith("/advance"):
            if self.phase == "documents_loaded":
                self.phase = "waiting_candidate"
                self.waiting_for = "candidate"
            elif self.phase == "selected":
                self.phase = "waiting_section"
                self.waiting_for = "section"
            return self.state()
        if method == "GET" and path == "/api/runs/run-1":
            return self.state()
        raise AssertionError((method, path, payload))

    def state(self):
        return {
            "id": "run-1",
            "phase": self.phase,
            "waiting_for": self.waiting_for,
            "error": None,
            "done": False,
            "candidates": [
                {"title": "候选一", "summary": "方案一摘要", "raw": "方案一完整技术内容"},
                {"title": "候选二", "summary": "方案二摘要", "raw": "方案二完整技术内容"},
            ],
            "section": {
                "index": self.section_number,
                "total": 9,
                "name": f"章节{self.section_number + 1}",
                "content": f"第{self.section_number + 1}章的完整正文",
                "quality": {"score": 90, "passed": True},
            },
        }


class FeishuAgentTests(unittest.TestCase):
    def test_scheduled_run_replaces_stale_persisted_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = FeishuStateStore(Path(directory) / "state.json")
            incoming = FeishuIncoming(
                message_id="",
                text="开始生成",
                chat_id="",
                chat_type="p2p",
                sender_open_id="ou-user-1",
            )
            store.save_session_for(
                incoming.session_keys,
                {"stage": "choose_candidate", "run_id": "stale-run"},
            )
            agent = FeishuPatentAgent(store)
            fake_api = FakeWorkflowAPI()

            def api(method: str, path: str, payload=None):
                if method == "GET" and path == "/api/runs/stale-run":
                    raise RuntimeError("run no longer exists")
                return fake_api(method, path, payload)

            agent._api = api  # type: ignore[method-assign]
            sink = RecordingSink()
            agent.start_scheduled(
                incoming,
                sink,
                {
                    "id": "schedule-retry",
                    "knowledge_base_id": "kb-1",
                    "innovation_level": "medium",
                },
            )

            self.assertEqual("choose_candidate", store.session("ou-user-1")["stage"])
            self.assertEqual("run-1", store.session("ou-user-1")["run_id"])
            self.assertEqual(1, len(fake_api.run_payloads))

    def test_private_scheduled_run_continues_when_reply_contains_a_chat_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = FeishuStateStore(Path(directory) / "state.json")
            agent = FeishuPatentAgent(store)
            fake_api = FakeWorkflowAPI()
            agent._api = fake_api  # type: ignore[method-assign]
            sink = RecordingSink()
            scheduled = FeishuIncoming(
                message_id="",
                text="开始生成",
                chat_id="",
                chat_type="p2p",
                sender_open_id="ou-user-1",
            )

            agent.start_scheduled(
                scheduled,
                sink,
                {
                    "id": "schedule-private",
                    "knowledge_base_id": "kb-1",
                    "innovation_level": "medium",
                },
            )
            self.assertEqual("choose_candidate", store.session("ou-user-1")["stage"])

            reply = FeishuIncoming(
                message_id="message-1",
                text="2",
                chat_id="oc-private-chat",
                chat_type="p2p",
                sender_open_id="ou-user-1",
            )
            agent.handle(reply, sink)

            self.assertEqual([1], fake_api.selected_indexes)
            self.assertEqual("confirm_section", store.session("ou-user-1")["stage"])
            self.assertEqual("confirm_section", store.session("oc-private-chat")["stage"])
            self.assertIn("第1章的完整正文", "\n".join(text for _, text in sink.messages))

    def test_scheduled_run_uses_saved_defaults_and_advances_to_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = FeishuStateStore(Path(directory) / "state.json")
            agent = FeishuPatentAgent(store)
            fake_api = FakeWorkflowAPI()
            agent._api = fake_api  # type: ignore[method-assign]
            sink = RecordingSink()
            incoming = FeishuIncoming(
                message_id="",
                text="开始生成",
                chat_id="cid-1",
                chat_type="group",
                sender_open_id="",
            )

            agent.start_scheduled(
                incoming,
                sink,
                {
                    "id": "schedule-1",
                    "knowledge_base_id": "kb-1",
                    "innovation_level": "high",
                    "reminder_text": "本周任务开始。",
                },
            )

            self.assertEqual(
                [{"knowledge_base_id": "kb-1", "innovation_level": "high", "channel": "feishu"}],
                fake_api.run_payloads,
            )
            self.assertEqual("choose_candidate", store.session("cid-1")["stage"])
            rendered = "\n".join(text for _, text in sink.messages)
            self.assertIn("本周任务开始", rendered)
            self.assertIn("工业知识库", rendered)
            self.assertIn("方案一完整技术内容", rendered)

    def test_candidate_and_each_section_are_sent_in_full(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = FeishuStateStore(Path(directory) / "state.json")
            agent = FeishuPatentAgent(store)
            fake_api = FakeWorkflowAPI()
            agent._api = fake_api  # type: ignore[method-assign]
            sink = RecordingSink()
            incoming = FeishuIncoming(
                message_id="m1",
                text="开始生成",
                chat_id="cid-1",
                chat_type="group",
                sender_union_id="sender",
                sender_open_id="ou_example",
                sender_name="用户",
                thread_id="https://example.invalid",
            )

            agent.handle(incoming, sink)
            incoming = FeishuIncoming(**{**incoming.__dict__, "message_id": "m2", "text": "2"})
            agent.handle(incoming, sink)
            incoming = FeishuIncoming(**{**incoming.__dict__, "message_id": "m3", "text": "中"})
            agent.handle(incoming, sink)

            rendered = "\n".join(text for _, text in sink.messages)
            self.assertIn("方案一完整技术内容", rendered)
            self.assertIn("方案二完整技术内容", rendered)

            incoming = FeishuIncoming(**{**incoming.__dict__, "message_id": "m4", "text": "1"})
            agent.handle(incoming, sink)
            self.assertIn("第1章的完整正文", "\n".join(text for _, text in sink.messages))

            incoming = FeishuIncoming(**{**incoming.__dict__, "message_id": "m5", "text": "接受"})
            agent.handle(incoming, sink)
            rendered = "\n".join(text for _, text in sink.messages)
            self.assertIn("第2章的完整正文", rendered)
            self.assertEqual(["accept"], fake_api.section_actions)

    def test_retry_resends_the_persisted_current_section(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = FeishuStateStore(Path(directory) / "state.json")
            agent = FeishuPatentAgent(store)
            fake_api = FakeWorkflowAPI()
            fake_api.phase = "waiting_section"
            fake_api.waiting_for = "section"
            agent._api = fake_api  # type: ignore[method-assign]
            incoming = FeishuIncoming(
                message_id="retry-1",
                text="重试",
                chat_id="oc-private-chat",
                chat_type="p2p",
                sender_open_id="ou-user-1",
            )
            store.save_session_for(
                incoming.session_keys,
                {"stage": "confirm_section", "run_id": "run-1"},
            )
            sink = RecordingSink()

            agent.handle(incoming, sink)

            self.assertEqual("confirm_section", store.session("ou-user-1")["stage"])
            rendered = "\n".join(text for _, text in sink.messages)
            self.assertIn("第1章的完整正文", rendered)
            self.assertIn("等待确认", "\n".join(title for title, _ in sink.messages))


if __name__ == "__main__":
    unittest.main()

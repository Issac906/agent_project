import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import requests

from feishu_integration import (
    FeishuConfig,
    FeishuIntegrationManager,
    FeishuOpenAPI,
    FeishuMessageSink,
    FeishuStateStore,
    _plain_chat_text,
    incoming_from_event,
    split_markdown,
    schedule_trigger,
    validate_schedule,
)


class FeishuIntegrationTests(unittest.TestCase):
    def test_markdown_is_rendered_as_readable_chat_text(self) -> None:
        rendered = _plain_chat_text("## 标题\n\n**重点**与[历史](https://example.com/history)")
        self.assertEqual("标题\n\n重点与历史：https://example.com/history", rendered)

    def test_long_markdown_is_split_without_content_loss(self) -> None:
        content = "# 标题\n\n" + ("完整章节内容。" * 1200) + "\n结尾"
        chunks = split_markdown(content, limit=500)

        self.assertGreater(len(chunks), 2)
        self.assertTrue(all(len(chunk) <= 500 for chunk in chunks))
        self.assertEqual(content, "".join(chunks))

    def test_schedule_accepts_arbitrary_valid_cron(self) -> None:
        schedule = validate_schedule(
            {
                "name": "每两小时提醒",
                "cron": "15 */2 * * *",
                "timezone": "Asia/Shanghai",
                "target_type": "group",
                "target_id": "cid-example",
            }
        )

        self.assertEqual("15 */2 * * *", schedule["cron"])
        self.assertEqual("cid-example", schedule["target_id"])

    def test_visual_weekly_schedule_builds_cron_and_trigger(self) -> None:
        schedule = validate_schedule(
            {
                "schedule_type": "recurring",
                "repeat_type": "weekly",
                "send_time": "14:35",
                "weekday": 4,
                "timezone": "Asia/Shanghai",
                "target_type": "group",
                "target_id": "cid-example",
            }
        )

        self.assertEqual("35 14 * * 4", schedule["cron"])
        self.assertEqual("weekly", schedule["repeat_type"])
        self.assertIsNotNone(schedule_trigger(schedule))

    def test_visual_monthly_schedule_uses_selected_calendar_day(self) -> None:
        schedule = validate_schedule(
            {
                "schedule_type": "recurring",
                "repeat_type": "monthly",
                "send_time": "09:10",
                "monthday": 18,
                "timezone": "Asia/Shanghai",
                "target_type": "user",
                "target_id": "ou-example",
            }
        )

        self.assertEqual("10 9 18 * *", schedule["cron"])
        self.assertEqual(18, schedule["monthday"])

    def test_delay_schedule_creates_future_one_time_trigger(self) -> None:
        schedule = validate_schedule(
            {
                "schedule_type": "once",
                "delay_value": 5,
                "delay_unit": "minutes",
                "timezone": "Asia/Shanghai",
                "target_type": "group",
                "target_id": "cid-example",
            }
        )

        self.assertEqual("once", schedule["schedule_type"])
        self.assertTrue(schedule["run_at"])
        self.assertEqual("", schedule["cron"])
        self.assertIsNotNone(schedule_trigger(schedule))

    def test_one_time_schedule_completion_is_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = FeishuStateStore(Path(directory) / "feishu.json")
            schedule = store.save_schedule(
                {
                    "schedule_type": "once",
                    "delay_value": 5,
                    "delay_unit": "minutes",
                    "timezone": "Asia/Shanghai",
                    "target_type": "group",
                    "target_id": "cid-example",
                }
            )

            store.finish_schedule(schedule["id"], ok=True)
            completed = store.schedules()[0]
            self.assertFalse(completed["enabled"])
            self.assertTrue(completed["completed_at"])
            self.assertTrue(completed["last_delivery_ok"])

    def test_one_time_scheduler_executes_agent_handler_and_completes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            calls: list[str] = []
            manager = FeishuIntegrationManager(
                lambda incoming, sink: None,
                lambda incoming, sink, schedule: calls.append(schedule["id"]),
            )
            manager.store = FeishuStateStore(Path(directory) / "feishu.json")
            schedule = manager.store.save_schedule(
                {
                    "schedule_type": "once",
                    "delay_value": 5,
                    "delay_unit": "minutes",
                    "timezone": "Asia/Shanghai",
                    "target_type": "group",
                    "target_id": "cid-example",
                }
            )

            manager._execute_schedule(schedule)

            self.assertEqual([schedule["id"]], calls)
            completed = manager.store.schedules()[0]
            self.assertFalse(completed["enabled"])
            self.assertTrue(completed["last_delivery_ok"])

    def test_state_store_persists_schedules_and_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "feishu.json"
            store = FeishuStateStore(path)
            schedule = store.save_schedule(
                {
                    "cron": "0 9 * * 1",
                    "timezone": "Asia/Shanghai",
                    "target_type": "group",
                    "target_id": "cid-example",
                }
            )
            store.save_session("cid-example", {"stage": "choose_candidate", "run_id": "abc"})

            reloaded = FeishuStateStore(path)
            self.assertEqual(schedule["id"], reloaded.schedules()[0]["id"])
            self.assertEqual("abc", reloaded.session("cid-example")["run_id"])
            self.assertIn("schedules", json.loads(path.read_text(encoding="utf-8")))

    def test_message_event_removes_bot_mention_and_keeps_ids(self) -> None:
        message = SimpleNamespace(
            message_id="om_1",
            content=json.dumps({"text": "@_user_1 开始生成"}),
            chat_id="oc_1",
            chat_type="group",
            thread_id="",
            mentions=[SimpleNamespace(key="@_user_1")],
        )
        sender_id = SimpleNamespace(open_id="ou_1", union_id="on_1", user_id="u_1")
        event = SimpleNamespace(message=message, sender=SimpleNamespace(sender_id=sender_id))

        incoming = incoming_from_event(SimpleNamespace(event=event))

        self.assertEqual("开始生成", incoming.text)
        self.assertEqual("oc_1", incoming.chat_id)
        self.assertEqual("ou_1", incoming.sender_open_id)

    @patch("feishu_integration.requests.post")
    def test_open_api_uses_chat_id_for_group_message(self, post: Mock) -> None:
        token_response = Mock()
        token_response.raise_for_status.return_value = None
        token_response.json.return_value = {"code": 0, "tenant_access_token": "token", "expire": 7200}
        send_response = Mock()
        send_response.status_code = 200
        send_response.raise_for_status.return_value = None
        send_response.json.return_value = {"code": 0, "data": {}}
        post.side_effect = [token_response, send_response]
        api = FeishuOpenAPI(
            FeishuConfig(True, "cli_test", "secret", "https://open.feishu.cn", "")
        )

        api.send_text("group", "oc_test", "标题", "正文")

        request = post.call_args_list[1]
        self.assertEqual({"receive_id_type": "chat_id"}, request.kwargs["params"])
        self.assertEqual("oc_test", request.kwargs["json"]["receive_id"])

    @patch("feishu_integration.requests.post")
    def test_open_api_surfaces_feishu_error_code(self, post: Mock) -> None:
        token_response = Mock()
        token_response.raise_for_status.return_value = None
        token_response.json.return_value = {"code": 0, "tenant_access_token": "token", "expire": 7200}
        send_response = Mock(status_code=400, reason="Bad Request")
        send_response.json.return_value = {"code": 99991672, "msg": "Access denied"}
        post.side_effect = [token_response, send_response]
        api = FeishuOpenAPI(
            FeishuConfig(True, "cli_test", "secret", "https://open.feishu.cn", "")
        )

        with self.assertRaisesRegex(RuntimeError, "99991672"):
            api.send_text("group", "oc_test", "标题", "正文")

    @patch("feishu_integration.time.sleep")
    @patch("feishu_integration.requests.post")
    def test_open_api_retries_transient_ssl_failure(self, post: Mock, sleep: Mock) -> None:
        token_response = Mock()
        token_response.raise_for_status.return_value = None
        token_response.json.return_value = {"code": 0, "tenant_access_token": "token", "expire": 7200}
        send_response = Mock(status_code=200)
        send_response.json.return_value = {"code": 0, "data": {}}
        post.side_effect = [
            token_response,
            requests.exceptions.SSLError("unexpected eof"),
            send_response,
        ]
        api = FeishuOpenAPI(
            FeishuConfig(True, "cli_test", "secret", "https://open.feishu.cn", "")
        )

        api.send_text("group", "oc_test", "标题", "正文")

        self.assertEqual(3, post.call_count)
        sleep.assert_called_once()

    def test_message_sink_sends_interactive_card(self) -> None:
        api = Mock(spec=FeishuOpenAPI)
        api.upload_image.side_effect = lambda name, payload: "img_key"
        sink = FeishuMessageSink(api=api, target_type="group", target_id="oc_test")

        sink.markdown("章节", "## 标题\n\n公式：$$E=mc^2$$")

        api.send_card.assert_called_once()
        card = api.send_card.call_args.args[2]
        self.assertEqual("markdown", card["elements"][0]["tag"])
        self.assertEqual("img", card["elements"][1]["tag"])
        self.assertEqual("img_key", card["elements"][1]["img_key"])


if __name__ == "__main__":
    unittest.main()

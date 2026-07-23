"""Feishu long-connection transport, proactive messages, and schedules."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta
import json
import logging
import os
from pathlib import Path
import re
from threading import Lock, Thread
import time
from typing import Any, Callable
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests

from feishu_rendering import build_feishu_cards

from runtime_paths import data_path
from tool_registry import register_tool
from user_config import load_user_config

try:
    import lark_oapi as lark
except ImportError:  # pragma: no cover - reported by status endpoint
    lark = None  # type: ignore[assignment]

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.date import DateTrigger
except ImportError:  # pragma: no cover - reported by status endpoint
    BackgroundScheduler = None  # type: ignore[assignment,misc]
    CronTrigger = None  # type: ignore[assignment,misc]
    DateTrigger = None  # type: ignore[assignment,misc]


LOGGER = logging.getLogger(__name__)
STATE_PATH = data_path("feishu", "state.json")
DEFAULT_API_BASE = "https://open.feishu.cn"
DEFAULT_TIMEZONE = "Asia/Shanghai"
MAX_MESSAGE_CHARS = 3600
FEISHU_REQUEST_ATTEMPTS = 4
FEISHU_TRANSIENT_STATUSES = {429, 500, 502, 503, 504}


class FeishuTransportError(RuntimeError):
    """Raised after transient Feishu transport failures exhaust retries."""


@dataclass(frozen=True)
class FeishuConfig:
    enabled: bool
    app_id: str
    app_secret: str
    api_base_url: str
    public_base_url: str

    @classmethod
    def load(cls) -> "FeishuConfig":
        values = load_user_config()

        def value(name: str, default: str = "") -> str:
            return str(values.get(name) or os.getenv(name) or default).strip()

        return cls(
            enabled=value("FEISHU_ENABLED", "false").lower() in {"1", "true", "yes", "on"},
            app_id=value("FEISHU_APP_ID"),
            app_secret=value("FEISHU_APP_SECRET"),
            api_base_url=value("FEISHU_API_BASE_URL", DEFAULT_API_BASE).rstrip("/"),
            public_base_url=value("FEISHU_PUBLIC_BASE_URL").rstrip("/"),
        )

    @property
    def ready(self) -> bool:
        return bool(self.enabled and self.app_id and self.app_secret)


@dataclass(frozen=True)
class FeishuIncoming:
    message_id: str
    text: str
    chat_id: str
    chat_type: str
    sender_open_id: str
    sender_union_id: str = ""
    sender_user_id: str = ""
    sender_name: str = ""
    thread_id: str = ""

    @property
    def session_keys(self) -> tuple[str, ...]:
        """Return stable and legacy keys for one conversational session.

        A scheduled direct message starts with only a user Open ID, while the
        reply event also contains a p2p Chat ID. Keeping both aliases prevents
        the conversation from being lost when the user answers the reminder.
        Group sessions remain scoped to the group Chat ID.
        """

        if self.chat_type == "group":
            candidates = [self.chat_id]
        else:
            candidates = [
                self.sender_open_id,
                self.chat_id,
                self.sender_union_id,
                self.sender_user_id,
            ]
        return tuple(dict.fromkeys(value for value in candidates if value))

    @property
    def session_key(self) -> str:
        return self.session_keys[0] if self.session_keys else ""


def split_markdown(text: str, limit: int = MAX_MESSAGE_CHARS) -> list[str]:
    """Split a complete message without dropping any content."""

    value = str(text or "")
    if not value.strip():
        return ["（无内容）"]
    chunks: list[str] = []
    current = ""
    for paragraph in value.splitlines(keepends=True):
        while len(paragraph) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(paragraph[:limit])
            paragraph = paragraph[limit:]
        if len(current) + len(paragraph) > limit and current:
            chunks.append(current)
            current = ""
        current += paragraph
    if current:
        chunks.append(current)
    return chunks or ["（无内容）"]


class FeishuStateStore:
    def __init__(self, path: Path = STATE_PATH) -> None:
        self.path = path
        self.lock = Lock()

    def _read_unlocked(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        payload.setdefault("schedules", [])
        payload.setdefault("sessions", {})
        payload.setdefault("processed_messages", [])
        payload.setdefault("deliveries", [])
        return payload

    def _write_unlocked(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    def schedules(self) -> list[dict[str, Any]]:
        with self.lock:
            return list(self._read_unlocked()["schedules"])

    def save_schedule(self, values: dict[str, Any], schedule_id: str | None = None) -> dict[str, Any]:
        schedule = validate_schedule(values, schedule_id=schedule_id)
        with self.lock:
            payload = self._read_unlocked()
            rows = [row for row in payload["schedules"] if row.get("id") != schedule["id"]]
            payload["schedules"] = [*rows, schedule]
            self._write_unlocked(payload)
        return schedule

    def delete_schedule(self, schedule_id: str) -> bool:
        with self.lock:
            payload = self._read_unlocked()
            before = len(payload["schedules"])
            payload["schedules"] = [row for row in payload["schedules"] if row.get("id") != schedule_id]
            changed = len(payload["schedules"]) != before
            if changed:
                self._write_unlocked(payload)
            return changed

    def finish_schedule(self, schedule_id: str, *, ok: bool, error: str = "") -> None:
        """Mark a one-time schedule complete without discarding its delivery record."""
        with self.lock:
            payload = self._read_unlocked()
            for row in payload["schedules"]:
                if row.get("id") != schedule_id:
                    continue
                row["enabled"] = False
                row["completed_at"] = datetime.now().isoformat(timespec="seconds")
                row["last_delivery_ok"] = ok
                row["last_delivery_error"] = error
                break
            self._write_unlocked(payload)

    def session(self, key: str) -> dict[str, Any]:
        with self.lock:
            return dict(self._read_unlocked()["sessions"].get(key) or {})

    def session_for(self, keys: tuple[str, ...]) -> dict[str, Any]:
        """Load a session by any alias and bind it to every supplied key."""

        aliases = tuple(dict.fromkeys(key for key in keys if key))
        if not aliases:
            return {}
        with self.lock:
            payload = self._read_unlocked()
            sessions = payload["sessions"]
            session = next((dict(sessions[key]) for key in aliases if sessions.get(key)), {})
            if session and any(sessions.get(key) != session for key in aliases):
                for key in aliases:
                    sessions[key] = dict(session)
                self._write_unlocked(payload)
            return session

    def save_session(self, key: str, values: dict[str, Any]) -> None:
        with self.lock:
            payload = self._read_unlocked()
            payload["sessions"][key] = {**values, "updated_at": datetime.now().isoformat(timespec="seconds")}
            self._write_unlocked(payload)

    def save_session_for(self, keys: tuple[str, ...], values: dict[str, Any]) -> None:
        aliases = tuple(dict.fromkeys(key for key in keys if key))
        if not aliases:
            return
        with self.lock:
            payload = self._read_unlocked()
            session = {**values, "updated_at": datetime.now().isoformat(timespec="seconds")}
            for key in aliases:
                payload["sessions"][key] = dict(session)
            self._write_unlocked(payload)

    def clear_session(self, key: str) -> None:
        with self.lock:
            payload = self._read_unlocked()
            payload["sessions"].pop(key, None)
            self._write_unlocked(payload)

    def clear_sessions(self, keys: tuple[str, ...]) -> None:
        aliases = tuple(dict.fromkeys(key for key in keys if key))
        with self.lock:
            payload = self._read_unlocked()
            for key in aliases:
                payload["sessions"].pop(key, None)
            self._write_unlocked(payload)

    def mark_message(self, message_id: str) -> bool:
        if not message_id:
            return True
        with self.lock:
            payload = self._read_unlocked()
            seen = payload["processed_messages"]
            if message_id in seen:
                return False
            payload["processed_messages"] = [*seen[-499:], message_id]
            self._write_unlocked(payload)
            return True

    def record_delivery(self, item: dict[str, Any]) -> None:
        with self.lock:
            payload = self._read_unlocked()
            payload["deliveries"] = [*payload["deliveries"][-199:], item]
            self._write_unlocked(payload)


def validate_schedule(values: dict[str, Any], schedule_id: str | None = None) -> dict[str, Any]:
    timezone = str(values.get("timezone") or DEFAULT_TIMEZONE).strip()
    try:
        timezone_info = ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("请选择有效的时区。") from exc
    if CronTrigger is None or DateTrigger is None:
        raise RuntimeError("缺少 APScheduler，无法创建定时任务。")

    schedule_type = str(values.get("schedule_type") or "").strip().lower()
    if schedule_type not in {"recurring", "once"}:
        schedule_type = "recurring"

    cron = ""
    run_at = ""
    repeat_type = str(values.get("repeat_type") or "custom").strip().lower()
    send_time = str(values.get("send_time") or "09:00").strip()
    weekday = int(values.get("weekday", 0) or 0)
    monthday = int(values.get("monthday", 1) or 1)
    delay_value = int(values.get("delay_value", 5) or 5)
    delay_unit = str(values.get("delay_unit") or "minutes").strip().lower()

    if schedule_type == "once":
        if delay_unit not in {"minutes", "hours"}:
            raise ValueError("倒计时单位必须是分钟或小时。")
        maximum_delay = 10080 if delay_unit == "minutes" else 168
        if delay_value < 1 or delay_value > maximum_delay:
            unit_label = "分钟" if delay_unit == "minutes" else "小时"
            raise ValueError(f"倒计时时长需要在 1 到 {maximum_delay} {unit_label}之间。")
        delta = timedelta(minutes=delay_value) if delay_unit == "minutes" else timedelta(hours=delay_value)
        requested_run_at = str(values.get("run_at") or "").strip()
        if requested_run_at:
            parsed_run_at = datetime.fromisoformat(requested_run_at)
            if parsed_run_at.tzinfo is None:
                parsed_run_at = parsed_run_at.replace(tzinfo=timezone_info)
            run_at_datetime = parsed_run_at.astimezone(timezone_info)
        else:
            run_at_datetime = datetime.now(timezone_info) + delta
        if run_at_datetime <= datetime.now(timezone_info):
            raise ValueError("一次性发送时间必须晚于当前时间。")
        run_at = run_at_datetime.isoformat(timespec="seconds")
        DateTrigger(run_date=run_at_datetime, timezone=timezone)
    else:
        cron = _recurring_cron(values, repeat_type, send_time, weekday, monthday)
        CronTrigger.from_crontab(cron, timezone=timezone)
    target_type = str(values.get("target_type") or "group").strip().lower()
    if target_type not in {"group", "user"}:
        raise ValueError("接收目标必须是 group 或 user。")
    target_id = str(values.get("target_id") or "").strip()
    if not target_id:
        raise ValueError("请填写飞书群 Chat ID 或用户 Open ID。")
    now = datetime.now().isoformat(timespec="seconds")
    return {
        "id": schedule_id or str(values.get("id") or uuid4().hex[:12]),
        "name": str(values.get("name") or "定时专利生成").strip(),
        "enabled": bool(values.get("enabled", True)),
        "schedule_type": schedule_type,
        "cron": cron,
        "run_at": run_at,
        "repeat_type": repeat_type,
        "send_time": send_time,
        "weekday": weekday,
        "monthday": monthday,
        "delay_value": delay_value,
        "delay_unit": delay_unit,
        "timezone": timezone,
        "target_type": target_type,
        "target_id": target_id,
        "reminder_text": str(values.get("reminder_text") or "定时专利生成任务已经启动，请留意候选 idea。").strip(),
        "knowledge_base_id": str(values.get("knowledge_base_id") or "all").strip(),
        "innovation_level": str(values.get("innovation_level") or "medium").strip(),
        "created_at": str(values.get("created_at") or now),
        "updated_at": now,
    }


def _recurring_cron(
    values: dict[str, Any],
    repeat_type: str,
    send_time: str,
    weekday: int,
    monthday: int,
) -> str:
    legacy_cron = str(values.get("cron") or "").strip()
    if repeat_type == "custom" and legacy_cron:
        return legacy_cron
    match = re.fullmatch(r"([01]\d|2[0-3]):([0-5]\d)", send_time)
    if not match:
        raise ValueError("请选择有效的发送时间。")
    hour, minute = (int(part) for part in match.groups())
    if repeat_type == "daily":
        return f"{minute} {hour} * * *"
    if repeat_type == "weekly":
        if weekday not in range(7):
            raise ValueError("请选择有效的星期。")
        return f"{minute} {hour} * * {weekday}"
    if repeat_type == "monthly":
        if monthday not in range(1, 32):
            raise ValueError("每月日期需要在 1 到 31 之间。")
        return f"{minute} {hour} {monthday} * *"
    raise ValueError("请选择每天、每周或每月。")


def schedule_trigger(row: dict[str, Any]) -> Any:
    """Build the persisted APScheduler trigger used by the live scheduler."""
    if str(row.get("schedule_type") or "recurring") == "once":
        run_at = datetime.fromisoformat(str(row["run_at"]))
        return DateTrigger(run_date=run_at, timezone=str(row.get("timezone") or DEFAULT_TIMEZONE))
    return CronTrigger.from_crontab(str(row["cron"]), timezone=str(row.get("timezone") or DEFAULT_TIMEZONE))


class FeishuOpenAPI:
    def __init__(self, config: FeishuConfig) -> None:
        self.config = config
        self._token = ""
        self._token_expires_at = 0.0

    def access_token(self) -> str:
        if self._token and time.time() < self._token_expires_at - 60:
            return self._token
        response = self._request(
            f"{self.config.api_base_url}/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": self.config.app_id, "app_secret": self.config.app_secret},
            timeout=(10, 30),
        )
        response.raise_for_status()
        data = response.json()
        if int(data.get("code") or 0) != 0:
            raise RuntimeError(f"飞书获取 tenant_access_token 失败：{data.get('msg') or data}")
        token = str(data.get("tenant_access_token") or "")
        if not token:
            raise RuntimeError(f"飞书未返回 tenant_access_token：{data}")
        self._token = token
        self._token_expires_at = time.time() + int(data.get("expire") or 7200)
        return token

    def send_text(self, target_type: str, target_id: str, title: str, text: str) -> None:
        receive_id_type = "chat_id" if target_type == "group" else "open_id"
        self._post(
            "/open-apis/im/v1/messages",
            params={"receive_id_type": receive_id_type},
            body={
                "receive_id": target_id,
                "msg_type": "text",
                "content": json.dumps({"text": _message_text(title, text)}, ensure_ascii=False),
            },
        )

    def reply_text(self, message_id: str, title: str, text: str) -> None:
        self._post(
            f"/open-apis/im/v1/messages/{message_id}/reply",
            body={
                "msg_type": "text",
                "content": json.dumps({"text": _message_text(title, text)}, ensure_ascii=False),
            },
        )

    def send_card(self, target_type: str, target_id: str, card: dict[str, Any]) -> None:
        receive_id_type = "chat_id" if target_type == "group" else "open_id"
        self._post(
            "/open-apis/im/v1/messages",
            params={"receive_id_type": receive_id_type},
            body={
                "receive_id": target_id,
                "msg_type": "interactive",
                "content": json.dumps(card, ensure_ascii=False),
            },
        )

    def reply_card(self, message_id: str, card: dict[str, Any]) -> None:
        self._post(
            f"/open-apis/im/v1/messages/{message_id}/reply",
            body={
                "msg_type": "interactive",
                "content": json.dumps(card, ensure_ascii=False),
            },
        )

    def upload_image(self, filename: str, payload: bytes) -> str:
        response = self._request(
            f"{self.config.api_base_url}/open-apis/im/v1/images",
            headers={"Authorization": f"Bearer {self.access_token()}"},
            data={"image_type": "message"},
            files={"image": (filename, payload, "image/png")},
            timeout=(10, 60),
        )
        try:
            data = response.json()
        except ValueError as exc:
            response.raise_for_status()
            raise RuntimeError("飞书图片接口返回了无法解析的内容。") from exc
        if response.status_code >= 400 or int(data.get("code") or 0) != 0:
            raise RuntimeError(f"飞书图片上传失败（{data.get('code') or response.status_code}）：{data.get('msg') or response.reason}")
        image_key = str((data.get("data") or {}).get("image_key") or "")
        if not image_key:
            raise RuntimeError("飞书图片上传成功但没有返回 image_key。")
        return image_key

    def _post(self, path: str, body: dict[str, Any], params: dict[str, str] | None = None) -> dict[str, Any]:
        response = self._request(
            f"{self.config.api_base_url}{path}",
            params=params,
            headers={
                "Authorization": f"Bearer {self.access_token()}",
                "Content-Type": "application/json; charset=utf-8",
            },
            json=body,
            timeout=(10, 45),
        )
        try:
            data = response.json()
        except ValueError:
            response.raise_for_status()
            raise RuntimeError("飞书消息接口返回了无法解析的内容。")
        if response.status_code >= 400:
            code = data.get("code") if isinstance(data, dict) else None
            message = data.get("msg") if isinstance(data, dict) else None
            raise RuntimeError(f"飞书消息发送失败（{code or response.status_code}）：{message or response.reason}")
        if int(data.get("code") or 0) != 0:
            raise RuntimeError(f"飞书消息发送失败（{data.get('code')}）：{data.get('msg') or data}")
        return data

    def _request(self, url: str, **kwargs: Any) -> requests.Response:
        """POST with bounded retries for temporary network and server failures."""

        transient_errors = (
            requests.exceptions.SSLError,
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
        )
        last_error: BaseException | None = None
        for attempt in range(FEISHU_REQUEST_ATTEMPTS):
            try:
                response = requests.post(url, **kwargs)
            except transient_errors as exc:
                last_error = exc
                if attempt == FEISHU_REQUEST_ATTEMPTS - 1:
                    break
                self._retry_sleep(attempt)
                continue

            status = getattr(response, "status_code", 0)
            if isinstance(status, int) and status in FEISHU_TRANSIENT_STATUSES:
                last_error = RuntimeError(f"HTTP {status}")
                if attempt == FEISHU_REQUEST_ATTEMPTS - 1:
                    break
                self._retry_sleep(attempt, response)
                continue
            return response

        detail = f"{type(last_error).__name__}: {last_error}" if last_error else "未知网络错误"
        raise FeishuTransportError(
            f"飞书网络连接暂时中断，已自动重试 {FEISHU_REQUEST_ATTEMPTS} 次（{detail}）。"
        ) from last_error

    @staticmethod
    def _retry_sleep(attempt: int, response: requests.Response | None = None) -> None:
        retry_after = ""
        if response is not None:
            headers = getattr(response, "headers", {}) or {}
            retry_after = str(headers.get("Retry-After") or "").strip()
        try:
            delay = min(float(retry_after), 8.0) if retry_after else 0.5 * (2**attempt)
        except ValueError:
            delay = 0.5 * (2**attempt)
        time.sleep(delay)


class FeishuMessageSink:
    def __init__(
        self,
        incoming: FeishuIncoming | None = None,
        api: FeishuOpenAPI | None = None,
        target_type: str = "group",
        target_id: str = "",
    ) -> None:
        self.incoming = incoming
        self.api = api
        self.target_type = target_type
        self.target_id = target_id

    def markdown(self, title: str, text: str) -> None:
        if not self.api:
            raise RuntimeError("飞书消息缺少 API 发送通道。")
        cards = build_feishu_cards(title, text, self.api.upload_image)
        for card in cards:
            if self.incoming and self.incoming.message_id:
                self.api.reply_card(self.incoming.message_id, card)
            elif self.target_id:
                self.api.send_card(self.target_type, self.target_id, card)
            else:
                raise RuntimeError("飞书消息缺少回复消息 ID 或主动发送目标。")


def _message_text(title: str, text: str) -> str:
    return f"【{title}】\n\n{_plain_chat_text(text)}".strip()


def _plain_chat_text(text: str) -> str:
    """Remove Markdown control characters while preserving all readable content."""

    value = str(text or "")
    value = re.sub(r"^\s{0,3}#{1,6}\s+", "", value, flags=re.MULTILINE)
    value = re.sub(r"\[([^\]]+)]\((https?://[^)]+)\)", r"\1：\2", value)
    value = value.replace("```", "").replace("**", "").replace("__", "")
    value = re.sub(r"(?<!`)`([^`]+)`(?!`)", r"\1", value)
    return value


def incoming_from_event(data: Any) -> FeishuIncoming:
    """Convert the official SDK message event into the transport-neutral model."""

    event = getattr(data, "event", None)
    message = getattr(event, "message", None)
    sender = getattr(event, "sender", None)
    if message is None:
        raise ValueError("飞书消息事件缺少 message。")
    content = {}
    try:
        content = json.loads(getattr(message, "content", "") or "{}")
    except json.JSONDecodeError:
        pass
    text = str(content.get("text") or "")
    for mention in getattr(message, "mentions", None) or []:
        key = str(getattr(mention, "key", "") or "")
        if key:
            text = text.replace(key, "")
    sender_id = getattr(sender, "sender_id", None)
    return FeishuIncoming(
        message_id=str(getattr(message, "message_id", "") or ""),
        text=text.strip(),
        chat_id=str(getattr(message, "chat_id", "") or ""),
        chat_type=str(getattr(message, "chat_type", "") or ""),
        sender_open_id=str(getattr(sender_id, "open_id", "") or ""),
        sender_union_id=str(getattr(sender_id, "union_id", "") or ""),
        sender_user_id=str(getattr(sender_id, "user_id", "") or ""),
        thread_id=str(getattr(message, "thread_id", "") or ""),
    )


class FeishuIntegrationManager:
    def __init__(
        self,
        message_handler: Callable[[FeishuIncoming, FeishuMessageSink], None],
        scheduled_handler: Callable[[FeishuIncoming, FeishuMessageSink, dict[str, Any]], None] | None = None,
    ) -> None:
        self.message_handler = message_handler
        self.scheduled_handler = scheduled_handler
        self.store = FeishuStateStore()
        self.executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="feishu-agent")
        self.session_locks: dict[str, Lock] = {}
        self.session_locks_guard = Lock()
        self.scheduler: Any = None
        self.connection_thread: Thread | None = None
        self.connection_client: Any = None
        self.started = False
        self.last_error = ""
        self.config = FeishuConfig.load()

    def start(self) -> None:
        if self.started:
            return
        self.started = True
        self._start_scheduler()
        self._start_connection_if_ready()

    def refresh(self) -> None:
        old = self.config
        self.config = FeishuConfig.load()
        self.last_error = ""
        self.reload_schedules()
        if self.config.ready and (not self.connection_thread or not self.connection_thread.is_alive()):
            self._start_connection_if_ready()
        elif self.connection_thread and old != self.config:
            self.last_error = "飞书凭据已更新；当前长连接仍使用旧凭据，重启应用后完全生效。"

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.config.enabled,
            "configured": bool(self.config.app_id and self.config.app_secret),
            "stream_connected": bool(self.connection_thread and self.connection_thread.is_alive()),
            "scheduler_running": bool(self.scheduler and self.scheduler.running),
            "schedule_count": len(self.schedules()),
            "last_error": self.last_error,
            "dependencies": {
                "lark_oapi": lark is not None,
                "apscheduler": BackgroundScheduler is not None,
            },
        }

    def dispatch(self, incoming: FeishuIncoming) -> None:
        if not self.store.mark_message(incoming.message_id):
            return
        sink = FeishuMessageSink(incoming=incoming, api=FeishuOpenAPI(self.config))

        def task() -> None:
            with self._session_lock(incoming.session_key):
                try:
                    self.message_handler(incoming, sink)
                    self.last_error = ""
                except FeishuTransportError as exc:
                    LOGGER.warning("Feishu delivery failed after retries: %s", exc)
                    self.last_error = f"{type(exc).__name__}: {exc}"
                    try:
                        sink.markdown(
                            "发送暂时中断",
                            "飞书网络连接暂时中断，系统已经自动重试。专利流程进度已保留，"
                            "请回复 **重试**，系统会重新发送当前候选、章节或完成结果。",
                        )
                    except Exception:
                        LOGGER.exception("Failed to send Feishu transport error message")
                except Exception as exc:  # noqa: BLE001 - keep event connection alive
                    LOGGER.exception("Feishu agent interaction failed")
                    self.last_error = f"{type(exc).__name__}: {exc}"
                    try:
                        sink.markdown("处理失败", f"本次操作未完成：{self.last_error}\n\n请回复“状态”或稍后重试。")
                    except Exception:
                        LOGGER.exception("Failed to send Feishu error message")

        self.executor.submit(task)

    def schedules(self) -> list[dict[str, Any]]:
        rows = self.store.schedules()
        jobs = {job.id: job for job in self.scheduler.get_jobs()} if self.scheduler else {}
        for row in rows:
            job = jobs.get(f"feishu-{row['id']}")
            row["next_run_time"] = job.next_run_time.isoformat() if job and job.next_run_time else None
        return rows

    @register_tool("schedule_feishu_patent_generation", "创建或更新用户自定义的飞书定时专利生成任务。", "Feishu")
    def save_schedule(self, values: dict[str, Any], schedule_id: str | None = None) -> dict[str, Any]:
        row = self.store.save_schedule(values, schedule_id)
        self.reload_schedules()
        return row

    def delete_schedule(self, schedule_id: str) -> bool:
        changed = self.store.delete_schedule(schedule_id)
        self.reload_schedules()
        return changed

    @register_tool("send_feishu_patent_reminder", "向指定飞书群或用户主动发送专利生成提醒。", "Feishu")
    def send_test(self, target_type: str, target_id: str, text: str = "飞书机器人连接测试成功。") -> None:
        if not self.config.ready:
            raise ValueError("请先启用飞书并填写 App ID 和 App Secret。")
        FeishuMessageSink(
            api=FeishuOpenAPI(self.config),
            target_type=target_type,
            target_id=target_id,
        ).markdown("专利策源台", text)
        self.last_error = ""

    def reload_schedules(self) -> None:
        if not self.scheduler:
            return
        self.scheduler.remove_all_jobs()
        for row in self.store.schedules():
            if not row.get("enabled"):
                continue
            trigger = schedule_trigger(row)
            self.scheduler.add_job(
                self._run_schedule,
                trigger=trigger,
                args=[row["id"]],
                id=f"feishu-{row['id']}",
                replace_existing=True,
                coalesce=True,
                max_instances=1,
                misfire_grace_time=86400,
            )

    def _start_scheduler(self) -> None:
        if BackgroundScheduler is None:
            self.last_error = "缺少 APScheduler，定时提醒未启动。"
            return
        self.scheduler = BackgroundScheduler(timezone=DEFAULT_TIMEZONE, daemon=True)
        self.scheduler.start()
        self.reload_schedules()

    def _start_connection_if_ready(self) -> None:
        if not self.config.ready:
            return
        if lark is None:
            self.last_error = "缺少 lark-oapi，飞书机器人长连接未启动。"
            return

        def on_message(data: Any) -> None:
            try:
                self.dispatch(incoming_from_event(data))
            except Exception as exc:  # noqa: BLE001 - report malformed events
                LOGGER.exception("Failed to parse Feishu event")
                self.last_error = f"{type(exc).__name__}: {exc}"

        event_handler = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(on_message)
            .build()
        )
        client = lark.ws.Client(
            self.config.app_id,
            self.config.app_secret,
            event_handler=event_handler,
            log_level=lark.LogLevel.INFO,
            domain=self.config.api_base_url,
        )
        self.connection_client = client

        def run() -> None:
            try:
                client.start()
            except Exception as exc:  # noqa: BLE001 - surface connection error in settings
                LOGGER.exception("Feishu long connection stopped")
                self.last_error = f"{type(exc).__name__}: {exc}"

        self.connection_thread = Thread(target=run, name="feishu-long-connection", daemon=True)
        self.connection_thread.start()

    def _run_schedule(self, schedule_id: str) -> None:
        row = next((item for item in self.store.schedules() if item.get("id") == schedule_id), None)
        if not row:
            return
        self.executor.submit(self._execute_schedule, row)

    def _execute_schedule(self, row: dict[str, Any]) -> None:
        schedule_id = str(row.get("id") or "")
        delivery = {
            "schedule_id": schedule_id,
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "target_type": row["target_type"],
            "target_id": row["target_id"],
        }
        try:
            target_type = str(row["target_type"])
            target_id = str(row["target_id"])
            incoming = FeishuIncoming(
                message_id="",
                text="开始生成",
                chat_id=target_id if target_type == "group" else "",
                chat_type="group" if target_type == "group" else "p2p",
                sender_open_id=target_id if target_type == "user" else "",
            )
            sink = FeishuMessageSink(
                api=FeishuOpenAPI(self.config),
                target_type=target_type,
                target_id=target_id,
            )
            with self._session_lock(incoming.session_key):
                if self.scheduled_handler:
                    self.scheduled_handler(incoming, sink, row)
                else:
                    sink.markdown("专利生成提醒", row["reminder_text"])
            delivery["ok"] = True
            self.last_error = ""
        except Exception as exc:  # noqa: BLE001 - scheduler must keep running
            delivery["ok"] = False
            delivery["error"] = f"{type(exc).__name__}: {exc}"
            self.last_error = delivery["error"]
        self.store.record_delivery(delivery)
        if str(row.get("schedule_type") or "recurring") == "once":
            self.store.finish_schedule(
                schedule_id,
                ok=bool(delivery.get("ok")),
                error=str(delivery.get("error") or ""),
            )

    def _session_lock(self, key: str) -> Lock:
        with self.session_locks_guard:
            return self.session_locks.setdefault(key, Lock())

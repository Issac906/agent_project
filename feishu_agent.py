"""Conversation controller that exposes the patent workflow through Feishu."""

from __future__ import annotations

from typing import Any

from feishu_integration import FeishuIncoming, FeishuMessageSink, FeishuStateStore
from tool_registry import register_tool


HELP_TEXT = """## 可用指令

- **开始生成**：选择知识库和创新档位，启动一次专利生成。
- **状态**：查看当前会话进行到哪一步。
- **重试**：网络中断后重新发送当前候选、章节或完成结果。
- **查看会话ID**：显示配置定时提醒所需的群或用户标识。
- **1-5 / 选择 2**：选择候选专利方向。
- **接受**：接受当前章节并自动生成下一章。
- **重写**：重新生成当前章节。
- **修改：你的意见**：按意见重写当前章节。
- **结束并保存**：用已确认章节生成文件并写入历史记录。
- **取消**：清除当前飞书会话状态。

每一章都会完整发送；内容较长时会分成连续多条消息，不会省略。"""


class FeishuPatentAgent:
    def __init__(self, store: FeishuStateStore) -> None:
        self.store = store

    @register_tool("route_feishu_patent_interaction", "将飞书消息路由到现有专利 Agent 的候选选择和分章节确认流程。", "Feishu")
    def handle(self, incoming: FeishuIncoming, sink: FeishuMessageSink) -> None:
        text = _clean_text(incoming.text)
        session = self._session(incoming)

        if text in {"帮助", "help", "?", "？"}:
            sink.markdown("使用帮助", HELP_TEXT)
            return
        if text == "查看会话id" or text == "查看会话ID":
            sink.markdown(
                "当前飞书会话",
                f"- 会话类型：`{'群聊' if incoming.chat_type == 'group' else '单聊'}`\n"
                f"- Chat ID：`{incoming.chat_id or '未返回'}`\n"
                f"- 用户 Open ID：`{incoming.sender_open_id or '未返回'}`\n\n"
                "群定时提醒填写 Chat ID；单人提醒填写用户 Open ID。",
            )
            return
        if text in {"取消", "重新开始"}:
            self.store.clear_sessions(incoming.session_keys)
            sink.markdown("会话已清除", "当前飞书交互已清除。回复 **开始生成** 可重新开始。")
            return
        if text == "状态":
            self._send_status(session, sink)
            return
        if text in {"重试", "重新发送", "继续发送"}:
            self._resume(incoming, session, sink)
            return
        if text in {"开始", "开始生成", "生成专利", "开始生成专利"}:
            self._begin(incoming, sink)
            return

        stage = session.get("stage")
        if stage == "choose_kb":
            self._choose_knowledge_base(incoming, session, text, sink)
            return
        if stage == "choose_innovation":
            self._choose_innovation(incoming, session, text, sink)
            return
        if stage == "choose_candidate":
            self._choose_candidate(incoming, session, text, sink)
            return
        if stage == "confirm_section":
            self._section_action(incoming, session, text, sink)
            return

        sink.markdown("专利策源台", "当前没有正在等待处理的步骤。回复 **开始生成** 启动流程，或回复 **帮助** 查看指令。")

    def start_scheduled(
        self,
        incoming: FeishuIncoming,
        sink: FeishuMessageSink,
        schedule: dict[str, Any],
    ) -> None:
        """Start a configured patent run without waiting for manual setup choices."""

        current = self._session(incoming)
        active_stages = {"choose_kb", "choose_innovation", "running", "choose_candidate", "confirm_section"}
        if current.get("stage") in active_stages:
            if self._session_is_live(current):
                sink.markdown(
                    "定时生成已触发",
                    "当前会话已有一项专利任务正在进行，本次不会重复创建。请回复 **状态** 查看进度，或回复 **取消** 后重新开始。",
                )
                return
            self.store.clear_sessions(incoming.session_keys)

        knowledge_base_id = str(schedule.get("knowledge_base_id") or "all")
        innovation_level = str(schedule.get("innovation_level") or "medium")
        if innovation_level not in {"low", "medium", "high"}:
            innovation_level = "medium"

        knowledge = self._api("GET", "/api/knowledge")
        bases = knowledge.get("knowledge_bases") or []
        selected = next((item for item in bases if str(item.get("id")) == knowledge_base_id and item.get("selectable", True)), None)
        if knowledge_base_id == "all":
            selected = {"id": "all", "name": "全部知识库"}
        elif selected is None:
            selected = {"id": "all", "name": "全部知识库"}
            knowledge_base_id = "all"

        result = self._api(
            "POST",
            "/api/runs",
            {
                "knowledge_base_id": knowledge_base_id,
                "innovation_level": innovation_level,
                "channel": "feishu",
            },
        )
        session = {
            "stage": "running",
            "run_id": result["run_id"],
            "knowledge_base": selected,
            "innovation_level": innovation_level,
            "trigger": "schedule",
            "schedule_id": schedule.get("id"),
        }
        self._save_session(incoming, session)
        level_name = {"low": "低", "medium": "中", "high": "高"}[innovation_level]
        reminder = str(schedule.get("reminder_text") or "定时专利生成任务已经启动。").strip()
        sink.markdown(
            "定时生成已启动",
            f"{reminder}\n\n知识库：**{selected.get('name') or selected.get('id')}**\n创新档位：**{level_name}**\n\n"
            "系统会自动推进到候选 idea，届时请直接在飞书中选择。",
        )
        self._advance_until_input(incoming, session, sink)

    def _begin(self, incoming: FeishuIncoming, sink: FeishuMessageSink) -> None:
        data = self._api("GET", "/api/knowledge")
        bases = [
            {
                "id": "all",
                "name": "全部知识库",
                "document_count": len(data.get("documents") or []),
            },
            *(item for item in (data.get("knowledge_bases") or []) if item.get("selectable", True)),
        ]
        session = {"stage": "choose_kb", "knowledge_bases": bases}
        self._save_session(incoming, session)
        lines = ["请选择本次只读取的知识库："]
        for index, item in enumerate(bases, start=1):
            name = item.get("name") or item.get("title") or item.get("id")
            count = item.get("document_count", len(item.get("document_ids") or []))
            lines.append(f"{index}. **{name}**（{count} 篇素材）")
        lines.append("\n请回复序号，例如：`2`。")
        sink.markdown("选择知识库", "\n".join(lines))

    def _choose_knowledge_base(
        self,
        incoming: FeishuIncoming,
        session: dict[str, Any],
        text: str,
        sink: FeishuMessageSink,
    ) -> None:
        options = session.get("knowledge_bases") or []
        index = _parse_index(text, len(options))
        if index is None:
            sink.markdown("选择无效", f"请输入 1 到 {len(options)} 之间的知识库序号。")
            return
        selected = options[index]
        session.update({"stage": "choose_innovation", "knowledge_base": selected})
        self._save_session(incoming, session)
        sink.markdown(
            "选择创新档位",
            f"已选择 **{selected.get('name') or selected.get('id')}**。\n\n"
            "请选择创新档位：\n\n1. **低**：优先使用邻近节点\n2. **中**：在较大关联范围内组合\n3. **高**：允许在全图寻找组合\n\n请回复 `低`、`中` 或 `高`。",
        )

    def _choose_innovation(
        self,
        incoming: FeishuIncoming,
        session: dict[str, Any],
        text: str,
        sink: FeishuMessageSink,
    ) -> None:
        levels = {"1": "low", "低": "low", "2": "medium", "中": "medium", "3": "high", "高": "high"}
        level = levels.get(text.strip())
        if not level:
            sink.markdown("选择无效", "请回复 `低`、`中` 或 `高`。")
            return
        selected = session.get("knowledge_base") or {"id": "all", "name": "全部知识库"}
        result = self._api(
            "POST",
            "/api/runs",
            {
                "knowledge_base_id": selected.get("id") or "all",
                "innovation_level": level,
                "channel": "feishu",
            },
        )
        session.update({"stage": "running", "run_id": result["run_id"], "innovation_level": level})
        self._save_session(incoming, session)
        sink.markdown(
            "开始生成",
            f"已开始读取 **{selected.get('name') or selected.get('id')}**。系统将自动完成素材评估、外部补充检索、候选生成和相似专利差异分析；需要你选择时会停下来。",
        )
        self._advance_until_input(incoming, session, sink)

    def _advance_until_input(
        self,
        incoming: FeishuIncoming,
        session: dict[str, Any],
        sink: FeishuMessageSink,
    ) -> None:
        run_id = str(session.get("run_id") or "")
        if not run_id:
            raise RuntimeError("当前飞书会话缺少 run_id。")
        previous_phase = ""
        for _ in range(80):
            state = self._api("GET", f"/api/runs/{run_id}")
            if state.get("error"):
                session["stage"] = "error"
                self._save_session(incoming, session)
                sink.markdown("运行暂停", f"{state['error']}\n\n修复配置后回复 **状态** 查看，或回复 **重新开始**。")
                return
            if state.get("waiting_for") == "candidate":
                session["stage"] = "choose_candidate"
                self._save_session(incoming, session)
                self._send_candidates(state, sink)
                return
            if state.get("waiting_for") == "section":
                session["stage"] = "confirm_section"
                self._save_session(incoming, session)
                self._send_section(state, sink)
                return
            if state.get("done"):
                session["stage"] = "done"
                self._save_session(incoming, session)
                self._send_completion(state, sink)
                return
            phase = str(state.get("phase") or "")
            if phase != previous_phase:
                self._send_progress(state, sink)
                previous_phase = phase
            self._api("POST", f"/api/runs/{run_id}/advance", {})
        raise RuntimeError("自动推进超过安全步数，已停止以避免重复运行。")

    def _choose_candidate(
        self,
        incoming: FeishuIncoming,
        session: dict[str, Any],
        text: str,
        sink: FeishuMessageSink,
    ) -> None:
        state = self._api("GET", f"/api/runs/{session['run_id']}")
        candidates = state.get("candidates") or []
        index = _parse_index(text, len(candidates))
        if index is None:
            sink.markdown("选择无效", f"请回复 1 到 {len(candidates)} 之间的候选序号，例如 `选择 2`。")
            return
        self._api("POST", f"/api/runs/{session['run_id']}/select", {"index": index})
        selected = candidates[index]
        session["stage"] = "running"
        session["selected_candidate"] = selected
        self._save_session(incoming, session)
        sink.markdown("已选择专利方向", f"## {selected.get('title')}\n\n现在开始分章节写作。每章完整发送后由你确认。")
        self._advance_until_input(incoming, session, sink)

    def _section_action(
        self,
        incoming: FeishuIncoming,
        session: dict[str, Any],
        text: str,
        sink: FeishuMessageSink,
    ) -> None:
        action = ""
        instruction = ""
        if text in {"接受", "接受并继续", "继续"}:
            action = "accept"
        elif text in {"重写", "重新写"}:
            action = "rewrite"
        elif text.startswith(("修改：", "修改:", "意见：", "意见:")):
            action = "revise"
            instruction = text.split(":" if ":" in text else "：", 1)[1].strip()
        elif text in {"结束", "结束并保存", "保存"}:
            action = "quit"
        else:
            sink.markdown("等待章节确认", "请回复 **接受**、**重写**、**修改：具体意见** 或 **结束并保存**。")
            return
        state = self._api(
            "POST",
            f"/api/runs/{session['run_id']}/section",
            {"action": action, "instruction": instruction},
        )
        if action in {"rewrite", "revise"}:
            self._send_section(state, sink, rewritten=True)
            return
        if state.get("done"):
            session["stage"] = "done"
            self._save_session(incoming, session)
            self._send_completion(state, sink)
            return
        session["stage"] = "running"
        self._save_session(incoming, session)
        self._advance_until_input(incoming, session, sink)

    def _session(self, incoming: FeishuIncoming) -> dict[str, Any]:
        return self.store.session_for(incoming.session_keys)

    def _save_session(self, incoming: FeishuIncoming, session: dict[str, Any]) -> None:
        self.store.save_session_for(incoming.session_keys, session)

    def _session_is_live(self, session: dict[str, Any]) -> bool:
        if session.get("stage") in {"choose_kb", "choose_innovation"}:
            return True
        run_id = str(session.get("run_id") or "")
        if not run_id:
            return False
        try:
            state = self._api("GET", f"/api/runs/{run_id}")
        except Exception:
            return False
        return not bool(state.get("done") or state.get("error"))

    def _send_candidates(self, state: dict[str, Any], sink: FeishuMessageSink) -> None:
        candidates = state.get("candidates") or []
        sink.markdown("候选专利方向", f"已生成 {len(candidates)} 个候选方向。下面逐个展示完整内容，阅读后回复序号进行选择。")
        for index, item in enumerate(candidates, start=1):
            body = [f"## {index}. {item.get('title')}"]
            summary = str(item.get("summary") or "").strip()
            raw = str(item.get("raw") or "").strip()
            if summary:
                body.extend(["", summary])
            if raw and raw != summary and raw != item.get("title"):
                body.extend(["", "### 详细方案", "", raw])
            sink.markdown(f"候选 {index}", "\n".join(body))
        sink.markdown("请选择方向", f"请回复 `1` 到 `{len(candidates)}`，例如：`选择 2`。")

    def _send_section(self, state: dict[str, Any], sink: FeishuMessageSink, rewritten: bool = False) -> None:
        section = state.get("section") or {}
        name = section.get("name") or "当前章节"
        content = str(section.get("content") or "")
        quality = section.get("quality") or {}
        heading = "章节已重写" if rewritten else f"第 {int(section.get('index') or 0) + 1}/{section.get('total') or '-'} 章"
        sink.markdown(f"{heading}：{name}", f"## {name}\n\n{content}")
        score = quality.get("score")
        status = "通过" if quality.get("passed") else "需要关注"
        sink.markdown(
            "等待确认",
            f"本章质量检查：**{score if score is not None else '-'} / 100，{status}**。\n\n"
            "请回复 **接受**、**重写**、**修改：具体意见** 或 **结束并保存**。",
        )

    def _send_completion(self, state: dict[str, Any], sink: FeishuMessageSink) -> None:
        artifacts = state.get("artifacts") or {}
        history = state.get("history_record") or {}
        public_base = _public_base_url()
        lines = ["专利文档已生成，并保存到应用历史记录。"]
        for label, key in (("Markdown", "draft"), ("Word", "docx"), ("相似专利分析", "similarity_xlsx")):
            path = artifacts.get(key)
            if path and public_base:
                lines.append(f"- [{label}]({public_base}{path})")
        detail_url = history.get("detail_url")
        if detail_url and public_base:
            lines.append(f"- [完整交互历史]({public_base}{detail_url})")
        if not public_base:
            lines.append("\n当前未配置可从飞书访问的应用公网/内网地址，请在应用的历史记录中下载 Markdown 和 Word 文件。")
        sink.markdown("生成完成", "\n".join(lines))

    def _send_progress(self, state: dict[str, Any], sink: FeishuMessageSink) -> None:
        phase = str(state.get("phase") or "")
        labels = {
            "documents_loaded": "已读取所选知识图谱，正在评估素材。",
            "initial_assessed": "素材初评完成，正在进行外部检索补充和现有技术避让。",
            "searched": "正在复评素材；未达标时会自动继续检索。",
            "reassessed": "素材已达到生成门槛，正在组织图谱证据并生成候选方向。",
            "candidates_ready": "候选方向已生成，正在制作相似专利差异分析。",
            "selected": "正在撰写下一章。",
        }
        if phase in labels:
            sink.markdown("处理进度", labels[phase])

    def _send_status(self, session: dict[str, Any], sink: FeishuMessageSink) -> None:
        if not session:
            sink.markdown("当前状态", "没有活动会话。回复 **开始生成** 即可开始。")
            return
        run_id = session.get("run_id")
        if not run_id:
            sink.markdown("当前状态", f"当前等待：`{session.get('stage', '未知')}`。")
            return
        try:
            state = self._api("GET", f"/api/runs/{run_id}")
        except Exception:
            sink.markdown("当前状态", "应用重启后，旧的活动运行已失效。历史记录仍保留；请回复 **重新开始** 创建新运行。")
            return
        sink.markdown(
            "当前状态",
            f"- Run ID：`{run_id}`\n- 阶段：`{state.get('phase')}`\n- 等待：`{state.get('waiting_for') or '自动执行'}`\n- 已选方向：{(state.get('selected_candidate') or {}).get('title') or '尚未选择'}",
        )

    def _resume(
        self,
        incoming: FeishuIncoming,
        session: dict[str, Any],
        sink: FeishuMessageSink,
    ) -> None:
        """Recover the visible conversation from the persisted workflow state."""

        if not session:
            sink.markdown("没有可恢复任务", "当前没有活动会话。回复 **开始生成** 即可开始。")
            return
        run_id = str(session.get("run_id") or "")
        if not run_id:
            self._send_status(session, sink)
            return
        state = self._api("GET", f"/api/runs/{run_id}")
        if state.get("error"):
            session["stage"] = "error"
            self._save_session(incoming, session)
            sink.markdown("运行暂停", str(state["error"]))
            return
        if state.get("waiting_for") == "candidate":
            session["stage"] = "choose_candidate"
            self._save_session(incoming, session)
            self._send_candidates(state, sink)
            return
        if state.get("waiting_for") == "section":
            session["stage"] = "confirm_section"
            self._save_session(incoming, session)
            self._send_section(state, sink)
            return
        if state.get("done"):
            session["stage"] = "done"
            self._save_session(incoming, session)
            self._send_completion(state, sink)
            return
        session["stage"] = "running"
        self._save_session(incoming, session)
        sink.markdown("恢复处理", "已找到此前保留的专利流程，正在从当前步骤继续。")
        self._advance_until_input(incoming, session, sink)

    @staticmethod
    def _api(method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        from app import app

        with app.test_client() as client:
            response = client.open(path, method=method, json=payload)
            data = response.get_json(silent=True) or {}
        if response.status_code >= 400:
            raise RuntimeError(data.get("error") or f"Patent Agent API {response.status_code}")
        return data


def _clean_text(text: str) -> str:
    value = str(text or "").strip()
    if value.startswith("@") and " " in value:
        value = value.split(" ", 1)[1].strip()
    return value


def _parse_index(text: str, count: int) -> int | None:
    digits = "".join(char for char in text if char.isdigit())
    if not digits:
        return None
    value = int(digits)
    return value - 1 if 1 <= value <= count else None


def _public_base_url() -> str:
    from feishu_integration import FeishuConfig

    return FeishuConfig.load().public_base_url

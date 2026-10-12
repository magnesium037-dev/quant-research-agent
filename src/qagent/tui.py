"""Textual owns terminal editing/rendering; the existing bounded agent owns research."""
import json
import threading
import time
from contextlib import closing
from queue import Empty, SimpleQueue

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Collapsible, Footer, Header, Label, ListItem, ListView, Markdown, OptionList, Static, TextArea
from textual.widgets.option_list import Option

from .agent import clean, model_config, run_research
from .feishu import FeishuBridge, split_message
from .store import Store


class Composer(TextArea):
    BINDINGS = [Binding("enter", "submit", "发送", show=False, priority=True),
                Binding("shift+enter", "newline", "换行", show=False, priority=True),
                Binding("ctrl+j", "newline", "换行", show=False, priority=True)]

    class Submit(Message):
        pass

    def action_submit(self):
        self.post_message(self.Submit())

    def action_newline(self):
        self.insert("\n")


class DetailScreen(ModalScreen):
    BINDINGS = [("escape", "dismiss", "关闭")]
    DEFAULT_CSS = """
    DetailScreen { align: center middle; }
    #detail { width: 90%; height: 85%; border: round $accent; background: $surface; padding: 1; }
    #detail TextArea { height: 1fr; }
    """

    def __init__(self, title, value):
        super().__init__()
        self.heading, self.value = title, value

    def compose(self):
        with Vertical(id="detail"):
            yield Label(self.heading, markup=False)
            yield TextArea(json.dumps(clean(self.value), ensure_ascii=False, indent=2), read_only=True)
            yield Button("关闭 · Esc", id="close")

    @on(Button.Pressed, "#close")
    def close_detail(self):
        self.dismiss()


class SessionsScreen(DetailScreen):
    def compose(self):
        with Vertical(id="detail"):
            yield Label(self.heading, markup=False)
            yield OptionList(*(Option(row["id"][:8] + " · " + row["question"].replace("\n", " ")[:60], id=row["id"])
                               for row in self.value), markup=False)
            yield Button("关闭 · Esc", id="close")

    @on(OptionList.OptionSelected)
    def chosen(self, event):
        self.dismiss(event.option.id)


class PairingScreen(ModalScreen):
    BINDINGS = [("escape", "dismiss", "关闭")]
    DEFAULT_CSS = """
    PairingScreen { align: center middle; }
    #pairing { width: 78; height: auto; border: round $accent; background: $surface; padding: 1 2; }
    #pairing Button { margin-top: 1; margin-right: 1; }
    """

    def __init__(self, request):
        super().__init__()
        self.request = request

    def compose(self):
        kind = "群聊" if self.request["conversation_type"] in ("group", "topic") else "私聊"
        name = self.request["subject_name"] or self.request["subject_id"]
        with Vertical(id="pairing"):
            yield Label("飞书绑定请求", markup=False)
            yield Label(f"类型：{kind}\n身份：{name}\n配对码：{self.request['code']}\n有效期：1 小时", markup=False)
            yield Label("批准后，该会话才可以调用 qagent；群聊仍要求 @机器人。", markup=False)
            with Horizontal():
                yield Button("绑定", variant="primary", id="approve-pairing")
                yield Button("拒绝", id="reject-pairing")
                yield Button("稍后处理", id="close-pairing")

    @on(Button.Pressed)
    def choose(self, event):
        if event.button.id == "close-pairing":
            self.dismiss(None)
        else:
            self.dismiss(("approve" if event.button.id == "approve-pairing" else "reject", self.request))


class BindingsScreen(ModalScreen):
    BINDINGS = [("escape", "dismiss", "关闭")]
    DEFAULT_CSS = """
    BindingsScreen { align: center middle; }
    #bindings { width: 78; height: 70%; border: round $accent; background: $surface; padding: 1; }
    #bindings OptionList { height: 1fr; }
    """

    def __init__(self, bindings):
        super().__init__()
        self.bindings = bindings

    def compose(self):
        with Vertical(id="bindings"):
            yield Label("选择要解绑的飞书会话", markup=False)
            yield OptionList(*(Option((row["subject_name"] or row["conversation_id"]) + " · " + row["conversation_id"],
                                      id=row["conversation_id"]) for row in self.bindings), markup=False)
            yield Button("关闭 · Esc", id="close")

    @on(OptionList.OptionSelected)
    def chosen(self, event):
        self.dismiss(event.option.id)

    @on(Button.Pressed, "#close")
    def close_bindings(self):
        self.dismiss(None)


def conversation(store, run_id):
    """Follow saved parent IDs without copying whole transcripts into every run."""
    turns, seen = [], set()
    while run_id and run_id not in seen and len(turns) < 20:
        seen.add(run_id)
        run = store.get_run(run_id)
        turns.append(run)
        run_id = (run.get("result") or {}).get("parent_run_id")
    return list(reversed(turns))


class ResearchApp(App):
    TITLE = "qagent · 投研工作台"
    BINDINGS = [Binding("ctrl+n", "new", "新会话", priority=True),
                Binding("ctrl+r", "sessions", "历史", priority=True),
                Binding("ctrl+x", "context", "上下文", priority=True),
                Binding("ctrl+t", "thinking", "推理", priority=True),
                Binding("escape", "cancel", "停止", priority=True),
                Binding("ctrl+c", "interrupt", "停止 / 复制", priority=True),
                Binding("ctrl+q", "quit", "退出", priority=True),
                Binding("ctrl+s", "send", "发送", priority=True),
                Binding("ctrl+up", "recall", "上次输入", priority=True)]
    CSS = """
    Screen { background: $background; }
    #layout { height: 1fr; }
    #sidebar { width: 28; border-right: solid $primary-background; padding: 1; }
    #sidebar Button { width: 100%; margin-bottom: 1; }
    #sessions { height: 1fr; background: $surface; }
    #sessions Label { padding: 1; width: 100%; }
    #main { width: 1fr; }
    #transcript { height: 1fr; padding: 1 2; }
    #context-line { height: auto; max-height: 3; color: $text-muted; padding: 0 2; }
    #status { height: 2; padding: 0 2; color: $accent; }
    #composer { height: 6; margin: 0 1; border: round $accent; }
    #actions { height: 3; padding: 0 1; }
    #actions Button { margin-right: 1; min-width: 10; }
    #input-hint { width: 1fr; padding: 1 0; color: $text-muted; }
    .user { border-left: thick $accent; padding: 1 2; margin: 1 0; height: auto; }
    .turn-status { color: $text-muted; height: auto; margin-top: 1; }
    .answer { height: auto; margin-bottom: 1; }
    .thinking { color: $text-muted; height: auto; max-height: 14; overflow-y: auto; }
    .tool-body { height: auto; max-height: 18; overflow-y: auto; }
    Collapsible { margin: 0 0 1 0; padding: 0; }
    """

    def __init__(self, home, allowed_files=(), show_thinking=True, runner=run_research, feishu=None):
        super().__init__()
        self.home, self.allowed, self.runner = home, list(allowed_files), runner
        self.show_thinking = show_thinking
        self.busy = False
        self.active = None
        self.history = []
        self.context_data = {}
        self.events = SimpleQueue()
        self.cancel_event = threading.Event()
        self.cards = {}
        self.started = 0.0
        self.phase = "就绪"
        self.exit_when_done = False
        self.last_prompt = ""
        self.context_label = "尚未发送 · 仅注入已批准记忆与当前会话"
        self.feishu = feishu
        self.feishu_config_error = None
        if self.feishu is None:
            try:
                self.feishu = FeishuBridge(self.events)
            except ValueError as exc:
                self.feishu_config_error = str(exc)
        self.feishu_pending = set()
        self.feishu_busy = set()

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="layout"):
            with Vertical(id="sidebar"):
                yield Button("＋ 新会话", id="new")
                yield Label("最近运行 · 选择继续", markup=False)
                yield ListView(id="sessions")
                yield Button("查看上下文", id="context")
            with Vertical(id="main"):
                with VerticalScroll(id="transcript"):
                    yield Markdown("## 今天想研究什么？\n\n可以直接聊天、查询公开资料，或运行固定 ETF 轮动实验。\n\n"
                                   "工具参数与结果可展开查看；报告只在校验后保存。")
                yield Static(self.context_label, id="context-line", markup=False)
                yield Static("就绪", id="status", markup=False)
                yield Composer(id="composer", soft_wrap=True, show_line_numbers=False)
                with Horizontal(id="actions"):
                    yield Button("发送", variant="primary", id="send")
                    yield Button("停止", id="stop", disabled=True)
                    yield Button("推理：开" if self.show_thinking else "推理：关", id="thinking")
                    yield Button("绑定飞书账号", id="feishu")
                    yield Button("解绑飞书", id="feishu-unbind")
                    yield Static("Enter 发送 · Shift+Enter / Ctrl+J 换行 · 粘贴多行", id="input-hint")
        yield Footer()

    async def on_mount(self):
        config = model_config()
        source = "Reasonix" if ".reasonix" in config["source"] else config["source"]
        self.sub_title = f"{config['model']} · 配置：{source}"
        self.query_one(Composer).focus()
        await self.refresh_sessions()
        self.set_interval(0.05, self.drain_events)
        if self.feishu_config_error:
            self.notify(self.feishu_config_error, severity="error")
        elif self.feishu and self.feishu.enabled:
            self.feishu.start()

    async def on_unmount(self):
        if self.feishu:
            self.feishu.stop()

    def on_resize(self, event):
        self.query_one("#sidebar").display = event.size.width >= 100

    async def refresh_sessions(self):
        with closing(Store(self.home)) as store:
            runs = store.list_runs()[:30]
        view = self.query_one("#sessions", ListView)
        await view.clear()
        for run in runs:
            row = ListItem(Label(run["question"].replace("\n", " ")[:28] + "\n" + run["id"][:8] + " · " + run["status"], markup=False))
            row.run_id = run["id"]
            await view.append(row)

    @on(ListView.Selected, "#sessions")
    async def select_session(self, event):
        await self.load_session(event.item.run_id)

    async def load_session(self, run_id):
        if not run_id:
            return
        if self.busy:
            self.notify("请先停止当前请求，再切换会话。")
            return
        with closing(Store(self.home)) as store:
            turns = conversation(store, run_id)
        self.history = [{"question": row["question"], "answer": (row.get("result") or {}).get("report", "")} for row in turns]
        self.active = turns[-1]["id"]
        self.context_data = (turns[-1].get("result") or {}).get("context", {})
        transcript = self.query_one("#transcript", VerticalScroll)
        await transcript.remove_children()
        for row in turns:
            await transcript.mount(Static(row["question"], classes="user", markup=False),
                                   Markdown((row.get("result") or {}).get("report", "未完成的运行")))
        self.query_one("#context-line", Static).update(f"已恢复 {self.active[:8]} · 最近 {len(turns)} 轮 · 下次请求重新加载已批准记忆")
        self.call_after_refresh(transcript.scroll_end, animate=False)
        self.query_one(Composer).focus()

    @on(Button.Pressed)
    async def button_action(self, event):
        actions = {"send": self.action_send, "new": self.action_new, "stop": self.action_cancel,
                   "thinking": self.action_thinking, "context": self.action_context,
                   "feishu": self.action_feishu, "feishu-unbind": self.action_feishu_unbind}
        action = actions.get(event.button.id)
        if action:
            result = action()
            if hasattr(result, "__await__"):
                await result

    @on(Composer.Submit)
    async def submitted(self):
        await self.action_send()

    async def action_send(self):
        editor = self.query_one(Composer)
        question = editor.text.strip()
        if self.busy or not question:
            return  # Keep the draft editable while the current request runs.
        if question.startswith("/"):
            if question == "/memory":
                editor.clear()
                with closing(Store(self.home)) as store:
                    pending = store.memory_list("pending")
                self.push_screen(DetailScreen("待确认记忆 · 使用 /approve ID 或 /reject ID", pending or "暂无待确认记忆"))
                return
            if question.startswith(("/approve ", "/reject ")):
                command, prefix = question.split(None, 1)
                with closing(Store(self.home)) as store:
                    matches = [item for item in store.memory_list("pending") if item["id"].startswith(prefix.strip())]
                    if len(matches) == 1:
                        result = (store.memory_approve if command == "/approve" else store.memory_reject)(matches[0]["id"])
                    else:
                        result = None
                if result:
                    editor.clear()
                    self.notify(("已批准：" if command == "/approve" else "已拒绝：") + result["text"][:80])
                else:
                    self.notify("待确认记忆 ID 不存在或不唯一；内容已保留。", severity="warning")
                return
            if question.startswith(("/resume ", "/open ")):
                with closing(Store(self.home)) as store:
                    matches = [row["id"] for row in store.list_runs() if row["id"].startswith(question.split(None, 1)[1].strip())]
                if len(matches) == 1:
                    editor.clear()
                    await self.load_session(matches[0])
                else:
                    self.notify("记录 ID 不存在或不唯一。", severity="warning")
                return
            if question in ("/new", "/context", "/sessions", "/thinking on", "/thinking off", "/exit", "/help"):
                editor.clear()
                if question == "/new":
                    await self.action_new()
                elif question == "/context":
                    self.action_context()
                elif question == "/sessions":
                    self.action_sessions()
                elif question.startswith("/thinking"):
                    self.show_thinking = question.endswith(" off")
                    self.action_thinking()
                elif question == "/exit":
                    self.action_quit()
                else:
                    self.notify("/memory 查看待确认记忆；/approve ID 批准；/reject ID 拒绝；Ctrl+X 查看上下文。", timeout=10)
            else:
                self.notify("未知命令。输入 /help 查看；内容已保留。", severity="warning")
            return
        if len(question) > 12000:
            self.notify("问题超过 12000 字符，内容已保留。", severity="error")
            return
        self.last_prompt = question
        editor.clear()
        self.busy, self.started, self.phase = True, time.monotonic(), "准备请求"
        self.cancel_event = threading.Event()
        self.cards = {}
        self.query_one("#send", Button).disabled = True
        self.query_one("#stop", Button).disabled = False
        transcript = self.query_one("#transcript", VerticalScroll)
        self.turn_label = Static("准备请求", classes="turn-status", markup=False)
        self.thinking_text = Static("等待接口返回推理…", classes="thinking", markup=False)
        self.thinking_box = Collapsible(self.thinking_text, title="模型推理 · 仅本次显示", collapsed=not self.show_thinking)
        self.answer = Markdown("", classes="answer")
        await transcript.mount(Static(clean(question), classes="user", markup=False), self.turn_label, self.thinking_box, self.answer)
        self.call_after_refresh(transcript.scroll_end, animate=False)
        self.run_turn(question, list(self.history), self.active)

    @work(thread=True, exit_on_error=False)
    def run_turn(self, question, history, parent):
        try:
            with closing(Store(self.home)) as store:
                result = self.runner(question, store, allowed_files=self.allowed, previous=history,
                                     stream=True, cancel_event=self.cancel_event, parent_run_id=parent,
                                     on_progress=lambda kind, value: self.events.put((kind, value)))
                from .cli import write_report
                write_report(self.home, result, display=False)
            self.events.put(("done", (question, result)))
        except Exception as exc:
            self.events.put(("error", type(exc).__name__))

    def action_feishu(self):
        if not self.feishu or not self.feishu.enabled:
            self.notify("请先设置 FEISHU_APP_ID 和 FEISHU_APP_SECRET。", severity="warning")
            return
        if self.feishu.ready.is_set():
            self.notify("飞书已连接；请在飞书私聊机器人发送一条消息完成绑定。", timeout=8)
        else:
            self.notify("飞书正在连接；请稍后在飞书私聊机器人发送一条消息。", timeout=8)

    def action_feishu_unbind(self):
        if not self.feishu or not self.feishu.enabled:
            self.notify("飞书未配置。", severity="warning")
            return
        with closing(Store(self.home)) as store:
            bindings = store.channel_binding_list("feishu", self.feishu.config.app_id)
        if not bindings:
            self.notify("暂无已绑定的飞书会话。")
            return
        self.push_screen(BindingsScreen(bindings), self.handle_unbind_decision)

    def handle_unbind_decision(self, conversation_id):
        if not conversation_id:
            return
        with closing(Store(self.home)) as store:
            removed = store.channel_unbind("feishu", self.feishu.config.app_id, conversation_id)
        self.notify("已解绑飞书会话。" if removed else "会话已不存在。", timeout=5)

    async def handle_feishu_message(self, message):
        conversation_id = message["conversation_id"]
        if not conversation_id or not message["text"]:
            return
        conversation_type = message["conversation_type"]
        is_group = conversation_type in ("group", "topic")
        if is_group and not message["mentioned_bot"]:
            return
        with closing(Store(self.home)) as store:
            binding = store.channel_binding("feishu", self.feishu.config.app_id, conversation_id)
            if not binding:
                from .feishu import pairing_code
                request, created = store.pairing_request(
                    "feishu", self.feishu.config.app_id, conversation_id, conversation_type,
                    message["subject_id"], message["subject_name"], pairing_code())
            else:
                request, created = None, False
        if not binding:
            if created and request["id"] not in self.feishu_pending:
                self.feishu_pending.add(request["id"])
                self.push_screen(PairingScreen(request), self.handle_pairing_decision)
            await self.send_feishu(conversation_id, "已收到绑定请求，请等待本机 qagent 确认后再发送问题。")
            return
        if message["text"].startswith(("/approve", "/reject", "/memory", "/context", "/exit")):
            await self.send_feishu(conversation_id, "管理命令仅可在本机 qagent 界面执行。")
            return
        if conversation_id in self.feishu_busy:
            await self.send_feishu(conversation_id, "上一条研究仍在处理中，请稍后再试。")
            return
        with closing(Store(self.home)) as store:
            session = store.channel_session("feishu", self.feishu.config.app_id, conversation_id)
            parent = session["last_run_id"] if session else None
            history = []
            if parent:
                history = [{"question": row["question"], "answer": (row.get("result") or {}).get("report", "")}
                           for row in conversation(store, parent)]
        self.feishu_busy.add(conversation_id)
        self.run_feishu_turn(message, history, parent)

    async def handle_pairing_decision(self, result):
        if not result:
            return
        decision, request = result
        self.feishu_pending.discard(request["id"])
        with closing(Store(self.home)) as store:
            try:
                decided = store.pairing_decide(request["id"], "approved" if decision == "approve" else "rejected")
            except ValueError as exc:
                self.notify(str(exc), severity="warning")
                return
        if decision == "approve":
            self.notify("飞书会话已绑定。", timeout=5)
            await self.send_feishu(request["conversation_id"], "绑定成功，现在可以发送研究问题了。")
        else:
            self.notify("飞书绑定请求已拒绝。", timeout=5)
            await self.send_feishu(request["conversation_id"], "本次绑定请求未获批准。")

    async def send_feishu(self, conversation_id, text, reply_to=None):
        if not self.feishu:
            return
        try:
            for part in split_message(text):
                await self.feishu.send(conversation_id, part, reply_to=reply_to)
                reply_to = None
        except Exception as exc:
            self.notify("飞书发送失败：" + type(exc).__name__, severity="warning")

    @work(thread=True, exit_on_error=False)
    def run_feishu_turn(self, message, history, parent):
        conversation_id = message["conversation_id"]
        try:
            with closing(Store(self.home)) as store:
                result = self.runner(message["text"], store, allowed_files=self.allowed, previous=history,
                                     stream=False, parent_run_id=parent)
                from .cli import write_report
                write_report(self.home, result, display=False)
                store.channel_session_set("feishu", self.feishu.config.app_id, conversation_id, result["run_id"])
            self.events.put(("feishu_done", {"conversation_id": conversation_id, "message_id": message["message_id"],
                                              "report": result["report"], "status": result["status"]}))
        except Exception as exc:
            self.events.put(("feishu_failed", {"conversation_id": conversation_id, "error": type(exc).__name__}))

    async def drain_events(self):
        # Coalesce snapshots to one paint per tick; Textual handles layout/Markdown.
        pending = None
        while True:
            try:
                kind, value = self.events.get_nowait()
            except Empty:
                break
            if kind == "stream":
                pending = value
                continue
            if pending is not None:
                self.paint_stream(pending)
                pending = None
            await self.handle_progress(kind, value)
        if pending is not None:
            self.paint_stream(pending)
        if self.busy:
            elapsed = time.monotonic() - self.started
            self.query_one("#status", Static).update(f"{self.phase} · {elapsed:.1f}s · Esc 停止 · 可继续编辑下一条")

    def paint_stream(self, value):
        transcript = self.query_one("#transcript", VerticalScroll)
        follow = transcript.is_vertical_scroll_end
        self.phase = "生成回答" if value["content"] else "模型思考"
        self.thinking_text.update(value["reasoning"] or "接口暂未返回推理内容")
        self.answer.update(value["content"])
        self.turn_label.update("生成中 · 草稿尚未通过来源校验")
        if follow:
            self.call_after_refresh(transcript.scroll_end, animate=False)

    async def handle_progress(self, kind, value):
        if kind == "context":
            self.context_data = value
            self.context_label = (f"记忆 {value['memory_characters']} 字符 · 历史 {value.get('previous_turns', 0)} 轮 / {value['previous_characters']} 字符"
                                  f" · 待审 {value.get('pending_memory_count', 0)} · 方法 {value.get('business_framework_characters', 0)} 字符"
                                  f" · 文件 {value['authorized_files']} · 工具 {len(value['tools'])}"
                                  + (" · 已截断（Ctrl+X 查看）" if value['memory_truncated'] or value['previous_truncated'] else ""))
            self.query_one("#context-line", Static).update(self.context_label)
        elif kind == "request":
            if value["round"] > 1:
                self.thinking_box.collapsed = True
                self.thinking_text = Static("等待接口返回推理…", classes="thinking", markup=False)
                self.thinking_box = Collapsible(self.thinking_text, title=f"模型推理 · 请求 {value['round']}", collapsed=not self.show_thinking)
                await self.query_one("#transcript").mount(self.thinking_box, before=self.answer)
            self.phase = f"请求 {value['round']} · 等待首个片段"
            self.turn_label.update(f"请求 {value['round']} · 消息 {value['characters']} 字符 · 工具结果 {value['tool_results']} 条")
        elif kind == "reasoning" and value == "本轮接口未返回推理内容。":
            self.thinking_text.update(value)
        elif kind == "tool_start":
            self.phase = "执行 " + value["name"]
            body = Static("参数\n" + value["arguments"], classes="tool-body", markup=False)
            box = Collapsible(body, title="运行中 · " + value["name"], collapsed=True)
            self.cards[value["id"]] = (box, body, value["arguments"])
            await self.query_one("#transcript").mount(box, before=self.answer)
        elif kind == "tool_end":
            box, body, arguments = self.cards[value["id"]]
            box.title = f"{'失败' if value['failed'] else '完成'} · {value['name']} · {value['seconds']}s"
            body.update("参数\n" + arguments + "\n\n结果\n" + value["result"])
        elif kind == "tokens":
            if value:
                self.query_one("#context-line", Static).update(self.context_label + f" · API tokens {value.get('total_tokens', '未提供')}")
        elif kind == "feishu_ready":
            self.notify("飞书长连接已建立。", timeout=5)
        elif kind == "feishu_error":
            self.notify("飞书连接异常：" + str(value), severity="warning")
        elif kind == "feishu_message":
            await self.handle_feishu_message(value)
        elif kind == "feishu_done":
            self.feishu_busy.discard(value["conversation_id"])
            await self.send_feishu(value["conversation_id"], value["report"], value["message_id"])
        elif kind == "feishu_failed":
            self.feishu_busy.discard(value["conversation_id"])
            await self.send_feishu(value["conversation_id"], "本次研究未完成，请稍后重试。")
        elif kind in ("done", "error"):
            self.busy = False
            self.query_one("#send", Button).disabled = False
            self.query_one("#stop", Button).disabled = True
            if kind == "done":
                question, result = value
                self.active = result["run_id"]
                self.history.append({"question": question, "answer": result["report"]})
                self.history = self.history[-20:]
                self.answer.update(result["report"])
                self.thinking_box.collapsed = True
                usage = result["usage"]
                self.turn_label.update(f"{result['status']} · {time.monotonic() - self.started:.1f}s · "
                                       f"模型 {usage['model_calls']} 次 / 工具 {usage['tool_calls']} 次 · 运行 {self.active[:8]}")
                for box, _, _ in self.cards.values():
                    if box.title.startswith("运行中"):
                        box.title = box.title.replace("运行中", "已停止", 1)
                self.query_one("#status", Static).update(f"已保存 · {self.active[:8]} · Ctrl+R 查看历史")
                self.query_one("#status", Static).tooltip = str(self.home / 'reports' / (self.active + '.md'))
                await self.refresh_sessions()
            else:
                self.answer.update("本轮未完成：" + value + "。上次输入可按 Ctrl+↑ 恢复。")
                self.query_one("#status", Static).update("请求失败，界面仍可继续使用")
            if self.exit_when_done:
                self.exit()

    async def action_new(self):
        if self.busy:
            self.notify("请先停止当前请求。")
            return
        self.active, self.history, self.context_data = None, [], {}
        await self.query_one("#transcript").remove_children()
        self.query_one("#context-line", Static).update("新会话 · 未注入此前对话")
        self.query_one(Composer).focus()

    def action_context(self):
        self.push_screen(DetailScreen("实际初始上下文 · 不含推理；后续工具结果在工具卡片中", self.context_data or "尚无请求"))

    def action_sessions(self):
        if self.busy:
            self.notify("请先停止当前请求。")
            return
        with closing(Store(self.home)) as store:
            runs = store.list_runs()[:100]
        self.push_screen(SessionsScreen("选择历史运行并继续", runs), self.load_session)

    def action_thinking(self):
        self.show_thinking = not self.show_thinking
        self.query_one("#thinking", Button).label = "推理：开" if self.show_thinking else "推理：关"
        if hasattr(self, "thinking_box") and self.thinking_box.is_mounted:
            self.thinking_box.collapsed = not self.show_thinking

    def action_cancel(self):
        if isinstance(self.screen, DetailScreen):
            self.screen.dismiss()
            return
        if self.busy:
            self.cancel_event.set()
            self.phase = "正在停止 · 等待当前网络读取或工具返回，随后保存"

    def action_interrupt(self):
        if isinstance(self.focused, TextArea) and self.focused.selected_text:
            self.focused.action_copy()
        elif self.busy:
            self.action_cancel()
        elif isinstance(self.focused, TextArea):
            self.focused.action_copy()

    def action_recall(self):
        self.query_one(Composer).load_text(self.last_prompt)

    def action_quit(self):
        if self.busy:
            self.exit_when_done = True
            self.action_cancel()
        else:
            self.exit()

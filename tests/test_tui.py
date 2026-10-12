import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from contextlib import closing
from types import SimpleNamespace as NS
from unittest.mock import patch

from qagent import agent
from qagent.store import Store
from qagent.tui import Composer, DetailScreen, PairingScreen, ResearchApp, conversation
from test_agent import FakeStore, FakeClient


def chunk(content=None, reasoning=None, tools=None, finish=None):
    return NS(choices=[NS(delta=NS(content=content, reasoning_content=reasoning, tool_calls=tools), finish_reason=finish)], usage=None)


class Stream:
    def __init__(self, chunks):
        self.chunks, self.closed = chunks, False

    def __iter__(self):
        return iter(self.chunks)

    def close(self):
        self.closed = True


class FakeFeishu:
    enabled = True

    def __init__(self):
        self.config = NS(app_id="test-app")
        self.ready = threading.Event()
        self.ready.set()
        self.sent = []

    def start(self):
        pass

    def stop(self):
        pass

    async def send(self, conversation_id, text, reply_to=None):
        self.sent.append((conversation_id, text, reply_to))


class StreamingTests(unittest.TestCase):
    def test_split_tool_arguments_reasoning_roundtrip_and_markdown(self):
        first = Stream([chunk(reasoning="先查工具。"),
                        chunk(tools=[NS(index=0, id="t1", function=NS(name="get_news", arguments='{"key'))]),
                        chunk(tools=[NS(index=0, id=None, function=NS(name=None, arguments='word":""}'))], finish="tool_calls")])
        report = "\n\n".join("## " + title + "\n" + ("新闻 [S1]" if title == "证据" else "待验证") for title in agent.SECTIONS)
        second = Stream([chunk(content=report[:30]), chunk(content=report[30:], finish="stop"),
                         NS(choices=[], usage=NS(model_dump=lambda **_: {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}))])
        client, store, events = FakeClient([first, second]), FakeStore(), []
        with patch.object(agent, "dispatch", return_value={"items": [{"title": "合成新闻"}], "source": "fixture"}):
            result = agent.run_research("测试", store, client, "fake", stream=True, on_progress=lambda *e: events.append(e))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["token_usage"][0]["total_tokens"], 30)
        self.assertTrue(first.closed and second.closed)
        self.assertEqual(client.requests[1]["messages"][2]["reasoning_content"], "先查工具。")
        self.assertEqual(json.loads(store.events[0][2]["arguments"]), {"keyword": ""})
        self.assertIn("tool_start", [e[0] for e in events])
        self.assertIn("tool_end", [e[0] for e in events])
        self.assertNotIn("先查工具。", json.dumps(store.finished, ensure_ascii=False))

    def test_stream_cancel_and_incomplete_output_do_not_publish(self):
        event = threading.Event()
        def cancelling():
            yield chunk(content="未核验草稿")
            event.set()
            yield chunk(content="后文", finish="stop")
        stream = Stream(cancelling())
        result = agent.run_research("测试", FakeStore(), FakeClient([stream]), "fake", stream=True, cancel_event=event)
        self.assertEqual(result["status"], "cancelled")
        self.assertTrue(stream.closed)
        self.assertNotIn("未核验草稿", result["report"])
        result = agent.run_research("测试", FakeStore(), FakeClient([Stream([chunk(content="截断内容", finish="length")])]), "fake", stream=True)
        self.assertEqual(result["status"], "partial")
        self.assertNotIn("截断内容", result["report"])

    def test_split_secret_never_appears_in_previews(self):
        with patch.dict(os.environ, {"LLM_API_KEY": "private-token"}):
            for size in range(1, len("private-token")):
                self.assertEqual(agent.stream_preview("hello " + "private-token"[:size]), "hello ")
            self.assertEqual(agent.stream_preview("hello private-token"), "hello [REDACTED]")


class TuiTests(unittest.IsolatedAsyncioTestCase):
    async def test_feishu_pairing_and_bound_turn(self):
        def runner(question, store, **kwargs):
            run_id = store.create_run(question, kind="feishu")
            result = {"run_id": run_id, "report": "飞书研究完成", "status": "completed",
                      "usage": {"model_calls": 1, "tool_calls": 0}, "parent_run_id": kwargs["parent_run_id"]}
            store.finish_run(run_id, result)
            return result

        with tempfile.TemporaryDirectory() as folder:
            bridge = FakeFeishu()
            app = ResearchApp(Path(folder), runner=runner, feishu=bridge)
            async with app.run_test(size=(110, 38)) as pilot:
                message = {"conversation_id": "chat-1", "conversation_type": "p2p", "subject_id": "user-1",
                           "subject_name": "Alice", "text": "你好", "message_id": "msg-1",
                           "mentioned_bot": False}
                await app.handle_feishu_message(message)
                self.assertIsInstance(app.screen, PairingScreen)
                await pilot.pause()
                await pilot.click("#approve-pairing")
                await pilot.pause()
                await app.handle_feishu_message(message)
                for _ in range(40):
                    await pilot.pause(0.05)
                    if "飞书研究完成" in [item[1] for item in bridge.sent]:
                        break
                self.assertTrue(any(item[1] == "飞书研究完成" for item in bridge.sent))
                with closing(Store(Path(folder))) as store:
                    self.assertIsNotNone(store.channel_binding("feishu", "test-app", "chat-1"))

    async def test_composer_stream_tools_context_resume_and_cancel(self):
        def runner(question, store, **kwargs):
            emit = kwargs["on_progress"]
            context = {"memory_characters": 2, "previous_characters": 0, "previous_turns": len(kwargs["previous"]),
                       "authorized_files": 0, "tools": ["get_news"], "memory_truncated": False, "previous_truncated": False}
            emit("context", context)
            emit("request", {"round": 1, "characters": 123, "tool_results": 0})
            emit("stream", {"content": "你好", "reasoning": "合成推理", "pending_tools": []})
            emit("tool_start", {"id": "test", "name": "get_news", "arguments": "{}"})
            if question == "停止测试":
                kwargs["cancel_event"].wait(3)
            else:
                time.sleep(0.08)
            emit("tool_end", {"id": "test", "name": "get_news", "result": '{"items":[]}', "failed": False, "seconds": 0.08})
            run_id = store.create_run(question)
            result = {"run_id": run_id, "report": "你好！可以查询资料。", "status": "cancelled" if kwargs["cancel_event"].is_set() else "completed",
                      "usage": {"model_calls": 1, "tool_calls": 1}, "context": context, "parent_run_id": kwargs["parent_run_id"]}
            store.finish_run(run_id, result, status=result["status"])
            return result

        with tempfile.TemporaryDirectory() as folder:
            app = ResearchApp(Path(folder), runner=runner)
            async with app.run_test(size=(110, 38)) as pilot:
                editor = app.query_one(Composer)
                editor.load_text("你好")
                await pilot.press("shift+enter")
                self.assertIn("\n", editor.text)
                await pilot.press("enter")
                for _ in range(60):
                    await pilot.pause(0.05)
                    if not app.busy:
                        break
                self.assertFalse(app.busy)
                self.assertTrue(app.active)
                first = app.active
                self.assertIn("完成", app.cards["test"][0].title)
                self.assertTrue(list(Path(folder, "reports").glob("*.md")))
                await pilot.press("ctrl+x")
                self.assertIsInstance(app.screen, DetailScreen)
                await pilot.press("escape")
                self.assertNotIsInstance(app.screen, DetailScreen)
                editor.load_text("停止测试")
                await pilot.press("enter")
                await pilot.pause(0.1)
                editor.load_text("下一条草稿")
                await app.action_send()
                self.assertEqual(editor.text, "下一条草稿")
                await pilot.press("escape")
                for _ in range(60):
                    await pilot.pause(0.05)
                    if not app.busy:
                        break
                self.assertFalse(app.busy)
                self.assertEqual(editor.text, "下一条草稿")
                with closing(Store(Path(folder))) as store:
                    self.assertEqual(store.get_run(app.active)["status"], "cancelled")
                    self.assertEqual([r["id"] for r in conversation(store, app.active)], [first, app.active])
                await pilot.resize_terminal(70, 25)
                await pilot.pause()
                self.assertFalse(app.query_one("#sidebar").display)
                await pilot.press("ctrl+r")
                await pilot.pause()
                await pilot.press("escape")
                await app.action_new()
                self.assertEqual(app.history, [])
                self.assertIsNone(app.active)

    async def test_memory_review_commands(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            with closing(Store(home)) as store:
                pending = store.memory_add("优先控制回撤", source="agent:test", pending=True, category="preference")
            app = ResearchApp(home)
            async with app.run_test(size=(90, 30)) as pilot:
                editor = app.query_one(Composer)
                editor.load_text("/memory")
                await pilot.press("enter")
                await pilot.pause()
                self.assertIsInstance(app.screen, DetailScreen)
                await pilot.press("escape")
                editor.load_text("/approve " + pending["id"][:8])
                await pilot.press("enter")
                await pilot.pause()
                with closing(Store(home)) as store:
                    self.assertEqual(store.memory_list()[0]["text"], "优先控制回撤")

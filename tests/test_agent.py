import json
import os
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

from qagent import agent


class FakeStore:
    def __init__(self):
        self.events = []
        self.finished = None
        self.memories = []

    def create_run(self, question, kind="research"):
        return "test-run"

    def memory_list(self, status="approved"):
        return [item for item in self.memories if item.get("status") == status]

    def memory_add(self, text, source="user", pending=False, category="general"):
        self.memories.append({"id": f"memory-{len(self.memories) + 1}", "text": text, "source": source,
                              "category": category, "pending": pending,
                              "status": "pending" if pending else "approved"})
        return self.memories[-1]

    def event(self, *args):
        self.events.append(args)

    def finish_run(self, run_id, result, status):
        self.finished = result


def message(calls=(), text=None):
    return NS(choices=[NS(message=NS(content=text, reasoning_content="reasoning roundtrip", tool_calls=[
        NS(id=f"call-{i}", function=NS(name=name, arguments=json.dumps(args))) for i, (name, args) in enumerate(calls)]))])


def final(evidence="未知"):
    return message(text=json.dumps({key: evidence if key == "证据" else "待验证" for key in agent.SECTIONS}))


class FakeClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []
        self.chat = NS(completions=NS(create=self.create))

    def create(self, **kwargs):
        self.requests.append(json.loads(json.dumps(kwargs)))
        response = next(self.responses)
        if isinstance(response, BaseException):
            raise response
        return response


class AgentTests(unittest.TestCase):
    def test_business_framework_is_versioned_context_and_read_only_tool(self):
        core = agent.framework_core()
        self.assertIn("七个节点", core)
        section = agent.framework_section("我为什么做它")
        self.assertIn("机会成本", section["content"])
        self.assertEqual(section["source"], "docs/OPPORTUNITY_FRAMEWORK.md")
        result, store, client = self.run_fake([message([("get_opportunity_framework", {"section": "需求"})]), final()])
        self.assertIn("商业机会方法核心", client.requests[0]["messages"][1]["content"])
        self.assertIn("从问题存在到可观测行动", store.events[0][2]["result"]["content"])
        self.assertFalse(any(tool["function"]["name"].startswith(("write", "edit")) for tool in agent.TOOLS))

    def test_quant_logic_is_python_only(self):
        client = self.run_fake([final()])[2]
        prompt = client.requests[0]["messages"][0]["content"]
        self.assertIn("LLM 与量化计算严格隔离", prompt)
        self.assertIn("所有行情、指标、竞价、波浪、信号和成交必须由 Python 工具确定性完成", prompt)
        backtest = next(tool for tool in agent.TOOLS if tool["function"]["name"] == "run_backtest")
        self.assertIn("不接受策略代码", backtest["function"]["description"])

    def test_router_keeps_vague_selection_out_of_news(self):
        route = agent.route_intent("今天具体什么底层可看呢")
        self.assertEqual(route["intent"], "momentum_selection")
        self.assertIn("screen_etfs", route["instruction"])
        self.assertIn("screen_etfs", [tool["function"]["name"] for tool in agent.TOOLS])
        self.assertEqual(agent.route_intent("最近有什么公告")["intent"], "information_research")

    def test_identical_failed_tool_call_is_not_retried(self):
        request = {"symbols": ["510300"]}
        with patch.object(agent, "dispatch", side_effect=ValueError("provider timeout")) as dispatch:
            result, store, _ = self.run_fake([message([("get_quotes", request)]),
                                               message([("get_quotes", request)]), final()])
        self.assertEqual(dispatch.call_count, 1)
        self.assertIn("retryable", store.events[0][2]["result"])
        self.assertIn("same arguments", store.events[1][2]["result"]["retry_policy"])
        self.assertEqual(result["status"], "partial")

    def test_plain_reply_context_and_reasoning_visibility(self):
        store = FakeStore()
        client = FakeClient([message(text=json.dumps({"reply": "你好！我能查询资料和运行固定 ETF 轮动回测。"}))])
        progress = []
        result = agent.run_research("你可以做什么", store, client, "fake",
                                    previous={"answer": "旧回答"}, on_progress=lambda *event: progress.append(event))
        self.assertEqual(result["status"], "completed")
        self.assertNotIn("## 证据", result["report"])
        self.assertIn("你好", result["report"])
        self.assertEqual(result["context"]["messages"], client.requests[0]["messages"])
        self.assertIn("旧回答", result["context"]["messages"][1]["content"])
        self.assertIn(("reasoning", "reasoning roundtrip"), progress)
        self.assertNotIn("reasoning roundtrip", json.dumps(store.finished))

    def test_plain_reply_cannot_bypass_evidence_validation(self):
        for sources, experiments, reply in [({}, [], "伪造 [S9]"), ({}, [], "https://fake.test"),
                                            ({"S1": {}}, [], "掩盖研究来源"), ({}, [{"return": 0.1}], "掩盖计算结果")]:
            failures = []
            report = agent.render_report("研究", json.dumps({"reply": reply}), sources, failures, experiments)
            self.assertTrue(failures)
            self.assertNotIn(reply, report)

    def test_context_truncation_and_reasoning_redaction(self):
        store = FakeStore()
        store.memory_list = lambda status="approved": [{"text": "m" * 9000}] if status == "approved" else []
        client = FakeClient([message(text=json.dumps({"reply": "收到"}))])
        progress = []
        with patch.dict(os.environ, {"LLM_API_KEY": "reasoning roundtrip"}):
            result = agent.run_research("继续", store, client, "fake", previous={"answer": "p" * 13000},
                                        on_progress=lambda *event: progress.append(event))
        self.assertTrue(result["context"]["memory_truncated"])
        self.assertTrue(result["context"]["previous_truncated"])
        self.assertIn(("reasoning", "[REDACTED]"), progress)
        self.assertNotIn("reasoning roundtrip", json.dumps(progress))

    def test_probe_requires_actual_valid_tool_response(self):
        client = FakeClient([message([("capability_probe", {"token": "ok"})])])
        agent.probe_tools(client, "configured-model")
        self.assertEqual(client.requests[0]["tool_choice"], "auto")
        self.assertEqual(client.requests[0]["model"], "configured-model")
        with self.assertRaises(ValueError):
            agent.probe_tools(FakeClient([message(text="ok")]), "configured-model")

    def run_fake(self, responses):
        store = FakeStore()
        client = FakeClient(responses)
        return agent.run_research("测试", store, client, "fake"), store, client

    def test_proposal_and_roundtrip(self):
        result, store, client = self.run_fake([message([("propose_memory", {"text": "关注回撤", "category": "preference"})]), final()])
        self.assertTrue(store.memories[0]["pending"])
        self.assertEqual(store.memories[0]["category"], "preference")
        self.assertIn("## 待确认记忆", result["report"])
        self.assertIn("/approve memory-1", result["report"])
        self.assertEqual(result["pending_memories"], store.memories)
        self.assertEqual(client.requests[1]["messages"][2]["reasoning_content"], "reasoning roundtrip")
        self.assertEqual(client.requests[1]["messages"][3]["tool_call_id"], "call-0")
        self.assertEqual(result["status"], "completed")

    def test_experiment_details_saved_but_not_sent_to_model(self):
        payload = {"summary": {"return": 0.1}, "details": {"orders": "FULL_DETAIL"}, "limitations": ["研究模拟"]}
        with patch.object(agent, "dispatch", return_value=payload):
            result, store, client = self.run_fake([message([("run_backtest", {})]), final()])
        self.assertEqual(store.events[0][2]["result"], payload)
        self.assertNotIn("FULL_DETAIL", result["report"])
        self.assertNotIn("FULL_DETAIL", json.dumps(client.requests[1]))
        self.assertIn("0.1", result["report"])

    def test_no_approval_tool_and_schema(self):
        result, store, _ = self.run_fake([message([("memory_approve", {"id": "1"}), ("propose_memory", {"text": "x", "pending": False})]), final()])
        self.assertEqual(store.memories, [])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["usage"]["tool_calls"], 2)

    def test_timeout_and_cancel_persist(self):
        for error in (TimeoutError("secret"), KeyboardInterrupt()):
            result, store, _ = self.run_fake([error])
            self.assertIsNotNone(store.finished)
            self.assertNotIn("secret", result["report"])
            self.assertNotEqual(result["status"], "completed")

    def test_budget_including_invalid_calls(self):
        result, store, _ = self.run_fake([message([("missing", {})] * 25)])
        self.assertEqual(result["usage"]["tool_calls"], 24)
        self.assertEqual(len(store.events), 25)
        self.assertEqual(result["status"], "partial")

    def test_model_budget(self):
        result, _, client = self.run_fake([message([("propose_memory", {"text": "x"})])] * 12)
        self.assertEqual(len(client.requests), 12)
        self.assertEqual(result["status"], "partial")

    def test_sources_and_rejected_fabrication(self):
        envelope = {"source": "test", "fetched_at": "now", "items": [{"title": "公告", "content_status": "directory", "page": 2}]}
        with patch.object(agent, "dispatch", return_value=envelope):
            result, _, _ = self.run_fake([message([("get_news", {})]), final("目录 [S1]，不存在的 [S9]")])
        self.assertEqual(result["sources"]["S1"]["content_status"], "directory")
        self.assertNotIn("不存在的", result["report"])
        self.assertEqual(result["status"], "partial")

    def test_schema_rejects_nan_bool_and_dates(self):
        for value, schema in [(float("nan"), {"type": "number"}), (True, {"type": "integer"}), ("2024-02-30", agent.DATE)]:
            with self.assertRaises(ValueError):
                agent.validate(value, schema)

    def test_redaction(self):
        with patch.dict(os.environ, {"LLM_API_KEY": "private-token"}):
            self.assertNotIn("private-token", agent.encoded({"x": "private-token"}))


if __name__ == "__main__":
    unittest.main()

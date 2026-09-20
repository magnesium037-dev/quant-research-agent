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

    def memory_list(self):
        return []

    def memory_add(self, text, source="user", pending=False):
        self.memories.append({"text": text, "pending": pending})
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
        result, store, client = self.run_fake([message([("propose_memory", {"text": "关注回撤"})]), final()])
        self.assertTrue(store.memories[0]["pending"])
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

import contextlib
import io
import os
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from qagent.cli import main, parser


class CliTests(unittest.TestCase):
    def test_chat_followup_context_and_thinking_switch(self):
        from qagent.agent import run_research
        from test_agent import FakeClient, message
        import json
        requests = []
        def fake_run(question, store, **kwargs):
            client = FakeClient([message(text=json.dumps({"reply": "你好，我能查询资料。"}))])
            result = run_research(question, store, client, "fake", **kwargs)
            requests.append((question, kwargs, client.requests))
            return result
        with tempfile.TemporaryDirectory() as folder:
            stream = io.StringIO()
            with patch.dict(os.environ, {"QAGENT_HOME": folder}), patch("qagent.cli.run_research", side_effect=fake_run), \
                 patch("builtins.input", side_effect=["你好", "/context", "/thinking off", "你能做什么", "/new", "再来", "/exit"]), \
                 contextlib.redirect_stdout(stream):
                self.assertEqual(main(["chat"]), 0)
            self.assertEqual(requests[1][0], "你能做什么")
            self.assertEqual(requests[1][1]["previous"]["question"], "你好")
            self.assertIsNone(requests[2][1]["previous"])
            self.assertIn('"messages"', stream.getvalue())
            self.assertIn("[上下文]", stream.getvalue())
            self.assertEqual(stream.getvalue().count("reasoning roundtrip"), 1)
            self.assertNotIn("## 证据", stream.getvalue())

    def test_commands(self):
        for command in (["ask", "test", "--file", "x.pdf"], ["chat"], ["memory", "approve", "a"],
                        ["runs", "show", "b"], ["backtest", "--symbols", "510300", "510500", "--start", "2020-01-01", "--end", "2021-01-01"]):
            self.assertTrue(parser().parse_args(command).command)

    def test_bare_command_defaults_to_chat(self):
        with patch("qagent.cli.run_research") as run, patch("builtins.input", side_effect=EOFError), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main([]), 0)
        run.assert_not_called()

    def test_doctor_offline_no_client_no_secret(self):
        stream = io.StringIO()
        with patch.dict(os.environ, {"LLM_BASE_URL": "https://test", "LLM_MODEL": "fake", "LLM_API_KEY": "SECRET"}), patch("qagent.cli.client_from_env") as client, contextlib.redirect_stdout(stream):
            self.assertEqual(main(["doctor"]), 0)
            client.assert_not_called()
        self.assertNotIn("SECRET", stream.getvalue())

    def test_doctor_missing_config(self):
        with patch.dict(os.environ, {"QAGENT_CONFIG": str(Path(tempfile.gettempdir()) / "qagent-no-config.json")}, clear=True), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["doctor"]), 1)

    def test_reasonix_config_is_used_without_printing_key(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            path.write_text('{"apiKey":"sk-test-secret-1234567890","model":"deepseek-v4-flash"}', encoding="utf-8")
            stream = io.StringIO()
            with patch.dict(os.environ, {"QAGENT_CONFIG": str(path)}, clear=True), contextlib.redirect_stdout(stream):
                self.assertEqual(main(["doctor"]), 0)
            self.assertIn("deepseek-flash", stream.getvalue())
            self.assertNotIn("sk-test-secret", stream.getvalue())


if __name__ == "__main__":
    unittest.main()

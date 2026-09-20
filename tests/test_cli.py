import contextlib
import io
import os
import unittest
from unittest.mock import patch

from qagent.cli import main, parser


class CliTests(unittest.TestCase):
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
        with patch.dict(os.environ, {}, clear=True), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["doctor"]), 1)


if __name__ == "__main__":
    unittest.main()

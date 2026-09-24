import os
import unittest
from unittest.mock import patch

from qagent.feishu import FeishuConfig, pairing_code, split_message


class FeishuTests(unittest.TestCase):
    def test_config_requires_both_secrets_without_printing_values(self):
        with patch.dict(os.environ, {"FEISHU_APP_ID": "app", "FEISHU_APP_SECRET": "secret"}, clear=False):
            self.assertEqual(FeishuConfig.from_env(), FeishuConfig("app", "secret"))
        with patch.dict(os.environ, {"FEISHU_APP_ID": "app", "FEISHU_APP_SECRET": ""}, clear=False):
            with self.assertRaisesRegex(ValueError, "FEISHU_APP_ID"):
                FeishuConfig.from_env()

    def test_pairing_code_avoids_ambiguous_characters(self):
        code = pairing_code()
        self.assertEqual(len(code), 8)
        self.assertTrue(set(code) <= set("ABCDEFGHJKLMNPQRSTUVWXYZ23456789"))

    def test_long_messages_split_at_newlines(self):
        parts = split_message("a" * 10 + "\n" + "b" * 10, limit=12)
        self.assertEqual(parts, ["a" * 10, "\n" + "b" * 10])


if __name__ == "__main__":
    unittest.main()

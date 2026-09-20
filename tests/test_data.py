import json
import subprocess
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import pandas as pd
from qagent import data


class DataTests(unittest.TestCase):
    def test_news_freshness_and_provenance(self):
        rows = pd.DataFrame([{"标题": str(n), "发布时间": stamp, "摘要": "evidence", "链接": "https://example.com/n"}
                             for n, stamp in enumerate(["2026-09-16 08:00:00", "2026-09-01", "2026-09-18", "unknown"])])
        with patch.object(data, "_call", return_value=rows), patch.object(data, "_now", return_value=datetime(2026, 9, 17, tzinfo=timezone.utc)):
            result = data.get_news(days=7)
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["content_status"], "summary")
        self.assertTrue(any("future" in w for w in result["warnings"]))
        self.assertTrue(any("unknown" in w for w in result["warnings"]))
        json.dumps(result, allow_nan=False)

    def test_directories_are_not_fulltext(self):
        with patch.object(data, "_call", return_value=pd.DataFrame([{"公告标题": "report", "公告日期": "2026-09-01", "报告ID": "AN123"}])):
            item = data.get_disclosures("510300", kind="fund")["items"][0]
            self.assertEqual(item["content_status"], "directory")
            self.assertIsNone(item["url"])
        with patch.object(data, "_call", return_value=pd.DataFrame([{"报告名称": "view", "日期": "2026-09-01", "报告PDF链接": "https://example.com/r.pdf"}])):
            item = data.get_research_reports("600519")["items"][0]
            self.assertEqual(item["source_type"], "analyst_opinion")
            self.assertEqual(item["url"], "https://example.com/r.pdf")

    def test_history_rejects_invalid_without_filling(self):
        frame = pd.DataFrame({"日期": ["2024-01-02", "2024-01-04"], "开盘": [1, 2], "最高": [2, 3],
                              "最低": [1, 1], "收盘": [2, 2], "成交量": [100, 200]})
        with patch.object(data, "_call", return_value=frame):
            out = data.get_history("510300", "2024-01-01", "2024-01-05")
            self.assertEqual(len(out), 2)  # Missing dates are left for calendar-aware backtest rejection.
            self.assertEqual(list(out.columns), ["Open", "High", "Low", "Close", "Volume"])
            self.assertEqual(out.attrs["adjust"], "hfq")
        for column, invalid in [("成交量", 0), ("最高", .1), ("收盘", float("nan"))]:
            broken = frame.copy()
            broken[column] = broken[column].astype(float)
            broken.loc[0, column] = invalid
            with patch.object(data, "_call", return_value=broken), self.assertRaises(ValueError):
                data.get_history("510300", "2024-01-01", "2024-01-05")

    def test_timeout_and_failure_do_not_become_empty_success(self):
        with patch.object(subprocess, "run", side_effect=subprocess.TimeoutExpired("worker", 30)), self.assertRaises(TimeoutError):
            data.get_calendar()
        with patch.object(subprocess, "run", return_value=subprocess.CompletedProcess([], 1, stderr=b"ProxyError")), self.assertRaisesRegex(ValueError, "ProxyError"):
            data.get_calendar()
        with self.assertRaises(ValueError):
            data.get_history("510300", "2024-01-02", "2024-01-01")

    def test_quotes_missing_symbol_and_nan(self):
        with patch.object(data, "_call", return_value=pd.DataFrame([{"代码": "510300", "名称": "ETF", "最新价": float("nan")}])):
            result = data.get_quotes(["510300"])
            json.dumps(result, allow_nan=False)
            with self.assertRaises(ValueError):
                data.get_quotes(["510300", "510500"])

    def test_worker_json_roundtrip_preserves_frame(self):
        frame = pd.DataFrame({"trade_date": ["2024-01-02", "2024-01-03"]})
        response = subprocess.CompletedProcess([], 0, stdout=frame.to_json(orient="table").encode())
        with patch.object(subprocess, "run", return_value=response):
            self.assertEqual(list(data.get_calendar()), [pd.Timestamp("2024-01-02"), pd.Timestamp("2024-01-03")])


if __name__ == "__main__":
    unittest.main()

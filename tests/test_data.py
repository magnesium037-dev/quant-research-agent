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
        fields = [""] * 88
        fields[1:7] = ["ETF", "510300", "nan", "1.0", "1.0", "100"]
        fields[30], fields[32] = "20260918150000", "0.0"
        raw = ('v_sh510300="' + "~".join(fields) + '";').encode("gbk")
        parsed = data._parse_tencent_quotes(raw)
        with patch.object(data, "_fetch_tencent_quotes", return_value=parsed):
            result = data.get_quotes(["510300"])
            json.dumps(result, allow_nan=False)
            self.assertEqual(result["status"], "success")
            self.assertIsNone(result["data"][0]["latest_price"])
        parsed[0]["latest_price"] = 1.0
        with patch.object(data, "_fetch_tencent_quotes", return_value=parsed), \
                patch.object(data, "_now", return_value=datetime(2026, 9, 21, tzinfo=timezone.utc)):
            result = data.get_quotes(["510300", "510500"])
            self.assertEqual(result["status"], "partial_success")
            self.assertEqual(result["missing"], ["510500"])
            self.assertEqual(result["data_date"], "2026-09-18")
            self.assertEqual(result["data"][0]["quote_time"], "2026-09-18T15:00:00+08:00")
            self.assertIsNone(result["data"][0]["provider_discount_rate"])
            self.assertTrue(any("today" in warning for warning in result["warnings"]))

    def test_worker_json_roundtrip_preserves_frame(self):
        frame = pd.DataFrame({"trade_date": ["2024-01-02", "2024-01-03"]})
        response = subprocess.CompletedProcess([], 0, stdout=frame.to_json(orient="table").encode())
        with patch.object(subprocess, "run", return_value=response):
            self.assertEqual(list(data.get_calendar()), [pd.Timestamp("2024-01-02"), pd.Timestamp("2024-01-03")])

    def test_screen_etfs_ranks_completed_bars_and_adds_live_quotes(self):
        start = datetime(2026, 7, 14).date()
        slopes = {symbol: -0.1 for symbol in data.ETF_UNIVERSE}
        slopes.update({"513100": 1.0, "512480": 0.8, "510500": 0.6})

        def history(symbol, count=70):
            return [((start + pd.Timedelta(days=i)).isoformat(), 100 + slopes[symbol] * i) for i in range(70)]

        def quotes(symbols):
            return {"data": [{"symbol": symbol, "name": data.ETF_UNIVERSE[symbol], "latest_price": 1.0,
                              "change_pct": 0.1, "quote_time": "2026-09-21T14:00:00+08:00"}
                             for symbol in symbols], "missing": []}

        now = datetime(2026, 9, 21, 6, tzinfo=timezone.utc)
        with patch.object(data, "_fetch_tencent_daily", side_effect=history), \
                patch.object(data, "get_quotes", side_effect=quotes), patch.object(data, "_now", return_value=now):
            result = data.screen_etfs()
        self.assertEqual([row["symbol"] for row in result["data"]], ["513100", "512480", "510500"])
        self.assertTrue(all(row["signal_date"] != "2026-09-21" for row in result["data"]))
        self.assertEqual([row["rank"] for row in result["data"]], [1, 2, 3])
        json.dumps(result, ensure_ascii=False, allow_nan=False)


if __name__ == "__main__":
    unittest.main()

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

from qagent.backtest import rotation_targets, run_backtest, simulate
from qagent.store import Store


class BacktestTests(unittest.TestCase):
    def test_shared_cash_next_open_sell_before_buy_and_cost(self):
        days = pd.bdate_range("2024-01-01", periods=4)
        close = pd.DataFrame([[10, 10], [10, 10], [10, 10], [10, 10]], index=days, columns=["a", "b"])
        targets = pd.DataFrame([[np.nan, np.nan], [.99, 0], [0, .99], [0, 0]], index=days, columns=close.columns)
        metrics, detail = simulate(close, close, targets, 100, .0005)
        self.assertAlmostEqual(detail["orders"][0]["quantity"], 9.9)
        self.assertAlmostEqual(detail["daily"][0]["cash"], .9505)
        self.assertEqual([o["side"] for o in detail["orders"]], ["buy", "sell", "buy", "sell"])
        self.assertEqual(detail["orders"][0]["date"], "2024-01-02")
        self.assertAlmostEqual(metrics["final_equity"], 100 - metrics["cost"])
        self.assertTrue(all(d["cash"] >= 0 for d in detail["daily"]))

    def test_gap_uses_prior_close_quantity_and_partial_fill(self):
        days = pd.bdate_range("2024-01-01", periods=2)
        close = pd.DataFrame([[10, 10], [20, 10]], index=days, columns=["a", "b"])
        weights = pd.DataFrame([[np.nan, np.nan], [.99, 0]], index=days, columns=close.columns)
        metrics, detail = simulate(close, close, weights, 100, .001)
        self.assertAlmostEqual(detail["daily"][0]["target_quantities"]["a"], 9.9)
        self.assertAlmostEqual(detail["orders"][0]["quantity"], 100 / 20 / 1.001)
        self.assertTrue(detail["daily"][0]["partial_or_unfilled"])
        self.assertAlmostEqual(metrics["final_equity"], 100 / 1.001)

    def test_signals_no_future_leak_short_week_and_negative(self):
        calendar = pd.bdate_range("2024-01-01", periods=12).difference(pd.DatetimeIndex(["2024-01-05"]))
        close = pd.DataFrame({"a": np.arange(len(calendar)) + 10., "b": np.arange(len(calendar)) + 10.}, index=calendar)
        targets = rotation_targets(close, calendar, 1, 1, "weekly")
        self.assertEqual(targets.loc["2024-01-08", "a"], .99)
        changed = close.copy()
        changed.iloc[6:] *= 10
        pd.testing.assert_frame_equal(targets.iloc[:7], rotation_targets(changed, calendar, 1, 1, "weekly").iloc[:7])
        negative = rotation_targets(100 - close, calendar, 1, 2, "daily")
        self.assertEqual(float(negative.iloc[2:].sum().sum()), 0)
        one_positive = close.copy()
        one_positive.b = 100 - close.b
        self.assertEqual(rotation_targets(one_positive, calendar, 1, 2, "daily").iloc[2].sum(), .495)

    def _inputs(self):
        calendar = pd.bdate_range("2023-10-01", "2024-03-01")
        def history(symbol, start, end, adjust):
            dates = calendar[(calendar >= start) & (calendar <= end)]
            value = np.arange(len(dates)) * .01 + 10
            return pd.DataFrame({"Open": value, "High": value + .1, "Low": value - .1,
                                 "Close": value, "Volume": 1000.}, index=dates)
        return calendar, history

    def test_public_run_splits_snapshots_and_repeat_holdout(self):
        calendar, history = self._inputs()
        with tempfile.TemporaryDirectory() as tmp, patch("qagent.backtest.get_calendar", return_value=calendar), patch("qagent.backtest.get_history", side_effect=history):
            store = Store(Path(tmp))
            run = store.create_run("test")
            args = dict(symbols=["510300", "510500"], start="2024-01-01", end="2024-01-31", lookback=20, rebalance="daily", store=store, run_id=run)
            first = run_backtest(**args)
            second = run_backtest(**args)
            self.assertFalse(first["holdout_previously_viewed"])
            self.assertTrue(second["holdout_previously_viewed"])
            self.assertEqual(first["snapshot_id"], second["snapshot_id"])
            self.assertEqual(len(store.get_run(run)["experiments"]), 2)
            snapshot = store.get_snapshot(first["snapshot_id"])["prices"]["510300"]
            for period in ("full", "development", "holdout"):
                result = first["summary"][period]
                self.assertEqual(result["strategy"]["initial_cash"], 100000)
                self.assertEqual(set(result["cost_sensitivity_bps"]), {"0", "5", "10"})
                first_day = first["details"][period]["strategy"]["daily"][0]
                previous_index = snapshot["dates"].index(first_day["date"]) - 1
                previous_close = snapshot["values"][previous_index][snapshot["columns"].index("Close")]
                self.assertAlmostEqual(first_day["target_quantities"]["510300"], .495 * 100000 / previous_close)
                self.assertEqual(first["details"][period]["equal_weight"]["daily"][0]["target_weights"], {"510300": .5, "510500": .5})
            store.close()

    def test_invalid_data_calendar_warmup_and_failed_attempts(self):
        calendar, history = self._inputs()
        args = dict(symbols=["510300", "510500"], start="2024-01-01", end="2024-01-31", lookback=20)
        for corruption in ("missing", "volume", "ohlc"):
            def bad(*a, **kw):
                frame = history(*a, **kw)
                if corruption == "missing":
                    return frame.iloc[1:]
                frame.iloc[0, frame.columns.get_loc("Volume" if corruption == "volume" else "High")] = 0
                return frame
            with patch("qagent.backtest.get_calendar", return_value=calendar), patch("qagent.backtest.get_history", side_effect=bad), self.assertRaises(ValueError):
                run_backtest(**args)
        with tempfile.TemporaryDirectory() as tmp, patch("qagent.backtest.get_calendar", return_value=calendar[calendar >= "2024-01-01"]):
            store = Store(Path(tmp))
            run = store.create_run("failure")
            with self.assertRaisesRegex(ValueError, "warmup"):
                run_backtest(**args, store=store, run_id=run)
            self.assertEqual(store.get_run(run)["experiments"][0]["status"], "failed")
            store.close()
        with patch("qagent.backtest.get_calendar", return_value=calendar[calendar <= "2024-01-31"]), self.assertRaisesRegex(ValueError, "calendar"):
            run_backtest(**args)


if __name__ == "__main__":
    unittest.main()

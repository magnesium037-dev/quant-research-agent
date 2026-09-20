"""Deterministic, fractional hfq ETF portfolio research; no trading interface."""
import hashlib
import json
import math
import re
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd

from .data import get_calendar, get_history

LIMITATIONS = [
    "复权价格上的小数单位组合研究模拟；同一开盘价格按先卖后买顺序撮合。",
    "未复现整手交易、最低佣金、分红到账、涨跌停排队和市场冲击。",
    "固定 ETF 池存在选择偏差；当前新闻不进入历史信号。",
    "留出被查看标记仅覆盖本地实验记录，不能证明其他渠道未查看。",
]


def rotation_targets(close, calendar, lookback, top_k, rebalance):
    """Targets indexed by EXECUTION day, determined solely at previous close."""
    momentum = close / close.shift(lookback) - 1
    targets = pd.DataFrame(np.nan, index=close.index, columns=close.columns)
    for i in range(lookback, len(close) - 1):
        day = close.index[i]
        next_day = calendar[calendar.get_loc(day) + 1]
        if rebalance == "weekly" and day.to_period("W-FRI") == next_day.to_period("W-FRI"):
            continue
        ranked = momentum.iloc[i].sort_values(ascending=False, kind="stable")
        selected = ranked[ranked > 0].index[:top_k]
        targets.iloc[i + 1] = 0.0
        targets.loc[close.index[i + 1], selected] = .99 / top_k
    return targets


def simulate(close, opening, targets, initial_cash, fee):
    """First row is prior-close valuation only; remaining rows are evaluated."""
    import vectorbt as vbt
    portfolio = vbt.Portfolio.from_orders(
        close, size=targets, price=opening, size_type="targetpercent",
        val_price=-np.inf, init_cash=initial_cash, cash_sharing=True,
        group_by=True, call_seq="auto", update_value=False,
        direction="longonly", allow_partial=True, fees=fee, log=True, freq="1D",
    )
    equity = portfolio.value()
    cash = portfolio.cash()
    holdings = portfolio.assets()
    if (cash < -1e-7).any() or (holdings < -1e-7).any().any():
        raise ArithmeticError("Portfolio violated long-only/shared-cash constraints")
    orders = []
    for row in portfolio.orders.records.itertuples(index=False):
        orders.append({"date": close.index[row.idx].date().isoformat(),
                       "symbol": str(close.columns[row.col]), "side": "buy" if row.side == 0 else "sell",
                       "quantity": float(row.size), "price": float(row.price), "fees": float(row.fees)})
    days = []
    for i in range(1, len(close)):
        opening_value = float(cash.iloc[i] + (holdings.iloc[i] * opening.iloc[i]).sum())
        target = targets.iloc[i]
        quantities = target * equity.iloc[i - 1] / close.iloc[i - 1]
        partial = bool(target.notna().any() and not np.allclose(
            holdings.iloc[i].to_numpy(), quantities.to_numpy(), atol=1e-8, rtol=1e-8))
        days.append({"date": close.index[i].date().isoformat(), "equity": float(equity.iloc[i]),
                     "net_value": float(equity.iloc[i] / initial_cash), "cash": float(cash.iloc[i]),
                     "holdings": holdings.iloc[i].to_dict(),
                     "target_weights": target.to_dict() if target.notna().any() else None,
                     "target_quantities": quantities.to_dict() if target.notna().any() else None,
                     "actual_open_weights": (holdings.iloc[i] * opening.iloc[i] / opening_value).to_dict(),
                     "partial_or_unfilled": partial})
    values = equity.to_numpy(dtype=float)
    gain = float(values[-1] / initial_cash - 1)
    annual_log = math.log(values[-1] / initial_cash) * 252 / (len(values) - 1)
    annual = math.expm1(annual_log) if annual_log < 700 else None
    summary = {"initial_cash": initial_cash, "final_equity": float(values[-1]),
               "total_return": gain, "annualized_return": annual,
               "max_drawdown": float((1 - values / np.maximum.accumulate(values)).max()),
               "turnover": sum(o["quantity"] * o["price"] for o in orders) / float(values[1:].mean()),
               "turnover_definition": "sum absolute traded notional / mean daily equity",
               "cost": sum(o["fees"] for o in orders), "order_count": len(orders),
               "partial_days": sum(d["partial_or_unfilled"] for d in days)}
    return summary, {"daily": days, "orders": orders}


def _validate_frame(frame, expected, symbol):
    if not isinstance(frame, pd.DataFrame) or not isinstance(frame.index, pd.DatetimeIndex):
        raise ValueError(f"{symbol}: invalid daily history")
    if frame.index.has_duplicates or not frame.index.is_monotonic_increasing or frame.index.tz is not None:
        raise ValueError(f"{symbol}: invalid daily dates")
    if not frame.index.equals(expected):
        raise ValueError(f"{symbol}: missing or unexpected trading dates / insufficient warmup")
    columns = ["Open", "High", "Low", "Close", "Volume"]
    if not set(columns).issubset(frame):
        raise ValueError(f"{symbol}: missing OHLCV columns")
    frame = frame[columns].astype(float)
    if not np.isfinite(frame.to_numpy()).all() or (frame <= 0).any().any():
        raise ValueError(f"{symbol}: nonfinite, nonpositive price or zero volume")
    if ((frame.High < frame[["Open", "Close", "Low"]].max(axis=1)) |
            (frame.Low > frame[["Open", "Close", "High"]].min(axis=1))).any():
        raise ValueError(f"{symbol}: illegal OHLC")
    return frame


def run_backtest(symbols, start, end, lookback=60, top_k=2, rebalance="weekly",
                 initial_cash=100000, store=None, run_id=None):
    params = dict(symbols=symbols, start=start, end=end, lookback=lookback, top_k=top_k,
                  rebalance=rebalance, initial_cash=initial_cash, template="momentum_rotation", adjust="hfq")
    snapshot_id = None
    try:
        if (not isinstance(symbols, list) or not 2 <= len(symbols) <= 20 or
                any(not isinstance(s, str) or not re.fullmatch(r"[0-9]{6}", s) for s in symbols) or
                len(set(symbols)) != len(symbols)):
            raise ValueError("Specify 2..20 distinct six-digit ETF codes")
        if type(lookback) is not int or lookback not in (20, 60, 120) or type(top_k) is not int or top_k not in (1, 2):
            raise ValueError("Invalid momentum parameters")
        if rebalance not in ("daily", "weekly") or type(initial_cash) not in (int, float) or not math.isfinite(initial_cash) or initial_cash <= 0:
            raise ValueError("Invalid rebalance or initial cash")
        first, last = date.fromisoformat(start), date.fromisoformat(end)
        if first > last:
            raise ValueError("start must not exceed end")
        yesterday = datetime.now(timezone(timedelta(hours=8))).date() - timedelta(days=1)
        last = min(last, yesterday)
        calendar = get_calendar()
        if (not isinstance(calendar, pd.DatetimeIndex) or calendar.empty or calendar.hasnans or
                calendar.has_duplicates or not calendar.is_monotonic_increasing or calendar.tz is not None or
                not calendar.equals(calendar.normalize()) or calendar[0] > pd.Timestamp(first) or calendar[-1] <= pd.Timestamp(last)):
            raise ValueError("Trading calendar does not cover requested range and next session")
        evaluation = calendar[(calendar >= pd.Timestamp(first)) & (calendar <= pd.Timestamp(last))]
        if len(evaluation) < 4:
            raise ValueError("Need at least four completed evaluation sessions")
        pos = calendar.get_loc(evaluation[0])
        if pos < lookback + 1:
            raise ValueError("Trading calendar has insufficient warmup")
        expected = calendar[pos - lookback - 1:calendar.get_loc(evaluation[-1]) + 1]
        frames, metadata = {}, {}
        for symbol in sorted(symbols):
            frame = get_history(symbol, expected[0].date().isoformat(), expected[-1].date().isoformat(), adjust="hfq")
            metadata[symbol] = dict(frame.attrs)
            frames[symbol] = _validate_frame(frame, expected, symbol)
        close = pd.DataFrame({s: f.Close for s, f in frames.items()})
        opening = pd.DataFrame({s: f.Open for s, f in frames.items()})
        payload = {"source": "AKShare/fund_etf_hist_em", "adjust": "hfq", "metadata": metadata,
                   "calendar": [d.date().isoformat() for d in calendar],
                   "prices": {s: {"dates": [d.date().isoformat() for d in f.index],
                                  "columns": list(f.columns), "values": f.to_numpy().tolist()} for s, f in frames.items()}}
        snapshot_id = (store.save_snapshot(payload) if store else hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest())
        targets = rotation_targets(close, calendar, lookback, top_k, rebalance)
        split = int(len(evaluation) * .7)
        periods = {"full": evaluation, "development": evaluation[:split], "holdout": evaluation[split:]}
        summary, details = {}, {}
        for name, days in periods.items():
            previous = calendar[calendar.get_loc(days[0]) - 1]
            dates = pd.DatetimeIndex([previous, *days])
            prices, opens, weights = close.loc[dates], opening.loc[dates], targets.loc[dates].copy()
            weights.iloc[0] = np.nan
            benchmark = pd.DataFrame(np.nan, index=dates, columns=close.columns)
            benchmark.iloc[1] = 1.0 / len(symbols)
            summary[name], details[name] = {"start": str(days[0].date()), "end": str(days[-1].date())}, {}
            sensitivity = {}
            for bps in (5, 0, 10):
                pair = {}
                for label, allocation in (("strategy", weights), ("equal_weight", benchmark)):
                    metrics, detail = simulate(prices, opens, allocation, initial_cash, bps / 10000)
                    pair[label] = metrics
                    if bps == 5:
                        summary[name][label], details[name][label] = metrics, detail
                pair["return_difference"] = pair["strategy"]["total_return"] - pair["equal_weight"]["total_return"]
                sensitivity[str(bps)] = pair
            summary[name]["cost_sensitivity_bps"] = sensitivity
            summary[name]["return_difference"] = sensitivity["5"]["return_difference"]
            summary[name]["cash"] = {"initial_cash": initial_cash, "final_equity": initial_cash,
                                     "total_return": 0.0, "annualized_return": 0.0, "max_drawdown": 0.0, "cost": 0.0}
        result = {"status": "completed", "parameters": params, "snapshot_id": snapshot_id,
                  "holdout_start": str(periods["holdout"][0].date()), "holdout_end": str(periods["holdout"][-1].date()),
                  "summary": summary, "details": details, "limitations": LIMITATIONS,
                  "holdout_previously_viewed": False}
        if store is not None:
            result = store.record_experiment(run_id, params, snapshot_id, result)["result"]
        return result
    except Exception as exc:
        if store is not None:
            store.record_experiment(run_id, params, snapshot_id,
                                    {"status": "failed", "error": str(exc), "parameters": params, "snapshot_id": snapshot_id})
        raise

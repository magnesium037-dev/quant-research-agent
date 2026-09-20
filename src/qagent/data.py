"""Bounded public data adapters. Provider failures never become invented data."""
import io
import json
import re
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone

CALL_TIMEOUT = 30
APIS = {"stock_info_global_em", "stock_news_em", "fund_etf_spot_em", "fund_etf_hist_em",
        "stock_individual_notice_report", "stock_research_report_em",
        "fund_announcement_report_em", "tool_trade_date_hist_sina"}
MARKET_TZ = timezone(timedelta(hours=8))


def _call(name, **kwargs):
    if name not in APIS:
        raise ValueError("Unknown data API")
    try:
        result = subprocess.run([sys.executable, "-m", "qagent.data", name, json.dumps(kwargs)],
                                capture_output=True, timeout=CALL_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        raise TimeoutError(f"AKShare {name} exceeded {CALL_TIMEOUT}s") from None
    if result.returncode:
        kind = result.stderr.decode("ascii", errors="ignore").strip()
        detail = kind if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,80}", kind) else "ProviderError"
        raise ValueError(f"AKShare {name} failed ({detail}); no data available")
    import pandas as pd
    return pd.read_json(io.StringIO(result.stdout.decode("utf-8")), orient="table")


def _symbol(symbol):
    if not isinstance(symbol, str) or not re.fullmatch(r"[0-9]{6}", symbol):
        raise ValueError("symbol must be a six digit code")
    return symbol


def _limit(limit):
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("limit must be 1..50")


def _date(value):
    return date.fromisoformat(value).isoformat()


def _now():
    return datetime.now(timezone.utc)


def _records(frame):
    return json.loads(frame.to_json(orient="records", date_format="iso", force_ascii=False))


def _pick(row, *names):
    return next((row[n] for n in names if row.get(n) is not None), None)


def _time(value):
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return stamp if stamp.tzinfo else stamp.replace(tzinfo=MARKET_TZ)
    except ValueError:
        return None


def _item(row, title, time, url, source_type, status="directory", content=None):
    stamp = _time(_pick(row, *time))
    return {"title": str(_pick(row, *title) or "Untitled"), "source_type": source_type,
            "url": _pick(row, *url), "published_at": stamp.isoformat() if stamp else None,
            "content": str(content if content is not None else _pick(row, *title) or ""),
            "content_status": status, "raw": row}


def _envelope(items, api, warnings=None):
    warnings = list(warnings or [])
    if not items:
        warnings.append("No matching records returned; this does not establish that no event exists.")
    if any(i["published_at"] is None for i in items):
        warnings.append("Some publication times are unknown; fetched_at is not publication time.")
    return {"items": items, "source": "AKShare/" + api, "fetched_at": _now().isoformat(), "warnings": warnings}


def get_news(keyword="", days=7, limit=20):
    _limit(limit)
    if type(days) is not int or not 1 <= days <= 365 or not isinstance(keyword, str) or len(keyword) > 100:
        raise ValueError("Invalid news query")
    api = "stock_news_em" if keyword else "stock_info_global_em"
    rows = _records(_call(api, **({"symbol": keyword} if keyword else {})))
    now = _now()
    items, warnings = [], ["Provider returns a limited recent feed, not exhaustive date-range coverage."]
    for row in rows:
        item = _item(row, ("新闻标题", "标题"), ("发布时间",), ("新闻链接", "链接"),
                     "news", "summary", _pick(row, "新闻内容", "摘要"))
        stamp = _time(item["published_at"])
        if stamp is None:
            warnings.append("Excluded news with unknown publication time; freshness cannot be verified.")
            continue
        if stamp > now:
            warnings.append("Excluded future-dated news; provider timestamp requires verification.")
            continue
        if stamp >= now - timedelta(days=days):
            items.append(item)
    items.sort(key=lambda x: x["published_at"], reverse=True)
    return _envelope(items[:limit], api, list(dict.fromkeys(warnings)))


def get_quotes(symbols):
    if not isinstance(symbols, list) or not 1 <= len(symbols) <= 20 or len(set(symbols)) != len(symbols):
        raise ValueError("Specify 1..20 unique ETF symbols")
    codes = {_symbol(s) for s in symbols}
    api = "fund_etf_spot_em"
    rows = _records(_call(api))
    rows = [r for r in rows if str(r.get("代码", "")).zfill(6) in codes]
    found = {str(r["代码"]).zfill(6) for r in rows}
    if found != codes:
        raise ValueError("ETF quotes missing for: " + ", ".join(sorted(codes - found)))
    return _envelope([_item(r, ("名称",), ("数据日期",), (), "market_quote", "summary",
                     json.dumps(r, ensure_ascii=False)) for r in rows], api,
                     ["Unadjusted displayed quotes; freshness is not guaranteed."])


def get_disclosures(symbol, kind="company", start="", end="", limit=20):
    _symbol(symbol)
    _limit(limit)
    start, end = _date(start) if start else "", _date(end) if end else ""
    if start and end and start > end:
        raise ValueError("start must not exceed end")
    if kind == "company":
        api = "stock_individual_notice_report"
        frame = _call(api, security=symbol, symbol="全部", begin_date=start.replace("-", ""), end_date=end.replace("-", ""))
    elif kind == "fund":
        api = "fund_announcement_report_em"
        frame = _call(api, symbol=symbol)
    else:
        raise ValueError("kind must be company or fund")
    items = [_item(r, ("公告标题",), ("公告日期",), ("网址", "链接"), "disclosure_directory") for r in _records(frame)]
    # No invented issuer URLs: fund report IDs stay in raw metadata until a real URL is available.
    items = [i for i in items if (not start or (i["published_at"] and i["published_at"][:10] >= start))
             and (not end or (i["published_at"] and i["published_at"][:10] <= end))]
    items.sort(key=lambda x: x["published_at"] or "", reverse=True)
    return _envelope(items[:limit], api, ["Directory only, not document text; Eastmoney is an intermediary, not the issuer."])


def get_research_reports(symbol, limit=20):
    _symbol(symbol)
    _limit(limit)
    api = "stock_research_report_em"
    items = [_item(r, ("报告名称",), ("日期",), ("报告PDF链接",), "analyst_opinion")
             for r in _records(_call(api, symbol=symbol))]
    items.sort(key=lambda x: x["published_at"] or "", reverse=True)
    return _envelope(items[:limit], api, ["Analyst opinions; directory metadata is not the report's full text."])


def get_history(symbol, start, end, adjust="hfq"):
    _symbol(symbol)
    start, end = _date(start), _date(end)
    if start > end or adjust != "hfq":
        raise ValueError("Require start <= end and hfq research prices")
    import pandas as pd
    import numpy as np
    frame = _call("fund_etf_hist_em", symbol=symbol, period="daily", start_date=start.replace("-", ""),
                  end_date=end.replace("-", ""), adjust=adjust)
    names = {"开盘": "Open", "最高": "High", "最低": "Low", "收盘": "Close", "成交量": "Volume"}
    if frame.empty or not {"日期", *names}.issubset(frame.columns):
        raise ValueError("ETF history is empty or missing required columns")
    result = frame.rename(columns=names).set_index("日期")[list(names.values())].copy()
    result.index = pd.DatetimeIndex(pd.to_datetime(result.index, errors="raise"))
    if result.index.has_duplicates or result.index.hasnans or not (result.index == result.index.normalize()).all():
        raise ValueError("Invalid or duplicate daily dates")
    for col in result:
        result[col] = pd.to_numeric(result[col], errors="raise")
    if not np.isfinite(result.to_numpy(dtype=float)).all() or (result <= 0).any().any():
        raise ValueError("Nonpositive, zero-volume, or nonfinite history")
    if ((result.High < result[["Open", "Close", "Low"]].max(axis=1)) |
            (result.Low > result[["Open", "Close", "High"]].min(axis=1))).any():
        raise ValueError("Invalid OHLC range")
    if (result.index < pd.Timestamp(start)).any() or (result.index > pd.Timestamp(end)).any():
        raise ValueError("Provider returned dates outside requested range")
    result = result.sort_index()
    result.attrs.update(source="AKShare/fund_etf_hist_em", fetched_at=_now().isoformat(), adjust=adjust, symbol=symbol)
    return result


def get_calendar():
    import pandas as pd
    frame = _call("tool_trade_date_hist_sina")
    if "trade_date" not in frame or frame.empty:
        raise ValueError("Trading calendar unavailable")
    result = pd.DatetimeIndex(pd.to_datetime(frame["trade_date"], errors="raise"))
    if result.has_duplicates or result.hasnans:
        raise ValueError("Invalid trading calendar")
    return result.sort_values()


if __name__ == "__main__":
    # Separate interpreter allows a hard timeout even when a provider's HTTP call hangs.
    from contextlib import redirect_stdout, redirect_stderr
    name = sys.argv[1]
    if name not in APIS:
        sys.exit(2)
    try:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            import akshare as ak
            frame = getattr(ak, name)(**json.loads(sys.argv[2]))
        sys.stdout.buffer.write(frame.to_json(orient="table", date_format="iso", force_ascii=False).encode("utf-8"))
    except Exception as exc:
        sys.stderr.write(type(exc).__name__)
        sys.exit(1)

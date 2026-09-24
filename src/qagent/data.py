"""Bounded public data adapters. Provider failures never become invented data."""
import io
import json
import math
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from urllib.error import URLError
from urllib.parse import quote as urlquote
from urllib.request import Request, urlopen

CALL_TIMEOUT = 30
QUOTE_TIMEOUT = 5
QUOTE_URL = "https://qt.gtimg.cn/q="
KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param="
ETF_UNIVERSE = {
    "510300": "沪深300", "510500": "中证500", "159915": "创业板",
    "588000": "科创50", "512480": "半导体", "512880": "证券",
    "513100": "纳斯达克100", "513500": "标普500", "518880": "黄金",
    "511260": "十年期国债",
}
APIS = {"stock_info_global_em", "stock_news_em", "fund_etf_hist_em",
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


def _safe(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


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


def _number(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _parse_tencent_quotes(raw):
    items = []
    for line in raw.decode("gbk").splitlines():
        match = re.fullmatch(r'v_(?:sh|sz)(\d{6})="(.*)";', line.strip())
        if not match:
            continue
        fields = match.group(2).split("~")
        if len(fields) <= 38 or fields[2] != match.group(1):
            continue
        stamp = None
        try:
            stamp = datetime.strptime(fields[30], "%Y%m%d%H%M%S").replace(tzinfo=MARKET_TZ)
        except ValueError:
            pass
        volume = _number(fields[6])
        items.append({"symbol": match.group(1), "name": fields[1],
                      "latest_price": _number(fields[3]), "change_pct": _number(fields[32]),
                      "volume": int(volume) if volume is not None else None,
                      "provider_discount_rate": None,
                      "market_date": stamp.date().isoformat() if stamp else None,
                      "quote_time": stamp.isoformat() if stamp else None,
                      "data_quality": ["Tencent web quote fields are undocumented; missing fields are not inferred."],
                      "source_type": "market_quote", "content_status": "summary"})
    return items


def _fetch_tencent_quotes(symbols):
    query = ",".join(("sh" if symbol.startswith("5") else "sz") + symbol for symbol in sorted(symbols))
    request = Request(QUOTE_URL + query, headers={"User-Agent": "qagent/0.1", "Referer": "https://gu.qq.com/"})
    try:
        with urlopen(request, timeout=QUOTE_TIMEOUT) as response:
            return _parse_tencent_quotes(response.read())
    except (OSError, TimeoutError, UnicodeError, URLError) as exc:
        raise ValueError(f"Tencent realtime quotes failed ({type(exc).__name__})") from None


def _fetch_tencent_daily(symbol, count=70):
    market_symbol = ("sh" if symbol.startswith("5") else "sz") + symbol
    param = urlquote(f"{market_symbol},day,,,{count},qfq", safe=",")
    request = Request(KLINE_URL + param, headers={"User-Agent": "qagent/0.1", "Referer": "https://gu.qq.com/"})
    try:
        with urlopen(request, timeout=QUOTE_TIMEOUT) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, TimeoutError, UnicodeError, ValueError, URLError) as exc:
        raise ValueError(f"Tencent daily history failed ({type(exc).__name__})") from None
    node = payload.get("data", {}).get(market_symbol, {})
    rows = node.get("qfqday") or node.get("day") or []
    result = []
    for row in rows:
        if len(row) < 6:
            continue
        try:
            day = date.fromisoformat(row[0]).isoformat()
        except (TypeError, ValueError):
            continue
        close = _number(row[2])
        if close is not None and close > 0:
            result.append((day, close))
    return result


def format_quotes_for_llm(items, requested, missing, source, fetched_at, warnings):
    """Return compact, field-labelled quote data; raw provider rows stay out of model context."""
    dates = sorted({item["market_date"] for item in items if item.get("market_date")})
    data_date = dates[-1] if dates else None
    status = "partial_success" if missing else "success"
    return {"status": status, "data_date": data_date, "data": items, "items": items,
            "requested_symbols": sorted(requested), "returned_symbols": sorted(item["symbol"] for item in items),
            "missing": sorted(missing), "source": source,
            "fetched_at": fetched_at, "warnings": warnings}


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
    items = _fetch_tencent_quotes(codes)
    found = {item["symbol"] for item in items}
    missing = sorted(codes - found)
    warnings = ["Realtime public snapshot, not exchange Level-2 data; provider availability is not guaranteed.",
                "Provider timestamps are preserved; no missing field or premium/discount value is inferred."]
    market_dates = sorted({item["market_date"] for item in items if item.get("market_date")})
    if market_dates and market_dates[-1] < _now().astimezone(MARKET_TZ).date().isoformat():
        warnings.append(f"Latest available quote date is {market_dates[-1]}; today's quote is unavailable.")
    if missing:
        warnings.append("Missing symbols returned as a batch; do not retry the same request unchanged.")
    return format_quotes_for_llm(items, codes, missing, "Tencent Finance/qt.gtimg.cn", _now().isoformat(), warnings)


def screen_etfs(limit=3):
    """Rank a fixed diversified ETF pool using completed 20/60-session momentum."""
    if type(limit) is not int or not 1 <= limit <= 5:
        raise ValueError("limit must be 1..5")
    histories, failed = {}, {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(_fetch_tencent_daily, symbol): symbol for symbol in ETF_UNIVERSE}
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                histories[symbol] = future.result()
            except ValueError as exc:
                failed[symbol] = str(exc)

    now = _now().astimezone(MARKET_TZ)
    ranked = []
    for symbol, rows in histories.items():
        if rows and rows[-1][0] == now.date().isoformat() and (now.hour, now.minute) < (15, 5):
            rows = rows[:-1]
        if len(rows) < 61:
            failed[symbol] = "fewer than 61 completed daily bars"
            continue
        closes = [row[1] for row in rows]
        return_20d = closes[-1] / closes[-21] - 1
        return_60d = closes[-1] / closes[-61] - 1
        ranked.append({"symbol": symbol, "underlying": ETF_UNIVERSE[symbol], "signal_date": rows[-1][0],
                       "return_20d": round(return_20d, 6), "return_60d": round(return_60d, 6),
                       "score": round((return_20d + return_60d) / 2, 6),
                       "eligible": return_20d > 0 and return_60d > 0,
                       "source_type": "quant_screen", "content_status": "summary"})

    signal_date = max((row["signal_date"] for row in ranked), default=None)
    for row in ranked:
        if row["signal_date"] != signal_date:
            row["eligible"] = False
            failed[row["symbol"]] = f"stale history ending {row['signal_date']}"
    ranked.sort(key=lambda row: (-row["score"], row["symbol"]))
    selected = [row for row in ranked if row["eligible"]][:limit]
    quotes = get_quotes([row["symbol"] for row in selected]) if selected else {"data": [], "missing": []}
    quote_by_symbol = {row["symbol"]: row for row in quotes["data"]}
    for rank, row in enumerate(selected, 1):
        quote = quote_by_symbol.get(row["symbol"], {})
        row.update(rank=rank, name=quote.get("name"), latest_price=quote.get("latest_price"),
                   change_pct=quote.get("change_pct"), quote_time=quote.get("quote_time"))

    missing = sorted(set(failed) | set(quotes.get("missing", [])))
    status = "no_selection" if not selected else ("partial_success" if missing else "success")
    return {"status": status, "signal_date": signal_date, "data": selected,
            "method": "fixed 10-ETF universe; score=(20-session return + 60-session return)/2; both returns must be positive",
            "universe": ETF_UNIVERSE, "evaluated": len(ranked), "missing": missing,
            "source": "Tencent Finance/qt.gtimg.cn + web.ifzq.gtimg.cn",
            "fetched_at": _now().isoformat(),
            "warnings": ["Signals use the latest completed daily bar; realtime quotes are display-only.",
                         "This is a deterministic shortlist, not an order or investment recommendation."]}


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

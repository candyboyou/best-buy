#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import queue
import re
import threading
import time
from datetime import datetime

import requests
from requests.adapters import HTTPAdapter

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

# 进程内复用的 Session（连接池 keep-alive），替代每次 fork curl 子进程
_session = None


def _get_session():
    global _session
    if _session is None:
        s = requests.Session()
        s.headers.update({"User-Agent": UA})
        adapter = HTTPAdapter(pool_connections=32, pool_maxsize=32, max_retries=0)
        s.mount("http://", adapter)
        s.mount("https://", adapter)
        _session = s
    return _session


def curl(url, headers=None, enc="utf-8", timeout=8):
    """进程内 HTTP GET，返回解码后的文本；失败返回空串。

    timeout 可为秒数（read）或 (connect, read) 元组。
    """
    if isinstance(timeout, tuple):
        connect_timeout, read_timeout = timeout
    else:
        connect_timeout, read_timeout = 5, timeout
    hdr = {"User-Agent": UA}
    if headers:
        for item in headers:
            if ":" in item:
                k, v = item.split(":", 1)
                hdr[k.strip()] = v.strip()
    try:
        r = _get_session().get(url, headers=hdr, timeout=(connect_timeout, read_timeout))
    except Exception:
        return ""
    try:
        return r.content.decode(enc, errors="replace")
    except Exception:
        return r.content.decode("utf-8", errors="replace")


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _has_price(quote):
    return bool(quote) and quote.get("price") is not None


def first_valid_quote(requests, timeout=30):
    if not requests:
        return {}

    results = queue.Queue()

    def run(fn, args):
        try:
            quote = fn(*args)
        except Exception:
            quote = {}
        results.put(quote or {})

    threads = [
        threading.Thread(target=run, args=(fn, args), daemon=True)
        for fn, args in requests
    ]
    for thread in threads:
        thread.start()

    deadline = time.monotonic() + timeout if timeout is not None else None
    fallback = {}
    remaining = len(threads)
    while remaining:
        wait = None
        if deadline is not None:
            wait = deadline - time.monotonic()
            if wait <= 0:
                break
        try:
            quote = results.get(timeout=wait)
        except queue.Empty:
            break
        remaining -= 1
        if _has_price(quote):
            return quote
        if quote and not fallback:
            fallback = quote
    return fallback


def primary_quote_with_fallback(primary, fallbacks):
    fallback = {}
    for fn, args in [primary] + list(fallbacks):
        try:
            quote = fn(*args) or {}
        except Exception:
            quote = {}
        if _has_price(quote):
            return quote
        if quote and not fallback:
            fallback = quote
    return fallback


def yahoo_chart(symbol, interval="1d", range_="3mo"):
    out = curl(
        f"https://query2.finance.yahoo.com/v8/finance/chart/{symbol}"
        f"?interval={interval}&range={range_}"
    )
    if not out:
        return None
    try:
        d = json.loads(out)
    except Exception:
        return None
    res = d.get("chart", {}).get("result")
    if not res:
        return None
    ch = res[0]
    meta = ch.get("meta", {})
    ts = ch.get("timestamp", [])
    q = ch.get("indicators", {}).get("quote", [{}])[0]
    rows = []
    for i, t in enumerate(ts):
        c = q["close"][i]
        if c is None:
            continue
        intraday = "m" in interval or "h" in interval
        rows.append(
            {
                "date": datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M")
                if intraday
                else datetime.fromtimestamp(t).strftime("%Y-%m-%d"),
                "open": round(q["open"][i], 4) if q["open"][i] else 0,
                "high": round(q["high"][i], 4) if q["high"][i] else 0,
                "low": round(q["low"][i], 4) if q["low"][i] else 0,
                "close": round(c, 4),
                "volume": int(q["volume"][i]) if q["volume"][i] else 0,
            }
        )
    return {"meta": meta, "rows": rows}


def hk_quote_tencent(code):
    # 优先简要接口 s_hk（响应小、几十 ms），失败回退完整 r_hk
    quote = _hk_quote_tencent_simple(code)
    if _has_price(quote):
        return quote
    txt = curl(f"https://qt.gtimg.cn/q=r_hk{code}", enc="gbk", timeout=5)
    m = re.search(r'"(.+)"', txt)
    if not m:
        return {}
    f = m.group(1).split("~")
    if len(f) < 50:
        return {}
    return {
        "source": "tencent",
        "name": f[1],
        "price": _f(f[3]),
        "prev_close": _f(f[4]),
        "open": _f(f[5]),
        "high": _f(f[33]),
        "low": _f(f[34]),
        "change_pct": _f(f[32]),
        "volume": _f(f[6]),
        "amount": _f(f[37]),
        "timestamp": f[30],
    }


def _hk_quote_tencent_simple(code):
    """腾讯 s_hk 简要接口：100~名称~代码~最新价~涨跌~涨跌幅~成交量~成交额"""
    txt = curl(f"https://qt.gtimg.cn/q=s_hk{code}", enc="gbk", timeout=5)
    m = re.search(r'"(.+)"', txt)
    if not m:
        return {}
    f = m.group(1).split("~")
    if len(f) < 6:
        return {}
    price = _f(f[3])
    change = _f(f[4])
    if price is None:
        return {}
    return {
        "source": "tencent",
        "name": f[1],
        "price": price,
        "prev_close": round(price - change, 4) if change is not None else None,
        "change_pct": _f(f[5]),
        "volume": _f(f[6]) if len(f) > 6 else None,
        "amount": _f(f[7]) if len(f) > 7 else None,
        "timestamp": None,
    }


def hk_quote_sina(code):
    txt = curl(
        f"https://hq.sinajs.cn/list=rt_hk{code}",
        headers=["Referer: https://finance.sina.com.cn/"],
        enc="gbk",
    )
    m = re.search(r'"(.+)"', txt)
    if not m:
        return {}
    f = m.group(1).split(",")
    if len(f) < 15:
        return {}
    return {
        "source": "sina",
        "name": f[1],
        "price": _f(f[6]),
        "prev_close": _f(f[3]),
        "open": _f(f[2]),
        "high": _f(f[4]),
        "low": _f(f[5]),
        "change_pct": _f(f[8]),
        "timestamp": None,
    }


def us_quote_tencent(ticker):
    """腾讯 s_us 简要接口：200~名称~代码~最新价~涨跌~涨跌幅~成交量~成交额"""
    txt = curl(f"https://qt.gtimg.cn/q=s_us{ticker.upper()}", enc="gbk", timeout=5)
    m = re.search(r'"(.+)"', txt)
    if not m:
        return {}
    f = m.group(1).split("~")
    if len(f) < 6:
        return {}
    price = _f(f[3])
    change = _f(f[4])
    if price is None:
        return {}
    return {
        "source": "tencent",
        "name": f[1],
        "price": price,
        "prev_close": round(price - change, 4) if change is not None else None,
        "change_pct": _f(f[5]),
        "volume": _f(f[6]) if len(f) > 6 else None,
        "timestamp": None,
    }


def us_quote_sina(ticker):
    txt = curl(
        f"https://hq.sinajs.cn/list=gb_{ticker.lower()}",
        headers=["Referer: https://finance.sina.com.cn/"],
        enc="gbk",
    )
    m = re.search(r'"(.+)"', txt)
    if not m:
        return {}
    f = m.group(1).split(",")
    if len(f) < 15:
        return {}
    return {
        "source": "sina",
        "name": f[0],
        "price": _f(f[1]),
        "change_pct": _f(f[2]),
        "prev_close": _f(f[26]),
        "open": _f(f[5]),
        "high": _f(f[6]),
        "low": _f(f[7]),
    }


def yahoo_quote(symbol):
    yc = yahoo_chart(symbol, "1d", "5d")
    if yc and yc["meta"].get("regularMarketPrice") is not None:
        meta = yc["meta"]
        return {
            "source": "yahoo",
            "price": meta.get("regularMarketPrice"),
            "prev_close": meta.get("previousClose") or meta.get("chartPreviousClose"),
            "market_state": meta.get("marketState"),
            "fifty_two_week_high": meta.get("fiftyTwoWeekHigh"),
            "fifty_two_week_low": meta.get("fiftyTwoWeekLow"),
            "currency": meta.get("currency"),
            "exchange": meta.get("exchangeName"),
            "regular_market_time": meta.get("regularMarketTime"),
        }
    return {}


def yahoo_chart_hk(code):
    code = str(code).lstrip("0") or "0"
    variants = [f"{code}.HK", f"{code.zfill(4)}.HK", f"{code.zfill(5)}.HK"]
    for variant in dict.fromkeys(variants):
        yc = yahoo_chart(variant, "1d", "3mo")
        if yc and yc["rows"]:
            return yc
    return None


def fetch_quote(symbol):
    s = symbol.upper()
    if re.match(r"^0?\d{4,5}$", s):
        code = s.zfill(5)
        return primary_quote_with_fallback(
            (hk_quote_sina, (code,)),
            [(hk_quote_tencent, (code,))],
        )
    m = re.match(r"^0?(\d{4,5})\.HK$", s)
    if m:
        code = m.group(1).zfill(5)
        return primary_quote_with_fallback(
            (hk_quote_sina, (code,)),
            [(hk_quote_tencent, (code,))],
        )
    if re.match(r"^[A-Z.]+$", s) and ".HK" not in s:
        return first_valid_quote([
            (us_quote_tencent, (s,)),
            (us_quote_sina, (s,)),
            (yahoo_quote, (symbol,)),
        ])
    return {}


def load_rows(symbol, range_="3mo"):
    s = symbol.upper()
    if re.match(r"^0?\d{4,5}$", s):
        yc = yahoo_chart_hk(s)
        if yc and range_ != "3mo":
            variant = f"{s.lstrip('0') or '0'}.HK"
            yc = yahoo_chart(variant, "1d", range_) or yc
        return yc
    return yahoo_chart(s, "1d", range_)


def now_ts():
    return time.time()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import re
from datetime import datetime
from urllib.parse import urlencode

from best_buy_app.data.market_data import UA, curl, first_valid_quote, primary_quote_with_fallback


def _float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _json(url, timeout=15):
    text = curl(url, timeout=timeout)
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {}


def normalize_symbol(symbol):
    s = str(symbol or "").strip().upper()
    if re.match(r"^0?\d{4,5}$", s):
        code = s.zfill(5)
        return {
            "input": symbol,
            "kind": "hk",
            "code": code,
            "yahoo": f"{code.lstrip('0') or '0'}.HK",
            "eastmoney_secid": f"116.{code}",
        }
    m = re.match(r"^0?(\d{4,5})\.HK$", s)
    if m:
        code = m.group(1).zfill(5)
        return {
            "input": symbol,
            "kind": "hk",
            "code": code,
            "yahoo": f"{code.lstrip('0') or '0'}.HK",
            "eastmoney_secid": f"116.{code}",
        }
    return {
        "input": symbol,
        "kind": "us",
        "code": s,
        "yahoo": s,
        "eastmoney_secid": None,
    }


def us_stock_quote_sina(ticker):
    txt = curl(
        f"https://hq.sinajs.cn/list=gb_{ticker.lower()}",
        headers=["Referer: https://finance.sina.com.cn/"],
        enc="gbk",
        timeout=10,
    )
    m = re.search(r'"(.+)"', txt)
    if not m:
        return {}
    f = m.group(1).split(",")
    if len(f) < 30:
        return {}
    return {
        "source": "sina",
        "symbol": ticker.upper(),
        "name": f[0],
        "price": _float(f[1]),
        "change_pct": _float(f[2]),
        "timestamp": f[3],
        "prev_close": _float(f[26]),
        "open": _float(f[5]),
        "high": _float(f[6]),
        "low": _float(f[7]),
        "volume": _float(f[10]),
        "high_52w": _float(f[8]),
        "low_52w": _float(f[9]),
        "market_cap": _float(f[12]),
        "eps": _float(f[13]),
        "pe": _float(f[14]),
    }


def us_stock_quote_tencent(ticker):
    txt = curl(f"https://qt.gtimg.cn/q=us{ticker.upper()}", enc="gbk", timeout=10)
    m = re.search(r'"(.+)"', txt)
    if not m:
        return {}
    f = m.group(1).split("~")
    if len(f) < 57:
        return {}
    return {
        "source": "tencent",
        "symbol": ticker.upper(),
        "name": f[1],
        "name_en": f[27],
        "price": _float(f[3]),
        "prev_close": _float(f[4]),
        "open": _float(f[5]),
        "volume": _int(f[6]),
        "high": _float(f[33]),
        "low": _float(f[34]),
        "high_52w": _float(f[35]),
        "low_52w": _float(f[36]),
        "change_pct": _float(f[32]),
        "market_cap": _float(f[44]),
        "pe": _float(f[53]),
        "pb": _float(f[56]),
        "timestamp": f[30],
    }


def hk_stock_quote_tencent(code):
    txt = curl(f"https://qt.gtimg.cn/q=r_hk{code}", enc="gbk", timeout=10)
    m = re.search(r'"(.+)"', txt)
    if not m:
        return {}
    f = m.group(1).split("~")
    if len(f) < 57:
        return {}
    return {
        "source": "tencent",
        "symbol": code,
        "name": f[1],
        "name_en": f[2],
        "price": _float(f[3]),
        "prev_close": _float(f[4]),
        "open": _float(f[5]),
        "high": _float(f[33]),
        "low": _float(f[34]),
        "volume": _int(f[6]),
        "amount": _float(f[37]),
        "change_pct": _float(f[32]),
        "pe": _float(f[39]),
        "pb": _float(f[56]),
        "high_52w": _float(f[35]),
        "low_52w": _float(f[36]),
        "market_cap": _float(f[44]),
        "timestamp": f[30],
    }


def hk_stock_quote_sina(code):
    txt = curl(
        f"https://hq.sinajs.cn/list=rt_hk{code}",
        headers=["Referer: https://finance.sina.com.cn/"],
        enc="gbk",
        timeout=10,
    )
    m = re.search(r'"(.+)"', txt)
    if not m:
        return {}
    f = m.group(1).split(",")
    if len(f) < 15:
        return {}
    return {
        "source": "sina",
        "symbol": code,
        "name_en": f[0],
        "name": f[1],
        "open": _float(f[2]),
        "prev_close": _float(f[3]),
        "high": _float(f[4]),
        "low": _float(f[5]),
        "price": _float(f[6]),
        "change": _float(f[7]),
        "change_pct": _float(f[8]),
        "volume": _float(f[12]),
        "amount": _float(f[11]),
    }


def stock_quote(symbol):
    meta = normalize_symbol(symbol)
    if meta["kind"] == "hk":
        q = primary_quote_with_fallback(
            (hk_stock_quote_sina, (meta["code"],)),
            [(hk_stock_quote_tencent, (meta["code"],))],
        )
    else:
        q = first_valid_quote([
            (us_stock_quote_sina, (meta["code"],)),
            (us_stock_quote_tencent, (meta["code"],)),
        ], timeout=10)
    if q:
        q["market"] = meta["kind"]
    return q


def stock_kline_yahoo(symbol, interval="1d", range_="6mo"):
    meta = normalize_symbol(symbol)
    params = urlencode({"interval": interval, "range": range_})
    d = _json(f"https://query2.finance.yahoo.com/v8/finance/chart/{meta['yahoo']}?{params}")
    chart = (d.get("chart", {}).get("result") or [{}])[0]
    timestamps = chart.get("timestamp") or []
    quote = (chart.get("indicators", {}).get("quote") or [{}])[0]
    rows = []
    intraday = "m" in interval or "h" in interval
    for i, ts in enumerate(timestamps):
        close = (quote.get("close") or [None])[i]
        if close is None:
            continue
        rows.append({
            "date": datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if intraday else datetime.fromtimestamp(ts).strftime("%Y-%m-%d"),
            "open": round((quote.get("open") or [0])[i] or 0, 4),
            "high": round((quote.get("high") or [0])[i] or 0, 4),
            "low": round((quote.get("low") or [0])[i] or 0, 4),
            "close": round(close, 4),
            "volume": _int((quote.get("volume") or [0])[i]) or 0,
        })
    return {
        "source": "yahoo_chart",
        "symbol": meta["yahoo"],
        "interval": interval,
        "range": range_,
        "meta": chart.get("meta", {}),
        "rows": rows,
    }


def us_stock_kline_sina(ticker, num=120):
    url = (
        "https://stock.finance.sina.com.cn/usstock/api/jsonp.php/var/US_MinKService.getDailyK"
        f"?symbol={ticker.upper()}&num={num}"
    )
    text = curl(url, headers=["Referer: https://finance.sina.com.cn/"], timeout=15)
    m = re.search(r"\((\[.+\])\)", text)
    if not m:
        return []
    try:
        items = json.loads(m.group(1))
    except json.JSONDecodeError:
        return []
    rows = []
    for item in items:
        rows.append({
            "date": item.get("d"),
            "open": _float(item.get("o")) or 0,
            "high": _float(item.get("h")) or 0,
            "low": _float(item.get("l")) or 0,
            "close": _float(item.get("c")) or 0,
            "volume": _int(item.get("v")) or 0,
        })
    return rows


def stock_kline(symbol, interval="1d", range_="6mo", limit=None):
    meta = normalize_symbol(symbol)
    if meta["kind"] == "us" and interval == "1d":
        rows = us_stock_kline_sina(meta["code"], limit or 120)
        if rows:
            return {
                "source": "sina_us_daily",
                "symbol": meta["code"],
                "interval": interval,
                "range": range_,
                "rows": rows[-limit:] if limit else rows,
            }
    data = stock_kline_yahoo(symbol, interval, range_)
    if limit:
        data["rows"] = data["rows"][-limit:]
    return data


def _ema(values, period):
    if not values:
        return []
    result = [values[0]]
    k = 2 / (period + 1)
    for v in values[1:]:
        result.append(v * k + result[-1] * (1 - k))
    return result


def calc_ma(klines, periods=None):
    periods = periods or [5, 10, 20, 60]
    closes = [k["close"] for k in klines]
    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    result = []
    for i, k in enumerate(klines):
        row = {"date": k["date"], "close": k["close"]}
        for p in periods:
            row[f"ma{p}"] = round(sum(closes[i - p + 1:i + 1]) / p, 4) if i >= p - 1 else None
        row["ema12"] = round(ema12[i], 4) if ema12 else None
        row["ema26"] = round(ema26[i], 4) if ema26 else None
        result.append(row)
    return result


def calc_macd(klines, fast=12, slow=26, signal=9):
    closes = [k["close"] for k in klines]
    ema_fast = _ema(closes, fast)
    ema_slow = _ema(closes, slow)
    dif = [round(f - s, 4) for f, s in zip(ema_fast, ema_slow)]
    dea = _ema(dif, signal)
    return [
        {
            "date": k["date"],
            "close": k["close"],
            "dif": round(dif[i], 4),
            "dea": round(dea[i], 4),
            "macd_hist": round((dif[i] - dea[i]) * 2, 4),
        }
        for i, k in enumerate(klines)
    ]


def calc_rsi(klines, periods=None):
    periods = periods or [6, 12, 24]
    closes = [k["close"] for k in klines]
    changes = [0.0] + [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(c, 0) for c in changes]
    losses = [max(-c, 0) for c in changes]
    result = []
    for i, k in enumerate(klines):
        row = {"date": k["date"], "close": k["close"]}
        for p in periods:
            if i < p:
                row[f"rsi{p}"] = None
                continue
            avg_gain = sum(gains[i - p + 1:i + 1]) / p
            avg_loss = sum(losses[i - p + 1:i + 1]) / p
            row[f"rsi{p}"] = 100.0 if avg_loss == 0 else round(100 - 100 / (1 + avg_gain / avg_loss), 2)
        result.append(row)
    return result


def calc_kdj(klines, n=9, m1=3, m2=3):
    k_val, d_val = 50.0, 50.0
    result = []
    for i, kline in enumerate(klines):
        if i < n - 1:
            result.append({"date": kline["date"], "close": kline["close"], "k": None, "d": None, "j": None})
            continue
        window = klines[i - n + 1:i + 1]
        high_n = max(w["high"] for w in window)
        low_n = min(w["low"] for w in window)
        rsv = (kline["close"] - low_n) / (high_n - low_n) * 100 if high_n != low_n else 50.0
        k_val = (1 / m1) * rsv + (1 - 1 / m1) * k_val
        d_val = (1 / m2) * k_val + (1 - 1 / m2) * d_val
        result.append({
            "date": kline["date"],
            "close": kline["close"],
            "k": round(k_val, 2),
            "d": round(d_val, 2),
            "j": round(3 * k_val - 2 * d_val, 2),
        })
    return result


def calc_boll(klines, period=20, num_std=2.0):
    closes = [k["close"] for k in klines]
    result = []
    for i, k in enumerate(klines):
        if i < period - 1:
            result.append({"date": k["date"], "close": k["close"], "upper": None, "middle": None, "lower": None, "bandwidth": None})
            continue
        window = closes[i - period + 1:i + 1]
        ma = sum(window) / period
        std = (sum((x - ma) ** 2 for x in window) / period) ** 0.5
        upper = ma + num_std * std
        lower = ma - num_std * std
        result.append({
            "date": k["date"],
            "close": k["close"],
            "upper": round(upper, 4),
            "middle": round(ma, 4),
            "lower": round(lower, 4),
            "bandwidth": round((upper - lower) / ma * 100, 2) if ma else None,
        })
    return result


def stock_search(keyword, count=10):
    params = urlencode({
        "input": keyword,
        "type": 14,
        "token": "D43BF722C8E33BDC906FB84D85E326E8",
        "count": count,
    })
    d = _json(f"https://searchapi.eastmoney.com/api/suggest/get?{params}", timeout=10)
    suggestions = d.get("QuotationCodeTable", {}).get("Data", [])
    result = []
    market_map = {"105": "NASDAQ", "106": "NYSE", "107": "US_OTHER", "116": "HK"}
    for s in suggestions:
        mkt = str(s.get("MktNum", ""))
        if mkt not in market_map:
            continue
        result.append({
            "code": s.get("Code"),
            "name": s.get("Name"),
            "mkt_num": int(mkt),
            "market_name": market_map[mkt],
            "security_type": s.get("SecurityTypeName"),
        })
    return result


def market_stock_list(market="us_nasdaq", sort_field="f3", sort_desc=True, page=1, page_size=20):
    market_map = {"us_nasdaq": "m:105", "us_nyse": "m:106", "us_etf": "m:107", "hk": "m:116"}
    params = urlencode({
        "fs": market_map.get(market, market),
        "fields": "f2,f3,f4,f5,f6,f7,f12,f14,f15,f16,f17,f18",
        "pn": page,
        "pz": page_size,
        "fid": sort_field,
        "po": 1 if sort_desc else 0,
    })
    d = _json(f"https://push2.eastmoney.com/api/qt/clist/get?{params}", timeout=15)
    data = d.get("data") or {}
    diff = data.get("diff") or []
    if isinstance(diff, dict):
        diff = list(diff.values())
    stocks = []
    for item in diff:
        stocks.append({
            "code": item.get("f12"),
            "name": item.get("f14"),
            "price": item.get("f2"),
            "change_pct": round(item["f3"] / 100, 2) if item.get("f3") is not None else None,
            "change_amount": item.get("f4"),
            "volume": item.get("f5"),
            "amount": item.get("f6"),
            "amplitude": round(item["f7"] / 100, 2) if item.get("f7") is not None else None,
            "high": item.get("f15"),
            "low": item.get("f16"),
            "open": item.get("f17"),
            "prev_close": item.get("f18"),
        })
    return {"total": data.get("total", 0), "stocks": stocks}


def intraday_swing_summary(rows, threshold_pct=0.3):
    valid = [r for r in rows or [] if r.get("close") not in (None, 0)]
    if not valid:
        return {}
    open_price = valid[0].get("open") or valid[0]["close"]
    high_row = max(valid, key=lambda r: r.get("high") or r["close"])
    low_row = min(valid, key=lambda r: r.get("low") or r["close"])
    closes = [r["close"] for r in valid]
    swings = []
    direction = 0
    start_i = 0
    extreme_i = 0
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        step_dir = 1 if diff > 0 else -1 if diff < 0 else 0
        if step_dir == 0:
            continue
        if direction == 0:
            direction = step_dir
            extreme_i = i
            continue
        if step_dir == direction:
            extreme_i = i
            continue
        start = closes[start_i]
        end = closes[extreme_i]
        pct = (end / start - 1) * 100 if start else 0
        if abs(pct) >= threshold_pct:
            swings.append({
                "from": valid[start_i]["date"],
                "to": valid[extreme_i]["date"],
                "direction": "up" if pct > 0 else "down",
                "start": round(start, 4),
                "end": round(end, 4),
                "pct": round(pct, 2),
            })
        start_i = extreme_i
        extreme_i = i
        direction = step_dir
    if start_i != extreme_i:
        start = closes[start_i]
        end = closes[extreme_i]
        pct = (end / start - 1) * 100 if start else 0
        if abs(pct) >= threshold_pct:
            swings.append({
                "from": valid[start_i]["date"],
                "to": valid[extreme_i]["date"],
                "direction": "up" if pct > 0 else "down",
                "start": round(start, 4),
                "end": round(end, 4),
                "pct": round(pct, 2),
            })
    return {
        "open": round(open_price, 4),
        "high": high_row.get("high") or high_row["close"],
        "high_time": high_row["date"],
        "high_pct_from_open": round(((high_row.get("high") or high_row["close"]) / open_price - 1) * 100, 2) if open_price else None,
        "low": low_row.get("low") or low_row["close"],
        "low_time": low_row["date"],
        "low_pct_from_open": round(((low_row.get("low") or low_row["close"]) / open_price - 1) * 100, 2) if open_price else None,
        "swing_count": len(swings),
        "swings": swings,
    }

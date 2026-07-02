#!/usr/bin/env python3
# -*- coding: utf-8 -*-

def ema(values, period):
    result = [values[0]]
    k = 2 / (period + 1)
    for v in values[1:]:
        result.append(v * k + result[-1] * (1 - k))
    return result


def ma(rows, p):
    c = [r["close"] for r in rows]
    return sum(c[-p:]) / p if len(c) >= p else None


def boll(rows, p=20, num_std=2.0):
    c = [r["close"] for r in rows]
    if len(c) < p:
        return None, None, None
    w = c[-p:]
    m = sum(w) / p
    sd = (sum((x - m) ** 2 for x in w) / p) ** 0.5
    return m + num_std * sd, m, m - num_std * sd


def rsi(rows, p=14):
    c = [r["close"] for r in rows]
    if len(c) < p + 1:
        return None
    gains = [0.0] + [max(c[i] - c[i - 1], 0) for i in range(1, len(c))]
    losses = [0.0] + [max(c[i - 1] - c[i], 0) for i in range(1, len(c))]
    ag = sum(gains[-p:]) / p
    al = sum(losses[-p:]) / p
    return 100 - 100 / (1 + ag / al) if al else 100.0


def kdj(rows, n=9, m1=3, m2=3):
    k = d = 50.0
    for i in range(len(rows)):
        if i < n - 1:
            continue
        w = rows[i - n + 1:i + 1]
        hn = max(x["high"] for x in w)
        ln = min(x["low"] for x in w)
        rsv = (rows[i]["close"] - ln) / (hn - ln) * 100 if hn != ln else 50.0
        k = rsv / m1 + k * (1 - 1 / m1)
        d = k / m2 + d * (1 - 1 / m2)
    return k, d, 3 * k - 2 * d


def macd(rows, fast=12, slow=26, signal=9):
    c = [r["close"] for r in rows]
    if len(c) < slow:
        return None, None, None, None, None
    ef = ema(c, fast)
    es = ema(c, slow)
    dif = [f - s for f, s in zip(ef, es)]
    dea = ema(dif, signal)
    return (dif[-1] - dea[-1]) * 2, dif[-1], dea[-1], dif[-2], dea[-2]


def atr(rows, p=14):
    if len(rows) < p + 1:
        return None
    true_ranges = []
    for i in range(1, len(rows)):
        high = rows[i]["high"]
        low = rows[i]["low"]
        prev_close = rows[i - 1]["close"]
        true_ranges.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    return sum(true_ranges[-p:]) / p


def vwap(rows):
    # 已知限制：标准财经 API 盘中不返回累计成交额(amount)，此处缺 amount 时用
    # typical_price=(H+L+C)/3 虚拟成交额。高脉冲标的(机构在极值点放量对倒)会
    # 使虚拟 VWAP 偏离真实机构成本线。真正修复需接入返回累计成交额的数据源。
    total_amount = 0.0
    total_volume = 0.0
    for row in rows or []:
        volume = row.get("volume") or 0
        if volume <= 0:
            continue
        amount = row.get("amount")
        if amount is None:
            typical_price = (row["high"] + row["low"] + row["close"]) / 3
            amount = typical_price * volume
        total_amount += amount
        total_volume += volume
    if not total_volume:
        return None
    return round(total_amount / total_volume, 4)


def fib_retrace(rows, lb=60):
    w = rows[-lb:]
    if len(w) < 2:
        return None, None, {}
    hi = max(r["high"] for r in w)
    lo = min(r["low"] for r in w)
    levels = {
        f"{r}": hi - (hi - lo) * v
        for r, v in [("23.6%", 0.236), ("38.2%", 0.382), ("50%", 0.5), ("61.8%", 0.618), ("78.6%", 0.786)]
    }
    return hi, lo, levels


def fib_ext(rows, lb=20):
    w = rows[-lb:]
    if len(w) < 2:
        return None, None, {}
    hi = max(r["high"] for r in w)
    lo = min(r["low"] for r in w)
    span = hi - lo
    return hi, lo, {"1.272": hi + span * 0.272, "1.618": hi + span * 0.618, "2.0": hi + span * 1.0}


def pivots(rows, n=3, kind="low"):
    key = "low" if kind == "low" else "high"
    out = []
    for i in range(n, len(rows) - n):
        if kind == "low":
            if all(rows[i][key] <= rows[i + j][key] for j in range(-n, n + 1)):
                out.append((rows[i]["date"], rows[i][key]))
        else:
            if all(rows[i][key] >= rows[i + j][key] for j in range(-n, n + 1)):
                out.append((rows[i]["date"], rows[i][key]))
    return out[-5:] if out else []


def candle_shape(rows):
    if not rows:
        return {}
    r = rows[-1]
    body = r["close"] - r["open"]
    rng = (r["high"] - r["low"]) or 1e-9
    upper = r["high"] - max(r["open"], r["close"])
    lower = min(r["open"], r["close"]) - r["low"]
    return {
        "body_pct": round(body / rng * 100, 1),
        "upper_shadow_pct": round(upper / rng * 100, 1),
        "lower_shadow_pct": round(lower / rng * 100, 1),
        "is_doji": abs(body) / rng < 0.1,
        "is_long_upper_shadow": upper / rng > 0.6 and body > 0,
        "is_long_lower_shadow": lower / rng > 0.6 and body < 0,
    }


def refresh_last_kline(rows, live, date=None):
    if not rows or live is None:
        return rows
    r = dict(rows[-1])
    r["close"] = live
    r["high"] = max(r["high"] or live, live)
    r["low"] = min(r["low"] or live, live)
    if date:
        r["date"] = date
    return rows[:-1] + [r]


def _projected_volume(current_vol, intraday_ctx):
    """盘中量能投影：将当日未走完的成交量按已交易进度推算为预期全天量。

    U 型曲线保守化：上午(progress < morning_cutoff_progress)乘以 morning_deflation，
    避免把开盘脉冲的爆发率线性外推为全天放量。返回 None 表示不投影（收盘/盘前/无 ctx）。
    """
    if not intraday_ctx or not intraday_ctx.get("is_open"):
        return None
    progress = intraday_ctx.get("progress") or 0
    if progress <= 0:
        return None
    projected = current_vol / progress
    if progress < intraday_ctx.get("morning_cutoff_progress", 0.5):
        projected *= intraday_ctx.get("morning_deflation", 0.7)
    return projected


def analyze(rows, label, intraday_ctx=None):
    if not rows:
        return {"label": label, "error": "无K线数据"}
    close = rows[-1]["close"]
    bu, bm, bl = boll(rows)
    r = rsi(rows)
    k, d, j = kdj(rows)
    hist, dif, dea, dif2, dea2 = macd(rows)
    atr14 = atr(rows, 14)
    hi, lo, fib_sup = fib_retrace(rows)
    _, _, fib_res = fib_ext(rows)
    mas = {p: ma(rows, p) for p in (5, 10, 20, 60)}
    # 追踪止损参考高点：取 60 周期（与 fib 回溯对齐）内的最高价，而非最近 6 根。
    # 避免 strong-trend 标的创出高点后震荡阴跌、高点滑出 6 根视窗导致 trailing_stop 下移。
    # 注意：滚动视窗只能延缓滑出，真正的跨调用单调非递减需持久化 highest_since_entry（TODO）。
    swing_window = rows[-60:] if len(rows) >= 60 else rows
    swing_high = max((r.get("high") for r in swing_window if r.get("high") is not None), default=None)
    # 基准均量只用 5 根已收盘的历史 bar，剔除 rows[-1]（盘中未收盘的当根），
    # 否则上午 10:00 当根 volume 极小会把 vol_5 拉低、量比虚高。
    benchmark = rows[-6:-1] if len(rows) >= 6 else rows[:-1]
    vol_5 = (sum(r.get("volume", 0) or 0 for r in benchmark) / len(benchmark)) if benchmark else 0
    current_vol = rows[-1].get("volume", 0) or 0
    vol_ratio = current_vol / vol_5 if vol_5 else 1.0
    volume_projected = None
    projected_vol = _projected_volume(current_vol, intraday_ctx)
    if projected_vol is not None:
        volume_projected = round(projected_vol, 2)
        max_ratio = intraday_ctx.get("max_ratio", 3.0)
        projected_ratio = projected_vol / vol_5 if vol_5 else 1.0
        vol_ratio = max(0.0, min(projected_ratio, max_ratio))

    supports = []
    if fib_sup:
        for name, v in fib_sup.items():
            if v < close:
                supports.append({"level": name, "price": round(v, 2), "dist_pct": round((v / close - 1) * 100, 1)})
    for p in (20, 60):
        v = mas[p]
        if v and v < close:
            supports.append({"level": f"MA{p}", "price": round(v, 2), "dist_pct": round((v / close - 1) * 100, 1)})
    if bl:
        supports.append({"level": "BOLL下轨", "price": round(bl, 2), "dist_pct": round((bl / close - 1) * 100, 1)})
    for d_, v in pivots(rows, kind="low"):
        if v < close:
            supports.append({"level": f"前低{d_}", "price": round(v, 2), "dist_pct": round((v / close - 1) * 100, 1)})
    supports.sort(key=lambda x: x["price"], reverse=True)

    resistances = []
    for p in (5, 10, 20, 60):
        v = mas[p]
        if v and v > close:
            resistances.append({"level": f"MA{p}", "price": round(v, 2), "dist_pct": round((v / close - 1) * 100, 1)})
    if hi:
        resistances.append({"level": "近端前高", "price": round(hi, 2), "dist_pct": round((hi / close - 1) * 100, 1)})
    if bu:
        resistances.append({"level": "BOLL上轨", "price": round(bu, 2), "dist_pct": round((bu / close - 1) * 100, 1)})
    if fib_res:
        for name, v in fib_res.items():
            if v > close:
                resistances.append({"level": f"Fib扩展{name}", "price": round(v, 2), "dist_pct": round((v / close - 1) * 100, 1)})
    for d_, v in pivots(rows, kind="high"):
        if v > close:
            resistances.append({"level": f"前高{d_}", "price": round(v, 2), "dist_pct": round((v / close - 1) * 100, 1)})
    resistances.sort(key=lambda x: x["price"])

    return {
        "label": label,
        "last_date": rows[-1]["date"],
        "close": close,
        "n": len(rows),
        "ma": {p: round(v, 2) if v else None for p, v in mas.items()},
        "boll": {"upper": round(bu, 2) if bu else None, "mid": round(bm, 2) if bm else None, "lower": round(bl, 2) if bl else None},
        "rsi14": round(r, 1) if r is not None else None,
        "kdj": {"k": round(k, 1), "d": round(d, 1), "j": round(j, 1), "cross": "死叉" if k < d else "金叉"},
        "macd": {
            "hist": round(hist, 3) if hist is not None else None,
            "dif": round(dif, 3) if dif is not None else None,
            "dea": round(dea, 3) if dea is not None else None,
            "bar": "红柱" if hist and hist > 0 else ("绿柱" if hist and hist < 0 else None),
            "shortening": (abs(hist) < abs((dif2 - dea2) * 2)) if (hist is not None and dif2 is not None and dea2 is not None) else None,
        },
        "atr14": round(atr14, 2) if atr14 is not None else None,
        "atr14_pct": round(atr14 / close, 4) if atr14 is not None and close else None,
        "swing_high": round(swing_high, 2) if swing_high is not None else None,
        "candle": candle_shape(rows),
        "volume_ratio": round(vol_ratio, 2),
        "volume_projected": volume_projected,
        "supports": supports,
        "resistances": resistances,
        "recent": [{"date": r["date"], "close": r["close"], "high": r["high"], "low": r["low"]} for r in rows[-6:]],
    }


def analyze_timeframes(rows_by_timeframe, label):
    analyses = {}
    for timeframe, rows in (rows_by_timeframe or {}).items():
        analysis = analyze(rows, f"{label}:{timeframe}")
        analysis["timeframe"] = timeframe
        analyses[timeframe] = analysis
    return analyses

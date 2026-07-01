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


def fib_retrace(rows, lb=20):
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


def analyze(rows, label):
    if not rows:
        return {"label": label, "error": "无K线数据"}
    close = rows[-1]["close"]
    bu, bm, bl = boll(rows)
    r = rsi(rows)
    k, d, j = kdj(rows)
    hist, dif, dea, dif2, dea2 = macd(rows)
    hi, lo, fib_sup = fib_retrace(rows)
    _, _, fib_res = fib_ext(rows)
    mas = {p: ma(rows, p) for p in (5, 10, 20, 60)}

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
        "candle": candle_shape(rows),
        "supports": supports,
        "resistances": resistances,
        "recent": [{"date": r["date"], "close": r["close"], "high": r["high"], "low": r["low"]} for r in rows[-6:]],
    }

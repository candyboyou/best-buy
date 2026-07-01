#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo


MARKETS = {
    "hk": {
        "label": "港股",
        "tz": "Asia/Hong_Kong",
        "windows": [(time(9, 30), time(12, 0)), (time(13, 0), time(16, 0))],
    },
    "us": {
        "label": "美股",
        "tz": "America/New_York",
        "windows": [(time(9, 30), time(16, 0))],
    },
    "kr": {
        "label": "韩股",
        "tz": "Asia/Seoul",
        "windows": [(time(9, 0), time(15, 30))],
    },
}

HOLIDAYS = {
    "hk": {
        date(2026, 1, 1),
        date(2026, 2, 17),
        date(2026, 2, 18),
        date(2026, 4, 3),
        date(2026, 4, 6),
        date(2026, 4, 7),
        date(2026, 5, 1),
        date(2026, 5, 25),
        date(2026, 6, 19),
        date(2026, 7, 1),
        date(2026, 9, 26),
        date(2026, 10, 1),
        date(2026, 10, 19),
        date(2026, 12, 25),
    },
    "us": {
        date(2026, 1, 1),
        date(2026, 1, 19),
        date(2026, 2, 16),
        date(2026, 4, 3),
        date(2026, 5, 25),
        date(2026, 6, 19),
        date(2026, 7, 3),
        date(2026, 9, 7),
        date(2026, 11, 26),
        date(2026, 12, 25),
    },
    "kr": {
        date(2026, 1, 1),
        date(2026, 2, 16),
        date(2026, 2, 17),
        date(2026, 2, 18),
        date(2026, 3, 2),
        date(2026, 5, 5),
        date(2026, 5, 25),
        date(2026, 8, 17),
        date(2026, 9, 24),
        date(2026, 9, 25),
        date(2026, 10, 5),
        date(2026, 10, 9),
        date(2026, 12, 25),
        date(2026, 12, 31),
    },
}

EARLY_CLOSES = {
    "hk": {
        date(2026, 12, 24): [(time(9, 30), time(12, 0))],
        date(2026, 12, 31): [(time(9, 30), time(12, 0))],
    },
    "us": {
        date(2026, 11, 27): [(time(9, 30), time(13, 0))],
        date(2026, 12, 24): [(time(9, 30), time(13, 0))],
    },
    "kr": {},
}


def market_for_symbol(symbol):
    s = str(symbol or "").strip().upper()
    if not s:
        return None
    if s.endswith((".KS", ".KQ")) or s in {"^KS11", "KOSPI", ".KOSPI"}:
        return "kr"
    if s.endswith(".HK") or _is_hk_code(s):
        return "hk"
    return "us"


def markets_for_symbols(symbols):
    markets = []
    seen = set()
    for symbol in symbols or []:
        market = market_for_symbol(symbol)
        if market and market not in seen:
            markets.append(market)
            seen.add(market)
    return markets


def market_status(markets, now=None):
    now = now or datetime.now().astimezone()
    active_markets = [m for m in (markets or []) if m in MARKETS]
    if not active_markets:
        active_markets = ["hk", "us", "kr"]

    open_markets = [m for m in active_markets if is_market_open(m, now)]
    next_open = min(next_market_open(m, now) for m in active_markets)
    return {
        "is_open": bool(open_markets),
        "open_markets": open_markets,
        "closed_markets": [m for m in active_markets if m not in open_markets],
        "next_open_at": next_open,
        "next_open_seconds": max(0, int((next_open - now).total_seconds())),
    }


def market_labels(markets):
    return [MARKETS[m]["label"] for m in markets or [] if m in MARKETS]


def describe_status(status):
    if status.get("is_open"):
        return "开盘市场: " + ", ".join(market_labels(status.get("open_markets")))
    next_open = status.get("next_open_at")
    next_text = next_open.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z") if next_open else "--"
    return "全部休市，下次开盘: " + next_text


def is_market_open(market, now=None):
    cfg = MARKETS.get(market)
    if not cfg:
        return False
    local_now = (now or datetime.now().astimezone()).astimezone(ZoneInfo(cfg["tz"]))
    if not is_trading_day(market, local_now.date()):
        return False
    current = local_now.time()
    return any(start <= current < end for start, end in trading_windows(market, local_now.date()))


def next_market_open(market, now=None):
    cfg = MARKETS[market]
    tz = ZoneInfo(cfg["tz"])
    local_now = (now or datetime.now().astimezone()).astimezone(tz)
    for offset in range(8):
        day = local_now.date() + timedelta(days=offset)
        if not is_trading_day(market, day):
            continue
        for start, _end in trading_windows(market, day):
            candidate = datetime.combine(day, start, tzinfo=tz)
            if candidate > local_now:
                return candidate.astimezone(local_now.tzinfo)
    return (local_now + timedelta(days=1)).astimezone(local_now.tzinfo)


def is_trading_day(market, day):
    if market not in MARKETS:
        return False
    if day.weekday() >= 5:
        return False
    return day not in HOLIDAYS.get(market, set())


def trading_windows(market, day):
    return EARLY_CLOSES.get(market, {}).get(day) or MARKETS[market]["windows"]


def apply_calendar_overrides(cfg):
    calendars = (cfg or {}).get("market_calendars", {})
    for market, data in calendars.items():
        if market not in MARKETS or not isinstance(data, dict):
            continue
        holidays = _parse_dates(data.get("holidays"))
        if holidays:
            HOLIDAYS.setdefault(market, set()).update(holidays)
        open_days = _parse_dates(data.get("open_days"))
        if open_days:
            HOLIDAYS.setdefault(market, set()).difference_update(open_days)
        for day_text, windows in (data.get("early_closes") or {}).items():
            day = _parse_date(day_text)
            parsed_windows = _parse_windows(windows)
            if day and parsed_windows:
                EARLY_CLOSES.setdefault(market, {})[day] = parsed_windows


def _parse_dates(items):
    dates = set()
    for item in items or []:
        day = _parse_date(item)
        if day:
            dates.add(day)
    return dates


def _parse_date(value):
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _parse_windows(items):
    windows = []
    for item in items or []:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            continue
        start = _parse_time(item[0])
        end = _parse_time(item[1])
        if start and end and start < end:
            windows.append((start, end))
    return windows


def _parse_time(value):
    try:
        return time.fromisoformat(str(value))
    except ValueError:
        return None


def _is_hk_code(symbol):
    code = symbol.removesuffix(".HK")
    return code.isdigit() and 4 <= len(code) <= 5

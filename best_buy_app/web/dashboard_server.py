#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import json
import mimetypes
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from copy import deepcopy
from datetime import datetime
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

from best_buy_app.core.config import load_config
from best_buy_app.core.decision_engine import (
    attach_relative_strength,
    buy_decision,
    buy_decision_mtfa,
    classify_zone,
    confirmation_score,
    final_action,
    sell_decision,
    short_term_plan,
    trade_plan,
)
from best_buy_app.core.indicators import analyze, analyze_timeframes, refresh_last_kline, vwap
from best_buy_app.core.output_utils import ts_now
from best_buy_app.data.global_stock_data import calc_boll, calc_kdj, calc_ma, calc_macd, calc_rsi, intraday_swing_summary, stock_kline, stock_quote, stock_search
from best_buy_app.data.market_data import fetch_quote, load_rows
from best_buy_app.data import storage
from best_buy_app.data.news_aggregator import compact_news_for_ai, fetch_news


ROOT = Path(__file__).resolve().parents[2]
DASHBOARD_DIR = ROOT / "dashboard"
AI_INSTRUCTIONS = """你是一位深耕全球半导体与存储芯片板块（HK/US/KR）的顶级量化交易员兼首席操盘策略师。
请结合实时流入的监控上下文，以极度专业、凝练、具有强行动导向的语气直接回答交易员的问题。

【核心量化指标解读单调性铁律】
上下文中包含了系统手写规则引擎计算出的最新指标切片，你必须将其视作唯一的交易底色：
1. `buy_score` / `sell_score`: 代表左侧指标超买超卖与形态的打分（0-6分）。
2. `confirm_score`: 全球趋势环境确认分。≥3 说明韩股（海力士/三星）与美股多头共振，大环境顺风，阻力变薄。
3. `zone` & `action`: 系统最终锁定的盘口区间与动作结论（如：🟢买点区、🟡支撑观察、🟢预动量、可分批试探等）。
4. `short_entries` / `short_exits`: 精确的近端回踩买点与分批阻力卖点。
5. `short_stop`: 动态风控止损线。这是结合纸面仓位（Paper Trading）与持仓最高价（position_highest）单调非递减维护的终极防线，一旦破位将无条件止损。

你必须熟练运用量化交易黑话（如：“右侧动量确立”、“左侧超卖接刀”、“多周期共振破位”、“锁盈防守线上移”等）来包装这些数据，给出精确具体的操盘路径，绝不能给出与系统 Action 矛盾的废话。

【回答与输出排版规范】
- 严禁废话：拒绝复述用户问题，拒绝机械地逐条罗列原始 JSON 字段。直接切中买卖要害。
- 必须使用 Markdown 结构化排版，关键价位用 **加粗** 强调。
- 谈及买入时，需检查量比（`volume_ratio`）警惕缩量假突破；谈及风险时，必须明确给出系统的止损防守价（`short_stop`）。

【输出排版模版】
### 📊 芯片板块盘口定调：[一句话说清当前标的多空状态与核心区间]
- **操盘核心策略**：[例如：多周期共振回踩，支撑位左侧分批吸纳 / 动量已扩展，谨慎追高]
- **量化引擎支撑**：盘口区间为 **{zone}**，最终动作 **{action}**（买入分 **{buy_score}** / 确认分 **{confirm_score}**）。[一两句硬核技术面/全球联动面点评]
- **精细化操盘路线图**：
  - 📍 理想买位：**[提取 short_entries 中的关键价位]**
  - 🛑 铁律防守：**{short_stop}** [触及此线纸面持仓将自动无条件清仓卖出]
  - 🎯 分批止盈：**[提取 resistances 或 short_exits 中的前两个目标阻力价]**"""

TOOL_DECIDER = """你要决定用户问题是否需要调用本地工具（global_stock_data 或 news_aggregator）进行上下文补充。

可用工具 1: global_stock_data
- quote: 实时行情，含 open/high/low/price/change_pct/volume。
- kline: K线时序数据，支持 1d/5m/15m 等。
- indicators: MA/MACD/RSI/KDJ/布林带。
- intraday_swings: 基于分钟 K 线统计盘中波段与多波震荡幅度。

可用工具 2: news_aggregator
- sources: finance, international, tech, ai, wallstreetcn, weibo 等。
- keyword: 过滤关键词（若用户问 AI 新闻可用 "AI"，自动扩展）。
- limit: 限制条数（1-20）。

只返回纯净的单行 JSON，绝对不要包含任何多余的 Markdown 标记、反引号、或自然语言解释。

【意图判断红线】
1. 问行情变动、价格、最高最低、分时走势、盘中几波涨跌、技术指标线、消息面利好利空时，use_tool=true。
2. 只问持仓止损怎么放、当前能不能买，且当前默认监控上下文已经足够完整，use_tool=false。
3. 问“盘中几波涨跌/每波百分比”时，data 必须包含 "intraday_swings"，interval 固定用 "5m"，range 固定用 "5d"。
4. 问宏观消息、利好利空、行业资讯时，news_aggregator.use_tool=true。

输出格式示例：
{"global_stock_data": {"use_tool": true, "symbols": ["MU"], "data": ["quote", "kline", "intraday_swings"], "interval": "5m", "range": "5d", "reason": "用户询问昨天盘中波段"}, "news_aggregator": {"use_tool": false}}"""


def _parse_int(value, default, maximum=None):
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    if maximum is not None and n > maximum:
        return maximum
    return max(n, 1)


def session_rules(cfg, session):
    strategy = cfg.get("strategy", {})
    if session == "preopen":
        return {
            "rsi_buy": strategy.get("rsi_buy", 30) + 2,
            "rsi_sell": strategy.get("rsi_sell", 70) - 2,
            "buy_ma_tolerance": strategy.get("buy_ma_tolerance", 0.02) + 0.005,
            "sell_boll_tolerance": strategy.get("sell_boll_tolerance", 0.98) - 0.005,
        }
    return {
        "rsi_buy": strategy.get("rsi_buy", 30),
        "rsi_sell": strategy.get("rsi_sell", 70),
        "buy_ma_tolerance": strategy.get("buy_ma_tolerance", 0.02),
        "sell_boll_tolerance": strategy.get("sell_boll_tolerance", 0.98),
    }


def current_session():
    hour = datetime.now().hour
    if hour < 9:
        return "preopen"
    if hour < 16:
        return "intraday"
    return "postclose"


def effective_config(cfg):
    session = current_session()
    result = json.loads(json.dumps(cfg or {}))
    result.setdefault("strategy", {}).update(session_rules(cfg or {}, session))
    return session, result


def normalize_search_symbol(item):
    code = str(item.get("code") or "").strip()
    market = item.get("market_name")
    if market == "HK" and code:
        return code.zfill(5)
    return code.upper()


def load_symbol_snapshot(symbol, range_="3mo"):
    watch_cfg = DashboardHandler.cfg.get("watch", {}) if DashboardHandler.cfg else {}
    rows_ttl = watch_cfg.get("rows_refresh_seconds", 600)
    quote_ttl = watch_cfg.get("quote_refresh_seconds", 15)
    rows_data = storage.load_rows_cached(DashboardHandler.cfg, symbol, range_, ttl_seconds=rows_ttl)
    if not rows_data or not rows_data.get("rows"):
        return None
    quote = storage.fetch_quote_cached(DashboardHandler.cfg, symbol, ttl_seconds=quote_ttl)
    rows = rows_data["rows"]
    if quote and quote.get("price") is not None:
        rows = refresh_last_kline(rows, quote["price"])
    analysis = analyze(rows, symbol)
    if quote and quote.get("price") is not None:
        analysis["live_price"] = quote["price"]
        if quote.get("change_pct") is not None:
            analysis["change_pct"] = quote["change_pct"]
        if quote.get("timestamp"):
            analysis["live_timestamp"] = quote["timestamp"]
    return {"symbol": symbol, "quote": quote, "rows": rows, "analysis": analysis}


# 短 TTL 内存缓存：避免每 4s 前端轮询都重拉 yahoo intraday K线 / 重算 peer 快照
_mem_cache = {}
_mem_cache_lock = threading.Lock()


def _ttl_cache_get(namespace, key, range_=None):
    ck = (namespace, key, range_)
    item = _mem_cache.get(ck)
    if item and (time.time() - item[0]) < item[2]:
        return item[1]
    return None


def _ttl_cache_set(namespace, key, value, ttl, range_=None):
    ck = (namespace, key, range_)
    with _mem_cache_lock:
        # 顺手清理过期项，避免无限膨胀
        if len(_mem_cache) > 256:
            now = time.time()
            for k in list(_mem_cache.keys()):
                v = _mem_cache[k]
                if (now - v[0]) >= v[2]:
                    _mem_cache.pop(k, None)
        _mem_cache[ck] = (time.time(), value, ttl)


def load_intraday_analysis(symbol):
    cached = _ttl_cache_get("intraday_analysis", symbol)
    if cached is not None:
        return cached
    rows_by_timeframe = {}
    for timeframe in ("60m", "15m"):
        try:
            data = stock_kline(symbol, interval=timeframe, range_="5d", limit=120)
        except Exception:
            data = None
        rows = (data or {}).get("rows") or []
        if rows:
            rows_by_timeframe[timeframe] = rows
    result = analyze_timeframes(rows_by_timeframe, symbol) if rows_by_timeframe else {}
    _ttl_cache_set("intraday_analysis", symbol, result, ttl=20)
    return result


def load_intraday_vwap(symbol):
    cached = _ttl_cache_get("intraday_vwap", symbol)
    if cached is not None:
        return cached
    try:
        data = stock_kline(symbol, interval="5m", range_="1d", limit=96)
    except Exception:
        return None
    rows = (data or {}).get("rows") or []
    value = vwap(rows)
    result = {"vwap": value, "rows": len(rows)} if value is not None else None
    _ttl_cache_set("intraday_vwap", symbol, result, ttl=20)
    return result


def load_related_snapshots(symbols, range_="3mo", limit=8):
    targets = (symbols or [])[:limit]
    if not targets:
        return []
    cached = _ttl_cache_get("related_snapshots", tuple(targets), range_)
    if cached is not None:
        return cached

    def _safe_snap(sym):
        try:
            return load_symbol_snapshot(sym, range_)
        except Exception:
            return None

    snapshots = []
    with ThreadPoolExecutor(max_workers=min(8, len(targets))) as pool:
        for snap in pool.map(_safe_snap, targets):
            if snap:
                snapshots.append(snap)
    _ttl_cache_set("related_snapshots", tuple(targets), snapshots, ttl=8, range_=range_)
    return snapshots


def build_stock_feed(symbol, cfg, history=None, range_="3mo"):
    session, cfg = effective_config(cfg)
    main_snapshot = load_symbol_snapshot(symbol, range_)
    if not main_snapshot:
        return {"error": f"无法获取 {symbol} 的K线数据", "symbol": symbol}

    defaults = cfg.get("defaults", {})
    peer_symbols = [s for s in defaults.get("peers", []) if s != symbol]
    market_symbols = defaults.get("market", [])
    peer_snapshots = load_related_snapshots(peer_symbols, range_, limit=8)
    market_snapshot = load_symbol_snapshot(market_symbols[0], range_) if market_symbols else None

    analysis = main_snapshot["analysis"]
    intraday_analyses = load_intraday_analysis(symbol) if cfg.get("strategy", {}).get("mtfa", {}).get("enabled", True) else {}
    intraday_vwap = load_intraday_vwap(symbol)
    if intraday_vwap:
        analysis = dict(analysis)
        analysis["intraday"] = intraday_vwap
    sentiment = storage.get_latest_sentiment(cfg, symbol)
    if sentiment and cfg.get("strategy", {}).get("sentiment_alpha", {}).get("enabled", True):
        analysis = dict(analysis)
        analysis["sentiment"] = sentiment
    peer_analyses = [item["analysis"] for item in peer_snapshots]
    market_analysis = market_snapshot["analysis"] if market_snapshot else None
    sell = sell_decision(analysis, cfg)
    confirm = confirmation_score(analysis, peer_analyses, market_analysis, cfg)
    analysis = attach_relative_strength(analysis, confirm)
    mtfa_analyses = {"1d": analysis}
    mtfa_analyses.update(intraday_analyses)
    buy = buy_decision_mtfa(mtfa_analyses, cfg) if intraday_analyses else buy_decision(analysis, cfg)
    # 持仓状态只读传入：dashboard 不自动开平仓，仅用持仓峰值锚定吊灯止损展示。
    pos = storage.get_position(cfg, symbol)
    position_highest = None
    live_price = analysis.get("close")
    if pos and live_price is not None:
        pos = storage.update_position_peak(cfg, symbol, live_price) or pos
        position_highest = pos.get("highest_since_entry")
    plan = trade_plan(analysis, confirm, cfg, position_highest=position_highest)
    short_plan = short_term_plan(analysis, cfg)
    plan["short_term"] = short_plan
    price = analysis.get("close")
    zone = classify_zone(analysis, price, buy, sell)
    action = final_action(buy, sell, confirm)
    ts = datetime.now().strftime("%H:%M:%S")
    quote = main_snapshot.get("quote") or {}
    next_history = (deepcopy(history or []) + [{
        "time": ts,
        "symbol": symbol,
        "price": price,
        "zone": zone,
        "action": action,
        "buy": buy["verdict"],
        "sell": sell["verdict"],
        "confirm": confirm["verdict"],
        "quote_source": quote.get("source"),
        "quote_timestamp": quote.get("timestamp"),
    }])[-5:]

    return {
        "time": ts_now(),
        "session": session,
        "symbol": symbol,
        "price": price,
        "quote": {
            "source": quote.get("source"),
            "timestamp": quote.get("timestamp"),
        },
        "position": {
            "symbol": pos.get("symbol") if pos else None,
            "entry_price": pos.get("entry_price") if pos else None,
            "highest_since_entry": pos.get("highest_since_entry") if pos else None,
            "opened_at": pos.get("opened_at") if pos else None,
            "opened_at_text": pos.get("opened_at_text") if pos else None,
        } if pos else None,
        "zone": zone,
        "action": action,
        "confirm_score": confirm["score"],
        "buy_score": buy["score"],
        "sell_score": sell["score"],
        "momentum": {"signals": [], "score": 0, "pct": None, "verdict": "未启用多标的盘中动量", "active": False},
        "premomentum": {"signals": [], "score": 0, "main_pct": None, "group_scores": {}, "verdict": "未启用多标的预动量", "active": False},
        "alerts": [],
        "main": {
            "buy": buy,
            "sell": sell,
            "confirm": confirm,
            "buy_score": buy["score"],
            "sell_score": sell["score"],
            "confirm_score": confirm["score"],
            "ma20": analysis.get("ma", {}).get(20),
            "supports": analysis.get("supports", []),
            "resistances": analysis.get("resistances", []),
            "analyses": mtfa_analyses,
            "intraday": analysis.get("intraday"),
            "sentiment": analysis.get("sentiment"),
            "short_term": short_plan,
            "plan": plan,
            "position": {
                "symbol": pos.get("symbol") if pos else None,
                "entry_price": pos.get("entry_price") if pos else None,
                "highest_since_entry": pos.get("highest_since_entry") if pos else None,
                "opened_at": pos.get("opened_at") if pos else None,
                "opened_at_text": pos.get("opened_at_text") if pos else None,
            } if pos else None,
            "market": {
                "symbol": market_snapshot["symbol"],
                "label": market_analysis.get("label"),
                "close": market_analysis.get("close"),
                "rsi14": market_analysis.get("rsi14"),
                "ma20": market_analysis.get("ma", {}).get(20),
            } if market_snapshot and market_analysis else None,
            "peers": [
                {
                    "symbol": item["symbol"],
                    "label": item["analysis"].get("label"),
                    "close": item["analysis"].get("close"),
                    "rsi14": item["analysis"].get("rsi14"),
                    "ma20": item["analysis"].get("ma", {}).get(20),
                }
                for item in peer_snapshots
            ],
            "analysis": analysis,
        },
        "history": next_history,
    }


def json_response(handler, status, payload, cache=False):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    if not cache:
        handler.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        handler.send_header("Pragma", "no-cache")
        handler.send_header("Expires", "0")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    try:
        handler.wfile.write(body)
    except (BrokenPipeError, ConnectionResetError):
        # 客户端在响应写完前已关闭连接（如轮询被取消、页面刷新），属正常情况，忽略即可
        pass


def compact_levels(levels, limit=4):
    parts = []
    for item in (levels or [])[:limit]:
        dist = item.get("dist_pct")
        dist_text = f"({dist:+.1f}%)" if isinstance(dist, (int, float)) else ""
        parts.append(f"{item.get('level')}={item.get('price')}{dist_text}")
    return " | ".join(parts) if parts else "暂无"


def compact_ai_context(feed):
    main = feed.get("main", {}) if feed else {}
    plan = main.get("plan", {})
    short_term = main.get("short_term") or plan.get("short_term") or {}
    return {
        "symbol": feed.get("symbol"),
        "price": feed.get("price"),
        "zone": feed.get("zone"),
        "action": feed.get("action"),
        "buy": (main.get("buy") or {}).get("verdict"),
        "sell": (main.get("sell") or {}).get("verdict"),
        "confirm": (main.get("confirm") or {}).get("verdict"),
        "confirm_score": feed.get("confirm_score"),
        "premomentum": (main.get("premomentum") or feed.get("premomentum") or {}).get("verdict"),
        "momentum": (main.get("momentum") or feed.get("momentum") or {}).get("verdict"),
        "short_entries": short_term.get("entries"),
        "short_exits": short_term.get("exits"),
        "short_stop": short_term.get("stop_loss"),
        "supports": (main.get("supports") or [])[:3],
        "resistances": (main.get("resistances") or [])[:3],
        "peers": (main.get("peers") or [])[:5],
        "market": main.get("market"),
    }


def _uniq(items):
    result = []
    seen = set()
    for item in items:
        if item and item not in seen:
            result.append(item)
            seen.add(item)
    return result


def extract_stock_symbols(message, feed):
    text = message or ""
    candidates = re.findall(r"\b[A-Z]{1,5}(?:\.[A-Z]{1,3})?\b", text.upper())
    candidates += re.findall(r"\b0?\d{4,5}(?:\.HK)?\b", text.upper())
    current = str((feed or {}).get("symbol") or "").strip()
    if current:
        candidates.append(current)
    main = (feed or {}).get("main") or {}
    for peer in (main.get("peers") or [])[:3]:
        sym = str(peer.get("symbol") or "").strip()
        if sym and sym in text:
            candidates.append(sym)
    market = main.get("market") or {}
    if market.get("symbol") and str(market.get("symbol")) in text:
        candidates.append(str(market.get("symbol")))
    return _uniq(candidates)[:4]


def stock_data_intent(message):
    text = message or ""
    data_keywords = [
        "行情", "价格", "涨跌", "涨幅", "跌幅", "最高", "最低", "开盘", "收盘",
        "K线", "k线", "分时", "盘中", "昨天", "今日", "今天", "技术指标",
        "MACD", "RSI", "KDJ", "布林", "均线", "MA20", "财报", "资金流",
    ]
    if "$global-stock-data" in text:
        return True
    if any(k in text for k in data_keywords):
        return True
    return bool(re.search(r"\b[A-Z]{1,5}\b", text) and re.search(r"quote|price|kline|chart|high|low", text, re.I))


def news_intent(message):
    text = message or ""
    keywords = [
        "新闻", "资讯", "消息", "消息面", "热点", "热搜", "早报", "日报", "简报",
        "国际新闻", "财经新闻", "科技新闻", "AI新闻", "AI 新闻", "GitHub趋势",
        "Hacker News", "微博", "Reuters", "BBC", "华尔街见闻", "利好", "利空",
    ]
    return "$news-aggregator-skill" in text or any(k.lower() in text.lower() for k in keywords)


def default_news_plan(message):
    if not news_intent(message):
        return None
    text = message or ""
    lower = text.lower()
    if "微博" in text or "热搜" in text:
        sources = ["weibo"]
    elif "github" in lower or "开源" in text:
        sources = ["github_trending"]
    elif "hacker news" in lower or "hn" in lower:
        sources = ["hackernews"]
    elif "ai" in lower or "人工智能" in text or "大模型" in text:
        sources = ["ai"]
    elif "财经" in text or "金融" in text or "美股" in text or "利好" in text or "利空" in text:
        sources = ["finance"]
    elif "国际" in text or "reuters" in lower or "bbc" in lower:
        sources = ["international"]
    elif "科技" in text:
        sources = ["tech"]
    else:
        sources = ["international"]
    keyword = None
    quoted = re.findall(r"[\"“](.+?)[\"”]", text)
    if quoted:
        keyword = quoted[0]
    elif "AI" in text or "ai" in lower or "人工智能" in text:
        keyword = "AI"
    limit = 10
    m = re.search(r"(\d+)\s*(条|个|篇)", text)
    if m:
        limit = int(m.group(1))
    return {
        "use_tool": True,
        "sources": sources,
        "keyword": keyword,
        "limit": min(max(limit, 1), 20),
        "reason": "本地规则命中新闻查询问题",
    }


def sanitize_news_plan(plan, message):
    if not isinstance(plan, dict) or not plan.get("use_tool"):
        return None
    sources = plan.get("sources") or []
    if isinstance(sources, str):
        sources = [s.strip() for s in sources.split(",") if s.strip()]
    sources = [str(s).strip() for s in sources if str(s).strip()]
    if not sources:
        fallback = default_news_plan(message)
        sources = (fallback or {}).get("sources") or ["international"]
    limit = plan.get("limit")
    if not isinstance(limit, int):
        limit = 10
    keyword = plan.get("keyword")
    return {
        "use_tool": True,
        "sources": sources[:6],
        "keyword": str(keyword).strip() if keyword else None,
        "limit": min(max(limit, 1), 20),
        "reason": str(plan.get("reason") or ""),
    }


def choose_kline_request(message):
    text = message or ""
    if any(k in text for k in ("分时", "盘中", "几波", "每次涨跌", "每波", "波段")):
        return {"interval": "5m", "range": "5d", "limit": 96, "include_swings": True}
    return {"interval": "1d", "range": "6mo", "limit": 80, "include_swings": False}


def latest_indicator_snapshot(rows):
    if not rows:
        return {}
    ma = calc_ma(rows)
    return {
        "ma": ma[-1] if ma else None,
        "macd": (calc_macd(rows) or [None])[-1],
        "rsi": (calc_rsi(rows) or [None])[-1],
        "kdj": (calc_kdj(rows) or [None])[-1],
        "boll": (calc_boll(rows) or [None])[-1],
    }


def default_global_stock_plan(message, feed):
    if not stock_data_intent(message):
        return None
    symbols = extract_stock_symbols(message, feed)
    if not symbols:
        return None
    kline_req = choose_kline_request(message)
    data = ["quote", "kline"]
    if kline_req["include_swings"]:
        data.append("intraday_swings")
    else:
        data.append("indicators")
    return {
        "use_tool": True,
        "symbols": symbols,
        "data": data,
        "interval": kline_req["interval"],
        "range": kline_req["range"],
        "limit": kline_req["limit"],
        "reason": "本地规则命中股票数据问题",
    }


def sanitize_global_stock_plan(plan, message, feed):
    if not isinstance(plan, dict) or not plan.get("use_tool"):
        return None
    symbols = _uniq([str(s).strip() for s in plan.get("symbols") or [] if str(s).strip()])
    if not symbols:
        symbols = extract_stock_symbols(message, feed)
    if not symbols:
        return None
    data = plan.get("data") or []
    if isinstance(data, str):
        data = [data]
    data = [str(item).strip() for item in data if str(item).strip()]
    if not data:
        data = ["quote", "kline"]
    interval = str(plan.get("interval") or "1d")
    range_ = str(plan.get("range") or "6mo")
    if "intraday_swings" in data and interval == "1d":
        interval = "5m"
        range_ = "5d"
    limit = plan.get("limit")
    if not isinstance(limit, int):
        limit = 96 if interval != "1d" else 80
    return {
        "use_tool": True,
        "symbols": symbols[:4],
        "data": data,
        "interval": interval,
        "range": range_,
        "limit": min(max(limit, 1), 240),
        "reason": str(plan.get("reason") or ""),
    }


def build_global_stock_context(message, feed, tool_plan=None):
    plan = sanitize_global_stock_plan(tool_plan, message, feed) if tool_plan is not None else default_global_stock_plan(message, feed)
    if not plan:
        return None
    data_needs = set(plan["data"])
    result = {
        "skill": "global-stock-data",
        "triggered": True,
        "reason": plan.get("reason") or "AI 决定调用本地股票数据工具",
        "tool_plan": plan,
        "symbols": {},
        "errors": [],
    }
    for symbol in plan["symbols"]:
        item = {}
        if "quote" in data_needs:
            try:
                quote = stock_quote(symbol)
                if quote:
                    item["quote"] = quote
            except Exception as exc:
                result["errors"].append(f"{symbol} quote: {exc}")
        if data_needs.intersection({"kline", "indicators", "intraday_swings"}):
            try:
                kline = stock_kline(
                    symbol,
                    interval=plan["interval"],
                    range_=plan["range"],
                    limit=plan["limit"],
                )
                rows = kline.get("rows") or []
                item["kline"] = {
                    "source": kline.get("source"),
                    "symbol": kline.get("symbol"),
                    "interval": kline.get("interval"),
                    "range": kline.get("range"),
                    "row_count": len(rows),
                    "rows": rows[-30:],
                }
                if "intraday_swings" in data_needs:
                    item["intraday_swing_summary"] = intraday_swing_summary(rows)
                if "indicators" in data_needs and len(rows) >= 26:
                    item["indicators"] = latest_indicator_snapshot(rows)
            except Exception as exc:
                result["errors"].append(f"{symbol} kline: {exc}")
        if item:
            result["symbols"][symbol] = item
    return result if result["symbols"] or result["errors"] else None


def enrich_feed_for_ai(message, feed, tool_plan=None):
    context = compact_ai_context(feed)
    stock_plan = (tool_plan or {}).get("global_stock_data") if isinstance(tool_plan, dict) else tool_plan
    news_plan = (tool_plan or {}).get("news_aggregator") if isinstance(tool_plan, dict) else None
    stock_context = build_global_stock_context(message, feed, stock_plan)
    if stock_context:
        context["global_stock_data"] = stock_context
    news_context = build_news_context(message, news_plan)
    if news_context:
        context["news_aggregator"] = news_context
    return context


def build_news_context(message, tool_plan=None):
    plan = sanitize_news_plan(tool_plan, message) if tool_plan is not None else default_news_plan(message)
    if not plan:
        return None
    try:
        news_data = fetch_news(plan["sources"], limit=plan["limit"], keyword=plan["keyword"])
        compact = compact_news_for_ai(news_data)
        compact["triggered"] = True
        compact["tool_plan"] = plan
        compact["reason"] = plan.get("reason") or "AI 决定调用本地新闻聚合工具"
        return compact
    except Exception as exc:
        return {
            "skill": "news-aggregator-skill",
            "triggered": True,
            "tool_plan": plan,
            "items": [],
            "errors": [str(exc)],
        }


def local_reply(message, feed):
    main = feed.get("main", {}) if feed else {}
    buy = main.get("buy", {})
    sell = main.get("sell", {})
    confirm = main.get("confirm", {})
    analysis = main.get("analysis", {})
    supports = main.get("supports") or analysis.get("supports", [])
    resistances = main.get("resistances") or analysis.get("resistances", [])
    lines = [
        "AI 接口暂时不可用，以下是本地监控数据摘要：",
        f"动作：{feed.get('action')}。",
        f"买入：{buy.get('verdict', '暂无')}；卖出：{sell.get('verdict', '暂无')}；确认：{confirm.get('verdict', '暂无')}({feed.get('confirm_score')})。",
        f"短线参考：支撑 {compact_levels(supports, 2)}；阻力 {compact_levels(resistances, 2)}。",
    ]
    return "\n".join(lines)


def call_external_ai(cfg, message, feed, history):
    request_data = build_ai_request(cfg, message, feed, history, stream=False)
    if not request_data:
        return None
    provider = request_data["provider"]
    endpoint = request_data["endpoint"]
    headers = request_data["headers"]
    payload = request_data["payload"]
    model = request_data["model"]
    if provider == "openai_responses":
        data = post_responses_with_fallback(endpoint, headers, payload, model, message)
    else:
        data = post_ai_json(endpoint, headers, payload)
    if provider == "openai_responses":
        return parse_responses_text(data)
    choices = data.get("choices") if isinstance(data, dict) else None
    if choices:
        first = choices[0]
        if isinstance(first.get("message"), dict):
            return first["message"].get("content")
        return first.get("text")
    return data.get("reply") or data.get("message") or data.get("content")


def build_ai_request(cfg, message, feed, history, stream=False):
    ai_cfg = cfg.get("ai", {})
    endpoint = ai_cfg.get("endpoint") or os.environ.get("BEST_BUY_AI_ENDPOINT")
    api_key = ai_cfg.get("api_key") or os.environ.get("BEST_BUY_AI_API_KEY")
    provider = ai_cfg.get("provider", "generic")
    model = ai_cfg.get("model") or os.environ.get("BEST_BUY_AI_MODEL")
    if not endpoint:
        return None
    if provider == "openai_compatible" and endpoint.rstrip("/").endswith("/v1"):
        endpoint = endpoint.rstrip("/") + "/chat/completions"
    if provider == "openai_responses" and endpoint.rstrip("/").endswith("/v1"):
        endpoint = endpoint.rstrip("/") + "/responses"
    tool_plan = decide_tools(endpoint, api_key, provider, model, message, feed) if provider in ("openai_responses", "openai_compatible") else None

    if provider == "openai_responses":
        context = json.dumps(enrich_feed_for_ai(message, feed, tool_plan), ensure_ascii=False)
        payload = {
            "input": build_responses_input(message, context),
        }
        if model:
            payload["model"] = model
        if stream:
            payload["stream"] = True
    elif provider == "openai_compatible":
        context = json.dumps(enrich_feed_for_ai(message, feed, tool_plan), ensure_ascii=False)
        messages = [
            {
                "role": "system",
                "content": AI_INSTRUCTIONS,
            },
            {"role": "user", "content": f"当前监控数据：{context}\n\n用户问题：{message}"},
        ]
        payload = {"messages": messages}
        if model:
            payload["model"] = model
        if stream:
            payload["stream"] = True
    else:
        payload = {
            "message": message,
            "context": feed,
            "history": history[-12:] if isinstance(history, list) else [],
        }
        if stream:
            payload["stream"] = True

    headers = {
        "Accept": "text/event-stream" if stream else "application/json",
        "Content-Type": "application/json",
        "User-Agent": "best-buy-dashboard/1.0",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return {
        "endpoint": endpoint,
        "headers": headers,
        "payload": payload,
        "provider": provider,
        "model": model,
    }


def default_tool_plan(message, feed):
    return {
        "global_stock_data": default_global_stock_plan(message, feed),
        "news_aggregator": default_news_plan(message),
    }


def sanitize_tool_plan(plan, message, feed):
    if not isinstance(plan, dict):
        return default_tool_plan(message, feed)
    if "use_tool" in plan:
        plan = {"global_stock_data": plan}
    return {
        "global_stock_data": sanitize_global_stock_plan(plan.get("global_stock_data"), message, feed),
        "news_aggregator": sanitize_news_plan(plan.get("news_aggregator"), message),
    }


def decide_tools(endpoint, api_key, provider, model, message, feed):
    baseline = compact_ai_context(feed)
    decider_input = (
        f"{TOOL_DECIDER}\n\n"
        f"当前监控上下文：{json.dumps(baseline, ensure_ascii=False)}\n\n"
        f"用户问题：{message}"
    )
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "best-buy-dashboard/1.0",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        if provider == "openai_responses":
            payload = {"input": decider_input}
            if model:
                payload["model"] = model
            data = post_ai_json(endpoint, headers, payload)
            text = parse_responses_text(data)
        elif provider == "openai_compatible":
            payload = {
                "messages": [
                    {"role": "system", "content": "你只返回 JSON。"},
                    {"role": "user", "content": decider_input},
                ]
            }
            if model:
                payload["model"] = model
            data = post_ai_json(endpoint, headers, payload)
            choices = data.get("choices") if isinstance(data, dict) else None
            text = choices[0].get("message", {}).get("content") if choices else None
        else:
            return default_tool_plan(message, feed)
        parsed = parse_json_object(text)
        sanitized = sanitize_tool_plan(parsed, message, feed)
        fallback = default_tool_plan(message, feed)
        if not sanitized.get("global_stock_data"):
            sanitized["global_stock_data"] = fallback.get("global_stock_data")
        if not sanitized.get("news_aggregator"):
            sanitized["news_aggregator"] = fallback.get("news_aggregator")
        return sanitized
    except Exception:
        return default_tool_plan(message, feed)


def parse_json_object(text):
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def post_ai_json(endpoint, headers, payload):
    req = Request(endpoint, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers=headers, method="POST")
    return post_json(req, payload)


def post_responses_with_fallback(endpoint, headers, payload, model, message):
    attempts = [
        ("带监控上下文", payload),
        ("最小请求", minimal_responses_payload(model, message)),
    ]
    errors = []
    for label, attempt in attempts:
        try:
            data = post_ai_json(endpoint, headers, attempt)
            if parse_responses_text(data):
                return data
            errors.append(f"{label}: 响应中没有 output_text")
        except Exception as exc:
            errors.append(f"{label}: {exc}")
    raise RuntimeError("；".join(errors))

def minimal_responses_payload(model, message):
    payload = {
        "input": message,
    }
    if model:
        payload["model"] = model
    return payload


def build_responses_input(message, context):
    return f"{AI_INSTRUCTIONS}\n\n当前监控数据：{context}\n\n用户问题：{message}"


def parse_responses_text(data):
    if not isinstance(data, dict):
        return None
    if data.get("output_text"):
        return data["output_text"]
    texts = []
    for item in data.get("output", []) or []:
        for content in item.get("content", []) or []:
            if content.get("text"):
                texts.append(content["text"])
    return "\n".join(texts) if texts else None


def parse_stream_text_delta(provider, data):
    if not isinstance(data, dict):
        return None
    if provider == "openai_responses":
        if data.get("type") in ("response.output_text.delta", "response.refusal.delta"):
            return data.get("delta")
        if data.get("type") in ("response.completed", "response.output_text.done"):
            return None
    choices = data.get("choices") if isinstance(data.get("choices"), list) else []
    if choices:
        first = choices[0]
        delta = first.get("delta") or {}
        if isinstance(delta, dict) and delta.get("content"):
            return delta["content"]
        if isinstance(first.get("message"), dict):
            return first["message"].get("content")
        return first.get("text")
    return data.get("reply") or data.get("message") or data.get("content")


def post_json(req, payload):
    try:
        with urlopen(req, timeout=30) as res:
            return json.loads(res.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        if exc.code in (307, 308) and exc.headers.get("Location"):
            redirected = Request(
                exc.headers["Location"],
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers=dict(req.header_items()),
                method="POST",
            )
            with urlopen(redirected, timeout=30) as res:
                return json.loads(res.read().decode("utf-8"))
        raise RuntimeError(f"HTTP {exc.code}: {body or exc.reason}") from exc


def iter_external_ai_stream(cfg, message, feed, history):
    request_data = build_ai_request(cfg, message, feed, history, stream=True)
    if not request_data:
        return None
    return read_ai_stream(
        request_data["endpoint"],
        request_data["headers"],
        request_data["payload"],
        request_data["provider"],
    )


def read_ai_stream(endpoint, headers, payload, provider):
    req = Request(endpoint, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers=headers, method="POST")
    try:
        with urlopen(req, timeout=60) as res:
            content_type = res.headers.get("Content-Type", "")
            if "text/event-stream" not in content_type.lower():
                data = json.loads(res.read().decode("utf-8"))
                text = parse_responses_text(data) if provider == "openai_responses" else parse_stream_text_delta(provider, data)
                if text:
                    yield text
                return
            yield from parse_sse_response(res, provider)
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        if exc.code in (307, 308) and exc.headers.get("Location"):
            yield from read_ai_stream(exc.headers["Location"], headers, payload, provider)
            return
        raise RuntimeError(f"HTTP {exc.code}: {body or exc.reason}") from exc


def parse_sse_response(response, provider):
    data_lines = []
    for raw_line in response:
        line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
        if not line:
            text = parse_sse_data(data_lines, provider)
            data_lines = []
            if text:
                yield text
            continue
        if line.startswith(":"):
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    text = parse_sse_data(data_lines, provider)
    if text:
        yield text


def parse_sse_data(data_lines, provider):
    if not data_lines:
        return None
    raw = "\n".join(data_lines)
    if raw == "[DONE]":
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    return parse_stream_text_delta(provider, data)


def sse_response_start(handler):
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
    handler.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
    handler.send_header("Pragma", "no-cache")
    handler.send_header("Expires", "0")
    handler.send_header("X-Accel-Buffering", "no")
    handler.end_headers()


class SseClientDisconnected(Exception):
    pass


def write_sse(handler, event, payload):
    body = json.dumps(payload, ensure_ascii=False)
    try:
        handler.wfile.write(f"event: {event}\n".encode("utf-8"))
        for line in body.splitlines() or [""]:
            handler.wfile.write(f"data: {line}\n".encode("utf-8"))
        handler.wfile.write(b"\n")
        handler.wfile.flush()
    except (BrokenPipeError, ConnectionResetError) as exc:
        raise SseClientDisconnected() from exc


class DashboardHandler(BaseHTTPRequestHandler):
    cfg = None
    symbol_histories = {}
    stock_feed_cache = {}
    stock_feed_lock = threading.Lock()
    stock_feed_inflight = {}

    def log_message(self, fmt, *args):
        return

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path in ("/", "/dashboard", "/dashboard/"):
            return self.serve_file(DASHBOARD_DIR / "index.html")
        if path == "/best_buy_feed.json":
            feed = storage.get_latest_feed(self.cfg) or {}
            return json_response(self, 200, feed)
        if path == "/api/watchlist":
            return json_response(self, 200, {"items": storage.list_watchlist(self.cfg)})
        if path == "/api/history":
            params = parse_qs(parsed.query)
            symbol = (params.get("symbol") or [""])[0].strip().upper()
            limit = _parse_int((params.get("limit") or ["100"])[0], 100, 1000)
            if not symbol:
                return json_response(self, 400, {"error": "symbol is required"})
            return json_response(self, 200, {"items": storage.list_watch_ticks(self.cfg, symbol, limit=limit)})
        if path == "/api/events":
            params = parse_qs(parsed.query)
            symbol = (params.get("symbol") or [""])[0].strip().upper()
            limit = _parse_int((params.get("limit") or ["100"])[0], 100, 1000)
            if not symbol:
                return json_response(self, 400, {"error": "symbol is required"})
            return json_response(self, 200, {"items": storage.list_events(self.cfg, symbol, limit=limit)})
        if path == "/api/stocks/search":
            params = parse_qs(parsed.query)
            keyword = (params.get("q") or [""])[0].strip()
            if len(keyword) < 1:
                return json_response(self, 200, {"items": []})
            try:
                raw_items = stock_search(keyword, count=12)
                items = []
                seen = set()
                for item in raw_items:
                    symbol = normalize_search_symbol(item)
                    if not symbol or symbol in seen:
                        continue
                    seen.add(symbol)
                    items.append({
                        "symbol": symbol,
                        "code": item.get("code"),
                        "name": item.get("name"),
                        "market_name": item.get("market_name"),
                        "security_type": item.get("security_type"),
                    })
                return json_response(self, 200, {"items": items})
            except Exception as exc:
                return json_response(self, 500, {"error": str(exc), "items": []})
        if path == "/api/stocks/feed":
            params = parse_qs(parsed.query)
            symbol = (params.get("symbol") or [""])[0].strip().upper()
            range_ = (params.get("range") or ["3mo"])[0].strip() or "3mo"
            if not symbol:
                return json_response(self, 400, {"error": "symbol is required"})
            cache_key = (symbol, range_)
            now = time.time()
            ttl = self.cfg.get("watch", {}).get("quote_refresh_seconds", 15)
            with self.stock_feed_lock:
                cached = self.stock_feed_cache.get(cache_key)
                if cached and now - cached["at"] < ttl:
                    feed = cached["feed"]
                    event = None
                    owner = False
                else:
                    event = self.stock_feed_inflight.get(cache_key)
                    if event:
                        owner = False
                    else:
                        event = threading.Event()
                        self.stock_feed_inflight[cache_key] = event
                        owner = True
                    feed = None
            if feed is None and owner:
                try:
                    history = self.symbol_histories.get(symbol, [])
                    feed = build_stock_feed(symbol, self.cfg, history=history, range_=range_)
                    if feed.get("error"):
                        cached_latest = storage.get_latest_feed(self.cfg, symbol)
                        if cached_latest:
                            feed = cached_latest
                    with self.stock_feed_lock:
                        self.stock_feed_cache[cache_key] = {"at": time.time(), "feed": feed}
                finally:
                    with self.stock_feed_lock:
                        self.stock_feed_inflight.pop(cache_key, None)
                        event.set()
            elif feed is None:
                event.wait(timeout=ttl)
                with self.stock_feed_lock:
                    cached = self.stock_feed_cache.get(cache_key)
                    feed = cached["feed"] if cached else {"error": f"{symbol} 数据请求仍在处理中", "symbol": symbol}
            if feed.get("history"):
                self.symbol_histories[symbol] = feed["history"]
            status = 500 if feed.get("error") else 200
            return json_response(self, status, feed)
        if path.startswith("/dashboard/"):
            rel = path.removeprefix("/dashboard/")
            return self.serve_file(DASHBOARD_DIR / rel)
        return self.serve_file(DASHBOARD_DIR / path.lstrip("/"))

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/chat/stream":
            return self.handle_chat_stream()
        if path == "/api/chat":
            return self.handle_chat_json()
        if path == "/api/watchlist":
            return self.handle_watchlist_add()
        return json_response(self, 404, {"error": "not found"})

    def do_DELETE(self):
        path = urlparse(self.path).path
        if path == "/api/watchlist":
            return self.handle_watchlist_remove()
        return json_response(self, 404, {"error": "not found"})

    def _read_json_body(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def handle_watchlist_add(self):
        try:
            payload = self._read_json_body()
            symbol = str(payload.get("symbol") or "").strip()
            if not symbol:
                return json_response(self, 400, {"error": "symbol is required"})
            label = str(payload.get("label") or "").strip() or None
            note = str(payload.get("note") or "").strip() or None
            item = storage.add_watch(self.cfg, symbol, label=label, note=note)
            return json_response(self, 200, {"item": item, "items": storage.list_watchlist(self.cfg)})
        except Exception as exc:
            return json_response(self, 500, {"error": str(exc)})

    def handle_watchlist_remove(self):
        try:
            params = parse_qs(urlparse(self.path).query)
            symbol = (params.get("symbol") or [""])[0].strip()
            if not symbol:
                return json_response(self, 400, {"error": "symbol is required"})
            storage.remove_watch(self.cfg, symbol)
            return json_response(self, 200, {"items": storage.list_watchlist(self.cfg)})
        except Exception as exc:
            return json_response(self, 500, {"error": str(exc)})

    def read_chat_payload(self):
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        message = str(payload.get("message", "")).strip()
        history = payload.get("history", [])
        if not message:
            return None, None, None, "message is required"
        context = payload.get("context")
        feed = context if isinstance(context, dict) and context.get("symbol") else (storage.get_latest_feed(self.cfg) or {})
        return message, history, feed, None

    def handle_chat_json(self):
        try:
            message, history, feed, error = self.read_chat_payload()
            if error:
                return json_response(self, 400, {"error": error})
            try:
                reply = call_external_ai(self.cfg, message, feed, history)
                if reply:
                    return json_response(self, 200, {"reply": reply})
                return json_response(self, 200, {"reply": local_reply(message, feed), "ai_error": "未配置外部 AI endpoint"})
            except Exception as exc:
                return json_response(self, 200, {"reply": local_reply(message, feed), "ai_error": str(exc)})
        except Exception as exc:
            return json_response(self, 500, {"error": str(exc)})

    def handle_chat_stream(self):
        try:
            message, history, feed, error = self.read_chat_payload()
        except Exception as exc:
            return json_response(self, 500, {"error": str(exc)})
        if error:
            return json_response(self, 400, {"error": error})
        sse_response_start(self)
        try:
            write_sse(self, "start", {"ok": True})
            chunks = iter_external_ai_stream(self.cfg, message, feed, history)
            if chunks is None:
                reply = local_reply(message, feed)
                write_sse(self, "delta", {"text": reply})
                write_sse(self, "done", {"reply": reply, "ai_error": "未配置外部 AI endpoint"})
                return
            reply_parts = []
            for chunk in chunks:
                if not chunk:
                    continue
                reply_parts.append(chunk)
                write_sse(self, "delta", {"text": chunk})
            reply = "".join(reply_parts)
            if not reply:
                reply = local_reply(message, feed)
                write_sse(self, "delta", {"text": reply})
                write_sse(self, "done", {"reply": reply, "ai_error": "AI 没有返回内容"})
                return
            write_sse(self, "done", {"reply": reply})
        except SseClientDisconnected:
            return
        except Exception as exc:
            try:
                reply = local_reply(message, feed)
                write_sse(self, "delta", {"text": reply})
                write_sse(self, "error", {"message": str(exc), "reply": reply})
                write_sse(self, "done", {"reply": reply, "ai_error": str(exc)})
            except SseClientDisconnected:
                return

    def serve_file(self, path):
        path = Path(path).resolve()
        if not str(path).startswith(str(DASHBOARD_DIR.resolve())) or not path.exists() or not path.is_file():
            return json_response(self, 404, {"error": "not found"})
        content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    parser = argparse.ArgumentParser(description="best-buy dashboard server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--config", default="config.json")
    args = parser.parse_args()

    cfg = load_config(args.config)
    storage.init_db(cfg)
    DashboardHandler.cfg = cfg
    server = ThreadingHTTPServer((args.host, args.port), DashboardHandler)
    print(f"dashboard: http://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()

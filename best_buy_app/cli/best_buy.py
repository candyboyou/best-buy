#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import json
import os
import re
import sys
import time
from copy import deepcopy
from datetime import datetime
from tempfile import NamedTemporaryFile
from urllib.request import Request, urlopen

from best_buy_app.core.config import load_config
from best_buy_app.core.decision_engine import (
    attach_relative_strength,
    buy_decision,
    classify_zone,
    confirmation_score,
    final_action,
    final_action_with_momentum,
    fmt_num,
    momentum_decision,
    premomentum_decision,
    render_report,
    render_watch_tick,
    sell_decision,
    short_term_plan,
    trade_plan,
)
from best_buy_app.core.indicators import analyze, refresh_last_kline
from best_buy_app.core.market_hours import apply_calendar_overrides, describe_status, intraday_progress, market_for_symbol, market_labels, market_status, markets_for_symbols
from best_buy_app.core.output_utils import CYAN, DIM, MAGENTA, RED, colorize, log_line, simple_table, supports_color, ts_now
from best_buy_app.data import storage
from best_buy_app.data.market_data import fetch_quote, load_rows, now_ts


def parse_leveraged(spec):
    if not spec:
        return None
    ratio = 2
    s = spec
    if "@ratio=" in s:
        s, rstr = s.split("@ratio=")
        ratio = float(rstr)
    parts = s.split(":")
    sym = parts[0]
    source = parts[1] if len(parts) > 1 else "auto"
    return {"symbol": sym, "source": source, "ratio": ratio}


def build_intraday_ctx(market, vol_proj_cfg=None):
    """根据盘中进度构建量能投影上下文。

    午休(paused)期间 progress 冻结在已走完时段，仍返回可投影 ctx，避免量比断崖式跳变。
    """
    vol_proj_cfg = vol_proj_cfg or {}
    if not vol_proj_cfg.get("enabled", True) or not market:
        return None
    prog = intraday_progress(market)
    if not (prog["is_open"] or prog.get("paused")):
        return None
    return {
        "is_open": True,
        "paused": prog.get("paused", False),
        "progress": prog["progress"],
        "elapsed_minutes": prog["elapsed_minutes"],
        "total_minutes": prog["total_minutes"],
        "morning_cutoff_progress": vol_proj_cfg.get("morning_cutoff_progress", 0.5),
        "morning_deflation": vol_proj_cfg.get("morning_deflation", 0.7),
        "max_ratio": vol_proj_cfg.get("max_ratio", 3.0),
    }


def load_snapshot(symbol, range_="3mo", rows=None, quote=None, intraday_ctx=None):
    if rows is None:
        yc = load_rows(symbol, range_)
        rows = yc["rows"] if yc and yc.get("rows") else None
    if not rows:
        return None
    if quote is None:
        quote = fetch_quote(symbol)
    if quote and quote.get("price") is not None:
        rows = refresh_last_kline(rows, quote["price"])
    analysis = analyze(rows, symbol, intraday_ctx=intraday_ctx)
    if quote and quote.get("price") is not None:
        analysis["live_price"] = quote["price"]
        if quote.get("change_pct") is not None:
            analysis["change_pct"] = quote["change_pct"]
        if quote.get("timestamp"):
            analysis["live_timestamp"] = quote["timestamp"]
    return {"symbol": symbol, "quote": quote, "rows": rows, "analysis": analysis}


def build_peer_snapshots(symbols, range_):
    snaps = []
    for sym in symbols:
        snap = load_snapshot(sym, range_)
        if snap:
            snaps.append(snap)
    return snaps


class SnapshotCache:
    def __init__(self, rows_ttl, quote_ttl, cfg=None):
        self.rows_ttl = rows_ttl
        self.quote_ttl = quote_ttl
        self.cfg = cfg
        self._cache = {}

    def _fetch_rows(self, symbol, range_):
        if self.cfg is not None:
            data = storage.load_rows_cached(self.cfg, symbol, range_, ttl_seconds=self.rows_ttl)
            if data:
                return data["rows"]
            return None
        yc = load_rows(symbol, range_)
        return yc["rows"] if yc and yc.get("rows") else None

    def _fetch_quote(self, symbol):
        if self.cfg is not None:
            return storage.fetch_quote_cached(self.cfg, symbol, ttl_seconds=self.quote_ttl)
        return fetch_quote(symbol)

    def get(self, symbol, range_="3mo", force_rows=False, force_quote=False, intraday_ctx=None):
        key = (symbol, range_)
        item = self._cache.get(key, {})
        now = now_ts()
        rows_ok = item.get("snapshot") and item.get("rows_at") and now - item["rows_at"] < self.rows_ttl
        quote_ok = item.get("quote") is not None and item.get("quote_at") and now - item["quote_at"] < self.quote_ttl
        if force_rows or not rows_ok:
            rows = self._fetch_rows(symbol, range_)
            quote = self._fetch_quote(symbol)
            snap = load_snapshot(symbol, range_, rows=rows, quote=quote, intraday_ctx=intraday_ctx)
            if snap:
                item["snapshot"] = snap
                item["rows_at"] = now
                item["quote"] = snap["quote"]
                item["quote_at"] = now
        elif force_quote or not quote_ok:
            quote = self._fetch_quote(symbol)
            if item.get("snapshot") and quote:
                rows = item["snapshot"]["rows"]
                if quote.get("price") is not None:
                    rows = refresh_last_kline(rows, quote["price"])
                analysis = analyze(rows, symbol, intraday_ctx=intraday_ctx)
                if quote.get("price") is not None:
                    analysis["live_price"] = quote["price"]
                    if quote.get("timestamp"):
                        analysis["live_timestamp"] = quote["timestamp"]
                item["snapshot"] = {"symbol": symbol, "quote": quote, "rows": rows, "analysis": analysis}
                item["quote"] = quote
                item["quote_at"] = now
        self._cache[key] = item
        return item.get("snapshot")


def current_session(cfg):
    hour = datetime.now().hour
    if hour < 9:
        return "preopen"
    if hour < 16:
        return "intraday"
    return "postclose"


def session_groups(cfg, session):
    groups = cfg.get("defaults", {}).get("market_groups", {})
    return groups.get(session, [])


def session_rules(cfg, session):
    strategy = cfg.get("strategy", {})
    if session == "preopen":
        return {
            "rsi_buy": strategy.get("rsi_buy", 30) + 2,
            "rsi_sell": strategy.get("rsi_sell", 70) - 2,
            "buy_ma_tolerance": strategy.get("buy_ma_tolerance", 0.02) + 0.005,
            "sell_boll_tolerance": strategy.get("sell_boll_tolerance", 0.98) - 0.005,
        }
    if session == "postclose":
        return {
            "rsi_buy": strategy.get("rsi_buy", 30),
            "rsi_sell": strategy.get("rsi_sell", 70),
            "buy_ma_tolerance": strategy.get("buy_ma_tolerance", 0.02),
            "sell_boll_tolerance": strategy.get("sell_boll_tolerance", 0.98),
        }
    return {
        "rsi_buy": strategy.get("rsi_buy", 30),
        "rsi_sell": strategy.get("rsi_sell", 70),
        "buy_ma_tolerance": strategy.get("buy_ma_tolerance", 0.02),
        "sell_boll_tolerance": strategy.get("sell_boll_tolerance", 0.98),
    }


def push_webhook(url, payload):
    if not url:
        return False
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=5) as resp:
            return 200 <= getattr(resp, "status", 200) < 300
    except Exception:
        return False


def write_json_atomic(path, payload):
    if not path:
        return
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=folder or None) as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        tmp = f.name
    os.replace(tmp, path)


def watch_mode(args, cfg):
    apply_calendar_overrides(cfg)
    interval = max(5, args.interval)
    leverage_spec = parse_leveraged(args.leveraged)
    has_lev = bool(leverage_spec)
    default_peers = cfg.get("defaults", {}).get("peers", [])
    default_market = cfg.get("defaults", {}).get("market", [])
    session = current_session(cfg)
    effective_cfg = json.loads(json.dumps(cfg))
    effective_cfg["strategy"].update(session_rules(cfg, session))
    session_market = session_groups(cfg, session)
    peer_symbols = [s.strip() for s in (args.peers or ",".join(default_peers)).split(",") if s.strip()]
    market_symbols = [s.strip() for s in (args.market or ",".join(default_market + session_market)).split(",") if s.strip()]
    if args.index and args.index not in market_symbols:
        market_symbols = [args.index] + market_symbols
    watch_cfg = cfg.get("watch", {})
    watched_markets = markets_for_symbols([args.symbol] + peer_symbols + market_symbols + ([leverage_spec["symbol"]] if leverage_spec else []))
    auto_market_hours = watch_cfg.get("market_hours_enabled", True) and not args.ignore_market_hours

    cache = SnapshotCache(
        rows_ttl=watch_cfg.get("rows_refresh_seconds", 600),
        quote_ttl=watch_cfg.get("quote_refresh_seconds", 10),
        cfg=cfg,
    )
    peer_cache = SnapshotCache(
        rows_ttl=watch_cfg.get("peer_rows_refresh_seconds", 900),
        quote_ttl=watch_cfg.get("peer_quote_refresh_seconds", 60),
        cfg=cfg,
    )
    market_cache = SnapshotCache(
        rows_ttl=watch_cfg.get("rows_refresh_seconds", 600),
        quote_ttl=watch_cfg.get("market_quote_refresh_seconds", 60),
        cfg=cfg,
    )

    main_market = market_for_symbol(args.symbol)
    vol_proj_cfg = effective_cfg.get("strategy", {}).get("volume_projection", {})

    def _intraday_ctx_for(market):
        return build_intraday_ctx(market, vol_proj_cfg)

    main_snapshot = cache.get(args.symbol, args.range, force_rows=True, intraday_ctx=_intraday_ctx_for(main_market))
    if not main_snapshot:
        print(f"错误：无法获取 {args.symbol} 的K线数据", file=sys.stderr)
        sys.exit(1)

    lev_snapshot = cache.get(leverage_spec["symbol"], args.range, force_rows=True) if has_lev else None
    peer_snapshots = [peer_cache.get(sym, args.range, force_rows=True) for sym in peer_symbols]
    peer_snapshots = [s for s in peer_snapshots if s]
    market_snapshot = market_cache.get(market_symbols[0], args.range, force_rows=True) if market_symbols else None

    primary_sym = leverage_spec["symbol"] if lev_snapshot else args.symbol
    confirm_sym = args.symbol if lev_snapshot else None

    color = supports_color()
    print("=" * 88)
    print(colorize(f"best-buy 监控模式  |  session={session}  |  每 {interval}s 一次  |  Ctrl+C 退出", CYAN, color))
    print(colorize("交易时段: " + ", ".join(market_labels(watched_markets)) + ("（自动暂停休市轮询）" if auto_market_hours else "（忽略休市）"), DIM, color))
    print(colorize(f"主交易对象: {primary_sym}" + (f"   确认: {confirm_sym}" if confirm_sym else ""), MAGENTA, color))
    if peer_snapshots:
        print(colorize("确认对象: " + ", ".join(s["symbol"] for s in peer_snapshots), DIM, color))
    if market_snapshot:
        print(colorize(f"环境确认: {market_snapshot['symbol']}", DIM, color))
    print("=" * 88)

    prev = {}
    price_history = []
    peer_price_histories = {}
    market_price_history = []
    tick = 0
    try:
        while True:
            if auto_market_hours:
                status = market_status(watched_markets)
                if not status["is_open"]:
                    sleep_seconds = min(max(status["next_open_seconds"], interval), 3600)
                    print(colorize(f"{describe_status(status)}，暂停 {sleep_seconds}s", DIM, color))
                    time.sleep(sleep_seconds)
                    continue

            today = datetime.now().date()
            if prev.get("last_date") and today != prev["last_date"]:
                # 跨交易日：清空分时 Tick 队列，防止 Rebound/VWAP 把昨天切片算进今天
                price_history.clear()
                peer_price_histories.clear()
                market_price_history.clear()
                print(colorize(f"跨交易日 {prev['last_date']}→{today}，已重置分时队列", DIM, color))

            main_snapshot = cache.get(args.symbol, args.range, intraday_ctx=_intraday_ctx_for(main_market)) or main_snapshot
            if has_lev:
                lev_snapshot = cache.get(leverage_spec["symbol"], args.range) or lev_snapshot
            peer_snapshots = [peer_cache.get(sym, args.range) or snap for sym, snap in zip(peer_symbols, peer_snapshots + [None] * max(0, len(peer_symbols) - len(peer_snapshots)))]
            peer_snapshots = [s for s in peer_snapshots if s]
            if market_symbols:
                market_snapshot = market_cache.get(market_symbols[0], args.range) or market_snapshot

            main_analysis = main_snapshot["analysis"]
            lev_analysis = lev_snapshot["analysis"] if lev_snapshot else None
            primary_analysis = lev_analysis or main_analysis
            peer_analyses = [snap["analysis"] for snap in peer_snapshots]
            market_analysis = market_snapshot["analysis"] if market_snapshot else None

            sell = sell_decision(main_analysis, effective_cfg)
            confirm = confirmation_score(main_analysis, peer_analyses, market_analysis, effective_cfg)
            main_analysis = attach_relative_strength(main_analysis, confirm)
            buy = buy_decision(main_analysis, effective_cfg)
            # 持仓状态：已持仓则刷新持仓以来最高价（单调非递减），传入 trade_plan 锚定吊灯止损。
            # 未持仓时 position_highest=None，禁止用历史 swing_high 算伪吊灯止损踩踏开仓。
            position_highest = None
            pos = storage.get_position(cfg, primary_sym)
            if pos and lp is not None:
                pos = storage.update_position_peak(cfg, primary_sym, lp) or pos
                position_highest = pos.get("highest_since_entry")
            plan = trade_plan(main_analysis, confirm, effective_cfg, position_highest=position_highest)
            short_plan = short_term_plan(main_analysis, effective_cfg)

            lp = primary_analysis.get("close")
            tick += 1
            ts = datetime.now().strftime("%H:%M:%S")
            max_hist = effective_cfg.get("strategy", {}).get("momentum", {}).get("lookback_ticks", 20) * 3
            if lp is not None:
                price_history.append({"time": ts, "price": lp})
                price_history = price_history[-max_hist:]
            for snap in peer_snapshots:
                pa = snap["analysis"]
                if pa.get("close") is None:
                    continue
                hist = peer_price_histories.setdefault(snap["symbol"], {"symbol": snap["symbol"], "label": pa.get("label"), "history": []})
                hist["history"].append({"time": ts, "price": pa.get("close")})
                hist["history"] = hist["history"][-max_hist:]
            if market_snapshot and market_analysis and market_analysis.get("close") is not None:
                market_price_history.append({"time": ts, "price": market_analysis.get("close")})
                market_price_history = market_price_history[-max_hist:]
            premomentum = premomentum_decision(
                price_history,
                list(peer_price_histories.values()),
                {"symbol": market_snapshot["symbol"], "label": market_analysis.get("label"), "history": market_price_history} if market_snapshot and market_analysis else None,
                effective_cfg,
            )
            momentum = momentum_decision(price_history, main_analysis, confirm, sell, effective_cfg)
            plan["momentum"] = momentum
            plan["premomentum"] = premomentum
            plan["short_term"] = short_plan
            if premomentum.get("active") and buy.get("score", 0) < 2:
                buy = dict(buy)
                buy["verdict"] = premomentum["verdict"]
                buy["signals"] = premomentum["signals"] + buy.get("signals", [])
                buy["score"] = max(buy.get("score", 0), 2)
            if momentum.get("active") and buy.get("score", 0) < 2:
                buy = dict(buy)
                buy["verdict"] = momentum["verdict"]
                buy["signals"] = momentum["signals"] + buy.get("signals", [])
                buy["score"] = max(buy.get("score", 0), 2)
            action = final_action_with_momentum(buy, sell, confirm, momentum, premomentum)

            # 纸面仓位（Paper Trading）：信号触发即虚拟开仓并追踪持仓峰值，破止损或强卖出则平仓。
            # 持仓状态只用于锚定吊灯止损，不接真实下单；外部实盘可用 storage.open_position(force=True) 覆写。
            paper_cfg = effective_cfg.get("strategy", {}).get("paper_trading", {})
            if paper_cfg.get("enabled", True) and lp is not None:
                stop_loss = plan.get("stop_loss")
                if pos:
                    should_close = (
                        (paper_cfg.get("close_on_stop_loss", True) and stop_loss is not None and lp <= stop_loss)
                        or sell.get("score", 0) >= paper_cfg.get("close_sell_score", 3)
                    )
                    if should_close:
                        storage.close_position(cfg, primary_sym)
                        print(colorize(f"📒 纸面平仓 {primary_sym} @{lp}（止损={fmt_num(stop_loss)} 卖出分={sell.get('score', 0)}）", MAGENTA, color))
                else:
                    open_buy = paper_cfg.get("open_buy_score", 3)
                    open_confirm = paper_cfg.get("open_confirm_score", 2)
                    should_open = bool(
                        momentum.get("active")
                        or (buy.get("score", 0) >= open_buy and confirm.get("score", 0) >= open_confirm)
                    )
                    if should_open:
                        opened = storage.open_position(cfg, primary_sym, lp)
                        if opened:
                            print(colorize(f"📒 纸面开仓 {primary_sym} @{lp}（买入分={buy.get('score', 0)} 确认分={confirm.get('score', 0)}）", MAGENTA, color))

            zone = classify_zone(primary_analysis, lp, buy, sell, momentum, premomentum)

            alerts = []
            if zone != prev.get("zone"):
                alerts.append(f"区间切换: {zone}")
            if premomentum.get("active") and not prev.get("premomentum_active"):
                alerts.append(premomentum["verdict"])
            if momentum.get("active") and not prev.get("momentum_active"):
                alerts.append(momentum["verdict"])
            if prev.get("rsi") is not None and main_analysis.get("rsi14") is not None:
                if prev["rsi"] >= 30 > main_analysis["rsi14"]:
                    alerts.append(f"RSI 跌破30 ({main_analysis['rsi14']})")
                if prev["rsi"] <= 70 < main_analysis["rsi14"]:
                    alerts.append(f"RSI 突破70 ({main_analysis['rsi14']})")

            watch_text = render_watch_tick(
                tick,
                ts,
                primary_sym,
                lp,
                zone,
                main_analysis,
                buy,
                sell,
                confirm,
                plan,
                peer_analyses,
                market_analysis,
            )
            print(watch_text)
            for a in alerts:
                line = f"🚨 {a}"
                print(colorize(line, RED, color))
            log_line(cfg.get("runtime", {}).get("log_file"), watch_text.replace("\n", " || "))
            feed = {
                "time": ts_now(),
                "session": session,
                "symbol": primary_sym,
                "price": lp,
                "quote": {
                    "source": (main_snapshot.get("quote") or {}).get("source"),
                    "timestamp": (main_snapshot.get("quote") or {}).get("timestamp"),
                },
                "zone": zone,
                "action": action,
                "confirm_score": confirm["score"],
                "buy_score": buy["score"],
                "sell_score": sell["score"],
                "momentum": momentum,
                "premomentum": premomentum,
                "alerts": alerts,
                "main": {
                    "buy": buy,
                    "sell": sell,
                    "confirm": confirm,
                    "momentum": momentum,
                    "premomentum": premomentum,
                    "buy_score": buy["score"],
                    "sell_score": sell["score"],
                    "confirm_score": confirm["score"],
                    "ma20": main_analysis.get("ma", {}).get(20),
                    "supports": main_analysis.get("supports", []),
                    "resistances": main_analysis.get("resistances", []),
                    "short_term": short_plan,
                    "plan": plan,
                    "market": {
                        "symbol": market_snapshot["symbol"],
                        "label": market_analysis.get("label"),
                        "close": market_analysis.get("close"),
                        "rsi14": market_analysis.get("rsi14"),
                        "ma20": market_analysis.get("ma", {}).get(20),
                    } if market_snapshot and market_analysis else None,
                    "peers": [
                        {
                            "symbol": s["symbol"],
                            "label": s["analysis"]["label"],
                            "close": s["analysis"].get("close"),
                            "rsi14": s["analysis"].get("rsi14"),
                            "ma20": s["analysis"].get("ma", {}).get(20),
                        }
                        for s in peer_snapshots
                    ],
                },
                "history": (deepcopy(prev.get("history", [])) + [{
                    "time": ts,
                    "symbol": primary_sym,
                    "price": lp,
                    "zone": zone,
                    "action": action,
                    "buy": buy["verdict"],
                    "sell": sell["verdict"],
                    "confirm": confirm["verdict"],
                    "quote_source": (main_snapshot.get("quote") or {}).get("source"),
                    "quote_timestamp": (main_snapshot.get("quote") or {}).get("timestamp"),
                }])[-5:],
            }
            storage.record_watch_tick(cfg, feed, prev=(prev or None))
            push_webhook(cfg.get("runtime", {}).get("webhook_url"), {
                "time": ts_now(),
                "symbol": primary_sym,
                "price": lp,
                "zone": zone,
                "action": action,
                "confirm_score": confirm["score"],
                "buy_score": buy["score"],
                "sell_score": sell["score"],
                "momentum": momentum,
                "alerts": alerts,
            })

            prev = {
                "zone": zone,
                "action": action,
                "rsi": main_analysis.get("rsi14"),
                "history": feed["history"],
                "momentum_active": momentum.get("active"),
                "premomentum_active": premomentum.get("active"),
                "last_date": today,
            }
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n监控已停止。")


def main():
    cfg = load_config()
    session = current_session(cfg)
    effective_cfg = json.loads(json.dumps(cfg))
    effective_cfg["strategy"].update(session_rules(cfg, session))
    ap = argparse.ArgumentParser(description="best-buy 个股买卖决策分析")
    ap.add_argument("--symbol", required=True, help="主标的，如 000660.KS / AAPL / 0700.HK")
    ap.add_argument("--leveraged", help="杠杆衍生品，格式 code:source[:ratio]")
    ap.add_argument("--range", default="3mo", help="K线回溯，默认 3mo")
    ap.add_argument("--mode", default="both", choices=["buy", "sell", "both"])
    ap.add_argument("--news", action="store_true", help="触发 last30days 新闻（默认关）")
    ap.add_argument("--json", action="store_true", help="输出 JSON 而非文本报告")
    ap.add_argument("--watch", action="store_true", help="监控模式：循环轮询实时价并告警")
    ap.add_argument("--interval", type=int, default=15, help="监控轮询间隔(秒)，默认15")
    ap.add_argument("--ignore-market-hours", action="store_true", help="忽略交易时段，始终按 interval 轮询")
    ap.add_argument("--index", help="监控模式：大盘指数作环境确认")
    ap.add_argument("--ref", help="监控模式：隔夜美股参照")
    ap.add_argument("--divergence", action="store_true", help="保留兼容参数")
    ap.add_argument("--peers", help="确认对象，逗号分隔，如 000660.KS,SOXX,SMH")
    ap.add_argument("--market", help="环境确认对象，逗号分隔，如 ^KS11,^SOX")
    args = ap.parse_args()

    if args.watch:
        watch_mode(args, cfg)
        return

    main_snapshot = load_snapshot(args.symbol, args.range)
    if not main_snapshot:
        print(f"错误：无法获取 {args.symbol} 的K线数据", file=sys.stderr)
        sys.exit(1)

    leverage_spec = parse_leveraged(args.leveraged)
    lev_snapshot = None
    if leverage_spec:
        lev_snapshot = load_snapshot(leverage_spec["symbol"], args.range)

    default_peers = cfg.get("defaults", {}).get("peers", [])
    default_market = cfg.get("defaults", {}).get("market", [])
    peer_symbols = [s.strip() for s in (args.peers or ",".join(default_peers)).split(",") if s.strip()]
    market_symbols = [s.strip() for s in (args.market or ",".join(default_market)).split(",") if s.strip()]
    if args.index and args.index not in market_symbols:
        market_symbols = [args.index] + market_symbols
    peer_snapshots = build_peer_snapshots(peer_symbols, args.range)
    market_snapshot = load_snapshot(market_symbols[0], args.range) if market_symbols else None

    analysis = main_snapshot["analysis"]
    quote = main_snapshot["quote"]
    lev_analysis = lev_snapshot["analysis"] if lev_snapshot else None
    sell = sell_decision(analysis, effective_cfg)
    confirm = confirmation_score(analysis, [p["analysis"] for p in peer_snapshots], market_snapshot["analysis"] if market_snapshot else None, effective_cfg)
    analysis = attach_relative_strength(analysis, confirm)
    buy = buy_decision(analysis, effective_cfg)
    plan = trade_plan(analysis, confirm, effective_cfg)
    action = final_action(buy, sell, confirm)

    if args.json:
        out = {
            "symbol": args.symbol,
            "quote": quote,
            "analysis": analysis,
            "leveraged_analysis": lev_analysis,
            "leverage_spec": leverage_spec,
            "buy": buy,
            "sell": sell,
            "confirmation": confirm,
            "plan": plan,
            "action": action,
            "peers": peer_symbols,
            "market": market_symbols,
            "mode": args.mode,
            "news": args.news,
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(render_report(args.symbol, quote, analysis, lev_analysis, buy, sell, args.mode, leverage_spec))
        print("\n【三层确认】")
        print(f"  结论: {confirm['verdict']} ({confirm['score']})")
        for mark, txt in confirm["signals"][:8]:
            print(f"  {mark} {txt}")
        print(f"\n【最终动作】 {action}")
        zone_label = "买入区" if plan.get("buy_zone") else "观察区"
        zone_value = plan.get("buy_zone") or plan.get("watch_zone")
        if zone_value:
            anchor = plan.get("watch_anchor") or {}
            anchor_text = f" ({anchor.get('level')})" if anchor.get("level") else ""
            print(f"  {zone_label}: {fmt_num(zone_value['low'])} ~ {fmt_num(zone_value['high'])}{anchor_text}")
            print(f"  止损位: {fmt_num(plan.get('stop_loss'))}")
            if plan.get("take_profit"):
                tp = " | ".join(f"{x['level']}={fmt_num(x['price'])}" for x in plan["take_profit"])
                print(f"  止盈位: {tp}")
            if plan.get("note"):
                print(f"  备注: {plan['note']}")

    if args.news:
        print("\n" + "=" * 64)
        print("[新闻] 请通过 Skill 工具调用 /last30days:last30days 拉取最近30天新闻")
        print(f"       建议主题: {args.symbol} 及其行业关键词")
        print("=" * 64)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import json
from statistics import mean

from best_buy_app.core.config import load_config
from best_buy_app.core.decision_engine import attach_relative_strength, buy_decision, confirmation_score, sell_decision, trade_plan
from best_buy_app.core.indicators import analyze
from best_buy_app.data.market_data import load_rows


def apply_cost(price, side, fee_bps, slippage_bps):
    bps = fee_bps + slippage_bps
    if side == "buy":
        return price * (1 + bps / 10000)
    return price * (1 - bps / 10000)


def build_series(symbol, range_):
    yc = load_rows(symbol, range_)
    return yc["rows"] if yc and yc.get("rows") else []


def run_backtest(symbol, range_, cfg, peer_symbols=None, market_symbol=None):
    rows = build_series(symbol, range_)
    if not rows:
        return {"error": "无K线数据"}

    peer_rows = {sym: build_series(sym, range_) for sym in (peer_symbols or [])}
    market_rows = build_series(market_symbol, range_) if market_symbol else []

    bt = cfg.get("strategy", {}).get("backtest", {})
    warmup = bt.get("warmup_bars", 60)
    entry_buy_score = bt.get("entry_buy_score", 2)
    entry_confirm_score = bt.get("entry_confirm_score", 2)
    exit_sell_score = bt.get("exit_sell_score", 2)
    max_hold_bars = bt.get("max_hold_bars", 10)
    fee_bps = bt.get("fee_bps", 4)
    slippage_bps = bt.get("slippage_bps", 2)

    trades = []
    state = None
    reference_lags = []

    for i in range(warmup, len(rows)):
        window = rows[: i + 1]
        an = analyze(window, symbol)
        peer_analyses = [analyze(peer_rows[sym][: i + 1], sym) for sym in (peer_symbols or []) if len(peer_rows.get(sym, [])) > i]
        market_an = analyze(market_rows[: i + 1], market_symbol) if market_symbol and len(market_rows) > i else None

        sell = sell_decision(an, cfg)
        confirm = confirmation_score(an, peer_analyses, market_an, cfg)
        an = attach_relative_strength(an, confirm)
        buy = buy_decision(an, cfg)
        plan = trade_plan(an, confirm, cfg)
        price = rows[i]["close"]

        if state is None:
            if buy["score"] >= entry_buy_score and confirm["score"] >= entry_confirm_score and plan.get("buy_zone"):
                entry_price = apply_cost(price, "buy", fee_bps, slippage_bps)
                state = {
                    "entry_i": i,
                    "entry_price": entry_price,
                    "stop_loss": plan.get("stop_loss"),
                    "take_profit": plan.get("take_profit", []),
                    "entry_buy_score": buy["score"],
                    "entry_confirm_score": confirm["score"],
                }
            continue

        hold_bars = i - state["entry_i"]
        low = rows[i]["low"]
        high = rows[i]["high"]
        exit_reason = None
        exit_price = None

        if state["stop_loss"] and low <= state["stop_loss"]:
            exit_reason = "stop_loss"
            exit_price = apply_cost(state["stop_loss"], "sell", fee_bps, slippage_bps)
        elif state["take_profit"]:
            tp_price = state["take_profit"][0]["price"]
            if high >= tp_price:
                exit_reason = "take_profit"
                exit_price = apply_cost(tp_price, "sell", fee_bps, slippage_bps)
        if exit_reason is None and sell["score"] >= exit_sell_score:
            exit_reason = "signal_exit"
            exit_price = apply_cost(price, "sell", fee_bps, slippage_bps)
        if exit_reason is None and hold_bars >= max_hold_bars:
            exit_reason = "timeout"
            exit_price = apply_cost(price, "sell", fee_bps, slippage_bps)

        if exit_reason:
            ret_pct = (exit_price / state["entry_price"] - 1) * 100
            trades.append(
                {
                    "entry_i": state["entry_i"],
                    "exit_i": i,
                    "entry_price": round(state["entry_price"], 4),
                    "exit_price": round(exit_price, 4),
                    "ret_pct": round(ret_pct, 2),
                    "reason": exit_reason,
                    "hold_bars": hold_bars,
                }
            )
            state = None

    if peer_symbols:
        ref = peer_rows[peer_symbols[0]]
        for i in range(1, min(len(rows), len(ref))):
            base_move = rows[i]["close"] / rows[0]["close"] - 1
            ref_move = ref[i]["close"] / ref[0]["close"] - 1
            if abs(base_move - ref_move) > 0.01:
                reference_lags.append(i)
                break

    wins = [t for t in trades if t["ret_pct"] > 0]
    stop_hits = len([t for t in trades if t["reason"] == "stop_loss"])
    tp_hits = len([t for t in trades if t["reason"] == "take_profit"])
    signal_exits = len([t for t in trades if t["reason"] == "signal_exit"])
    timeout_exits = len([t for t in trades if t["reason"] == "timeout"])

    return {
        "symbol": symbol,
        "trades": len(trades),
        "win_rate": round(len(wins) / len(trades) * 100, 1) if trades else 0,
        "avg_return": round(mean([t["ret_pct"] for t in trades]), 2) if trades else 0,
        "median_hold_bars": sorted([t["hold_bars"] for t in trades])[len(trades) // 2] if trades else 0,
        "stop_hits": stop_hits,
        "tp_hits": tp_hits,
        "signal_exits": signal_exits,
        "timeout_exits": timeout_exits,
        "lead_lag_bars": reference_lags[0] if reference_lags else None,
        "sample_trades": trades[:20],
    }


def main():
    ap = argparse.ArgumentParser(description="best-buy 事件级回测")
    ap.add_argument("--symbol", help="主标的，默认 config 里的 main_symbol")
    ap.add_argument("--range", default="6mo")
    ap.add_argument("--peers", help="确认对象，逗号分隔")
    ap.add_argument("--market", help="环境对象")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    cfg = load_config()
    default_symbol = cfg.get("defaults", {}).get("main_symbol", "07709")
    symbol = args.symbol or default_symbol
    default_peers = cfg.get("defaults", {}).get("peers", [])
    default_market = cfg.get("defaults", {}).get("market", [])
    peer_symbols = [s.strip() for s in (args.peers or ",".join(default_peers)).split(",") if s.strip()]
    market_symbol = None
    if args.market:
        market_symbol = [s.strip() for s in args.market.split(",") if s.strip()][0]
    elif default_market:
        market_symbol = default_market[0]
    result = run_backtest(symbol, args.range, cfg, peer_symbols, market_symbol)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if "error" in result:
        print(result["error"])
        return
    print(f"标的: {result['symbol']}")
    print(f"交易次数: {result['trades']}")
    print(f"胜率: {result['win_rate']}%")
    print(f"平均收益: {result['avg_return']}%")
    print(f"中位持有: {result['median_hold_bars']} bars")
    print(f"止损命中: {result['stop_hits']}")
    print(f"止盈命中: {result['tp_hits']}")
    print(f"信号退出: {result['signal_exits']}")
    print(f"超时退出: {result['timeout_exits']}")
    print(f"粗略滞后: {result['lead_lag_bars']} bars")


if __name__ == "__main__":
    main()

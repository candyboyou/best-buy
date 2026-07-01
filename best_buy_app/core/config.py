#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
from copy import deepcopy
from pathlib import Path

DEFAULT_CONFIG = {
    "strategy": {
        "rsi_buy": 30,
        "rsi_sell": 70,
        "kdj_buy_j": 0,
        "kdj_sell_j": 100,
        "buy_ma": 20,
        "buy_ma_tolerance": 0.02,
        "buy_zone_buffer": 0.018,
        "buy_zone_max_width_pct": 0.035,
        "sell_boll_tolerance": 0.98,
        "stop_loss_support_buffer": 0.015,
        "stop_loss_ma60_buffer": 0.02,
        "stop_loss_atr_multiplier": 1.5,
        "take_profit_count": 3,
        "short_term": {
            "pullback_pct": 0.012,
            "breakout_buffer_pct": 0.003,
            "stop_loss_pct": 0.018,
            "max_primary_pullback_pct": 0.03,
        },
        "momentum": {
            "enabled": True,
            "lookback_ticks": 20,
            "trigger_pct": 3.0,
            "strong_pct": 5.0,
            "chase_limit_pct": 4.0,
            "confirm_min": 2,
            "sell_score_block": 3,
        },
        "premomentum": {
            "enabled": True,
            "upstream_trigger_pct": 0.8,
            "main_lag_max_pct": 1.5,
            "strong_score": 70,
            "watch_score": 50,
            "lead_weights": {
                "underlying": 35,
                "korea_semis": 20,
                "us_storage": 30,
                "market": 15,
            },
        },
        "confirmation": {
            "main_rsi_min": 45,
            "main_rsi_max": 65,
            "peer_rsi_min": 50,
            "market_rsi_min": 45,
            "market_rsi_max": 65,
            "weighted_threshold_support": 65,
            "weighted_threshold_neutral": 45,
            "weights": {
                "main": 20,
                "underlying": 30,
                "korea_semis": 20,
                "us_storage": 20,
                "market": 10,
            },
            "groups": {
                "underlying": ["000660.KS", "000660", "SK海力士"],
                "korea_semis": ["005930.KS", "005930", "07747", "三星电子"],
                "us_storage": ["DRAM", "MU", "MRVL", "SNDK", "SNXX", "SOXX", "SMH", "WDC", "STX"],
                "market": ["^KS11", ".KOSPI", "KOSPI"],
            },
        },
        "backtest": {
            "warmup_bars": 60,
            "entry_buy_score": 2,
            "entry_confirm_score": 2,
            "exit_sell_score": 2,
            "max_hold_bars": 10,
            "fee_bps": 4,
            "slippage_bps": 2
        },
    },
    "watch": {
        "min_interval_seconds": 5,
        "rows_refresh_seconds": 600,
        "peer_rows_refresh_seconds": 900,
        "quote_refresh_seconds": 15,
        "peer_quote_refresh_seconds": 60,
        "market_quote_refresh_seconds": 60,
        "market_hours_enabled": True,
    },
    "defaults": {
        "main_symbol": "07709",
        "peers": ["000660.KS", "005930.KS", "07747", "DRAM", "MU", "MRVL", "SNDK", "SNXX"],
        "market": ["^KS11"],
        "storage_refs": ["DRAM", "MU", "MRVL", "SNDK", "SNXX", "SOXX", "SMH", "WDC", "STX"],
        "market_groups": {
            "preopen": ["^GSPC", "^SOX", "SOXX", "SMH", "MU"],
            "intraday": ["^KS11", "^SOX", "SOXX", "SMH", "MU"],
            "postclose": ["SOXX", "SMH", "MU", "WDC", "STX"]
        },
        "session": "intraday",
    },
    "runtime": {
        "log_file": "best_buy.log",
        "webhook_url": "",
        "feed_file": "best_buy_feed.json",
        "db_file": ".runtime/best_buy.db",
    },
    "market_calendars": {
        "hk": {"holidays": [], "open_days": [], "early_closes": {}},
        "us": {"holidays": [], "open_days": [], "early_closes": {}},
        "kr": {"holidays": [], "open_days": [], "early_closes": {}},
    },
    "ai": {
        "provider": "generic",
        "endpoint": "",
        "api_key": "",
        "model": "",
    },
}


def deep_merge(base, override):
    out = deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(path=None):
    cfg = deepcopy(DEFAULT_CONFIG)
    cfg_path = Path(path) if path else Path("config.json")
    if cfg_path.exists():
        with cfg_path.open("r", encoding="utf-8") as f:
            override = json.load(f)
        cfg = deep_merge(cfg, override)
    return cfg

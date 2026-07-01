#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""SQLite 持久化层。

替代原 best_buy_feed.json 的单文件覆盖写入，提供监控历史归档、
告警/决策事件日志、最新 feed、快照缓存与关注股票 watchlist。

使用标准库 sqlite3，连接采用 WAL 模式，多线程环境下每次操作使用短连接。
"""

import json
import sqlite3
import time
from pathlib import Path

from best_buy_app.core.output_utils import ts_now
from best_buy_app.data.market_data import fetch_quote, load_rows

ROOT = Path(__file__).resolve().parents[2]

SCHEMA = """
CREATE TABLE IF NOT EXISTS watch_ticks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  ts_text TEXT,
  session TEXT,
  symbol TEXT,
  price REAL,
  zone TEXT,
  action TEXT,
  confirm_score INTEGER,
  buy_score INTEGER,
  sell_score INTEGER,
  momentum_verdict TEXT,
  premomentum_verdict TEXT,
  alerts TEXT,
  payload TEXT
);
CREATE INDEX IF NOT EXISTS idx_watch_ticks_symbol_ts ON watch_ticks(symbol, ts);

CREATE TABLE IF NOT EXISTS decision_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  ts_text TEXT,
  symbol TEXT,
  event_type TEXT,
  message TEXT,
  zone TEXT,
  action TEXT,
  payload TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_symbol_ts ON decision_events(symbol, ts);

CREATE TABLE IF NOT EXISTS latest_feed (
  symbol TEXT PRIMARY KEY,
  payload TEXT NOT NULL,
  updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS snapshot_cache (
  symbol TEXT,
  range_ TEXT,
  kind TEXT,
  payload TEXT,
  fetched_at REAL,
  PRIMARY KEY (symbol, range_, kind)
);

CREATE TABLE IF NOT EXISTS watchlist (
  symbol TEXT PRIMARY KEY,
  label TEXT,
  note TEXT,
  added_at REAL,
  added_at_text TEXT,
  active INTEGER DEFAULT 1
);
"""


def db_path(cfg):
    """返回解析后的 DB 路径，相对路径基于项目根目录。"""
    raw = (cfg.get("runtime", {}) or {}).get("db_file", ".runtime/best_buy.db")
    path = Path(raw)
    if not path.is_absolute():
        path = ROOT / raw
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def connect(cfg):
    """建立一个新的 SQLite 连接（WAL + Row 工厂）。"""
    conn = sqlite3.connect(
        str(db_path(cfg)),
        check_same_thread=False,
        isolation_level=None,  # autocommit；写操作显式 BEGIN/COMMIT
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=3000")
    return conn


def init_db(cfg):
    """幂等建表。"""
    conn = connect(cfg)
    try:
        conn.executescript(SCHEMA)
    finally:
        conn.close()


def _as_dict(row):
    return dict(row) if row else None


def record_watch_tick(cfg, feed, prev=None):
    """记录一个 watch tick：写 watch_ticks、刷新 latest_feed、对比 prev 写决策事件。"""
    init_db(cfg)
    symbol = feed.get("symbol")
    ts = time.time()
    ts_text = feed.get("time") or ts_now()
    momentum = feed.get("momentum") or {}
    premomentum = feed.get("premomentum") or {}
    alerts = feed.get("alerts") or []
    zone = feed.get("zone")
    action = feed.get("action")
    payload = json.dumps(feed, ensure_ascii=False)

    conn = connect(cfg)
    try:
        conn.execute("BEGIN")
        conn.execute(
            """INSERT INTO watch_ticks
               (ts, ts_text, session, symbol, price, zone, action,
                confirm_score, buy_score, sell_score,
                momentum_verdict, premomentum_verdict, alerts, payload)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                ts, ts_text, feed.get("session"), symbol, feed.get("price"),
                zone, action,
                feed.get("confirm_score"), feed.get("buy_score"), feed.get("sell_score"),
                momentum.get("verdict"), premomentum.get("verdict"),
                json.dumps(alerts, ensure_ascii=False), payload,
            ),
        )
        conn.execute(
            """INSERT INTO latest_feed (symbol, payload, updated_at) VALUES (?,?,?)
               ON CONFLICT(symbol) DO UPDATE SET payload=excluded.payload, updated_at=excluded.updated_at""",
            (symbol, payload, ts),
        )
        prev_zone = (prev or {}).get("zone")
        prev_action = (prev or {}).get("action")
        if prev is not None and zone != prev_zone:
            conn.execute(
                """INSERT INTO decision_events
                   (ts, ts_text, symbol, event_type, message, zone, action, payload)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (ts, ts_text, symbol, "zone_change",
                 f"区间切换: {prev_zone} -> {zone}", zone, action, payload),
            )
        if prev is not None and action != prev_action:
            conn.execute(
                """INSERT INTO decision_events
                   (ts, ts_text, symbol, event_type, message, zone, action, payload)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (ts, ts_text, symbol, "action_change",
                 f"动作切换: {prev_action} -> {action}", zone, action, payload),
            )
        for alert in alerts:
            conn.execute(
                """INSERT INTO decision_events
                   (ts, ts_text, symbol, event_type, message, zone, action, payload)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (ts, ts_text, symbol, "alert", str(alert), zone, action, payload),
            )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def get_latest_feed(cfg, symbol=None):
    """返回最新 feed dict；symbol 为空时取 updated_at 最大者。"""
    init_db(cfg)
    conn = connect(cfg)
    try:
        if symbol:
            row = conn.execute(
                "SELECT payload FROM latest_feed WHERE symbol=? ORDER BY updated_at DESC LIMIT 1",
                (symbol,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT payload FROM latest_feed ORDER BY updated_at DESC LIMIT 1"
            ).fetchone()
        if not row:
            return None
        return json.loads(row["payload"])
    finally:
        conn.close()


def list_watch_ticks(cfg, symbol, limit=100):
    """按时间倒序返回监控历史。"""
    init_db(cfg)
    conn = connect(cfg)
    try:
        rows = conn.execute(
            """SELECT ts, ts_text, session, symbol, price, zone, action,
                      confirm_score, buy_score, sell_score,
                      momentum_verdict, premomentum_verdict, alerts
               FROM watch_ticks
               WHERE symbol = ?
               ORDER BY ts DESC
               LIMIT ?""",
            (symbol, limit),
        ).fetchall()
        result = []
        for row in rows:
            item = _as_dict(row)
            try:
                item["alerts"] = json.loads(item.get("alerts") or "[]")
            except (TypeError, ValueError):
                item["alerts"] = []
            result.append(item)
        return result
    finally:
        conn.close()


def list_events(cfg, symbol, limit=100):
    """按时间倒序返回决策/告警事件。"""
    init_db(cfg)
    conn = connect(cfg)
    try:
        rows = conn.execute(
            """SELECT ts, ts_text, symbol, event_type, message, zone, action
               FROM decision_events
               WHERE symbol = ?
               ORDER BY ts DESC
               LIMIT ?""",
            (symbol, limit),
        ).fetchall()
        return [_as_dict(row) for row in rows]
    finally:
        conn.close()


def _cache_get(cfg, symbol, range_, kind, ttl_seconds):
    init_db(cfg)
    conn = connect(cfg)
    try:
        row = conn.execute(
            "SELECT payload, fetched_at FROM snapshot_cache WHERE symbol=? AND range_=? AND kind=?",
            (symbol, range_, kind),
        ).fetchone()
        if not row:
            return None
        if ttl_seconds is not None and (time.time() - row["fetched_at"]) >= ttl_seconds:
            return None
        try:
            return json.loads(row["payload"])
        except (TypeError, ValueError):
            return None
    finally:
        conn.close()


def _cache_set(cfg, symbol, range_, kind, payload):
    init_db(cfg)
    conn = connect(cfg)
    try:
        conn.execute("BEGIN")
        conn.execute(
            """INSERT INTO snapshot_cache (symbol, range_, kind, payload, fetched_at)
               VALUES (?,?,?,?,?)
               ON CONFLICT(symbol, range_, kind)
               DO UPDATE SET payload=excluded.payload, fetched_at=excluded.fetched_at""",
            (symbol, range_, kind, json.dumps(payload, ensure_ascii=False), time.time()),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def load_rows_cached(cfg, symbol, range_="3mo", ttl_seconds=None):
    """带 TTL 的 K 线缓存；命中返回缓存，未命中拉取并回写。返回 None 表示无数据。"""
    cached = _cache_get(cfg, symbol, range_, "rows", ttl_seconds)
    if cached is not None:
        return cached
    data = load_rows(symbol, range_)
    if data:
        _cache_set(cfg, symbol, range_, "rows", data)
    return data


def fetch_quote_cached(cfg, symbol, ttl_seconds=None):
    """带 TTL 的实时报价缓存。"""
    cached = _cache_get(cfg, symbol, "", "quote", ttl_seconds)
    if cached is not None:
        return cached
    quote = fetch_quote(symbol)
    if quote:
        _cache_set(cfg, symbol, "", "quote", quote)
    return quote


def add_watch(cfg, symbol, label=None, note=None):
    """新增关注股票（已存在则更新 label/note，不重复报错）。"""
    init_db(cfg)
    symbol = str(symbol).strip().upper()
    if not symbol:
        raise ValueError("symbol is required")
    now = time.time()
    conn = connect(cfg)
    try:
        conn.execute("BEGIN")
        conn.execute(
            """INSERT INTO watchlist (symbol, label, note, added_at, added_at_text, active)
               VALUES (?,?,?,?,?,1)
               ON CONFLICT(symbol) DO UPDATE SET label=excluded.label, note=excluded.note, active=1""",
            (symbol, label, note, now, ts_now()),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()
    return {"symbol": symbol, "label": label, "note": note}


def remove_watch(cfg, symbol):
    """删除关注股票。返回是否删除了一行。"""
    init_db(cfg)
    symbol = str(symbol).strip().upper()
    conn = connect(cfg)
    try:
        cur = conn.execute("DELETE FROM watchlist WHERE symbol=?", (symbol,))
        return cur.rowcount > 0
    finally:
        conn.close()


def list_watchlist(cfg):
    """返回全部关注股票，按加入时间倒序。"""
    init_db(cfg)
    conn = connect(cfg)
    try:
        rows = conn.execute(
            """SELECT symbol, label, note, added_at, added_at_text, active
               FROM watchlist ORDER BY added_at DESC"""
        ).fetchall()
        return [_as_dict(row) for row in rows]
    finally:
        conn.close()
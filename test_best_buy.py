import json
import time
import unittest
from unittest.mock import patch

from best_buy_app.core.decision_engine import buy_decision, classify_zone, confirmation_score, final_action, final_action_with_momentum, momentum_decision, premomentum_decision, render_watch_tick, sell_decision, short_term_plan, trade_plan
from best_buy_app.core.indicators import analyze, fib_retrace
from best_buy_app.cli.best_buy import parse_leveraged
from best_buy_app.web.dashboard_server import AI_INSTRUCTIONS, build_global_stock_context, build_news_context, build_responses_input, call_external_ai, compact_ai_context, default_global_stock_plan, enrich_feed_for_ai, local_reply, minimal_responses_payload, parse_json_object, parse_responses_text, parse_stream_text_delta
from best_buy_app.data import storage
from best_buy_app.data.global_stock_data import intraday_swing_summary, normalize_symbol, stock_quote
from best_buy_app.data.market_data import fetch_quote
from best_buy_app.data.news_aggregator import filter_items, parse_rss_content, source_keys_for_request


class TestBestBuy(unittest.TestCase):
    def test_parse_leveraged(self):
        self.assertEqual(parse_leveraged("07709:tencent@ratio=2"), {"symbol": "07709", "source": "tencent", "ratio": 2.0})

    def test_fetch_quote_treats_plain_numeric_symbol_as_hk_quote(self):
        with patch("best_buy_app.data.market_data.hk_quote_tencent", return_value={"source": "tencent", "price": 149.05}) as tencent, \
             patch("best_buy_app.data.market_data.hk_quote_sina") as sina, \
             patch("best_buy_app.data.market_data.yahoo_chart") as yahoo:
            quote = fetch_quote("07709")

        self.assertEqual(quote["source"], "tencent")
        self.assertEqual(quote["price"], 149.05)
        tencent.assert_called_once_with("07709")
        yahoo.assert_not_called()

    def test_fetch_quote_uses_first_valid_hk_quote_without_waiting_for_slow_source(self):
        def slow_tencent(_code):
            time.sleep(0.5)
            return {"source": "tencent", "price": 149.05}

        with patch("best_buy_app.data.market_data.hk_quote_tencent", side_effect=slow_tencent), \
             patch("best_buy_app.data.market_data.hk_quote_sina", return_value={"source": "sina", "price": 150.25}):
            started_at = time.monotonic()
            quote = fetch_quote("07709")
            elapsed = time.monotonic() - started_at

        self.assertEqual(quote["source"], "sina")
        self.assertEqual(quote["price"], 150.25)
        self.assertLess(elapsed, 0.3)

    def test_stock_quote_uses_first_valid_us_quote_without_waiting_for_slow_source(self):
        def slow_sina(_ticker):
            time.sleep(0.5)
            return {"source": "sina", "price": 1120.0}

        with patch("best_buy_app.data.global_stock_data.us_stock_quote_sina", side_effect=slow_sina), \
             patch("best_buy_app.data.global_stock_data.us_stock_quote_tencent", return_value={"source": "tencent", "price": 1145.28}):
            started_at = time.monotonic()
            quote = stock_quote("MU")
            elapsed = time.monotonic() - started_at

        self.assertEqual(quote["source"], "tencent")
        self.assertEqual(quote["price"], 1145.28)
        self.assertEqual(quote["market"], "us")
        self.assertLess(elapsed, 0.3)

    def test_confirmation_score(self):
        main = {"close": 110, "ma": {20: 100}, "rsi14": 50, "label": "main"}
        peer = {"close": 20, "ma": {20: 18}, "rsi14": 55, "label": "peer"}
        market = {"close": 300, "ma": {20: 290}, "rsi14": 52, "label": "market"}
        res = confirmation_score(main, [peer], market)
        self.assertGreaterEqual(res["score"], 2)

    def test_default_strategy_relaxes_bottom_entry_and_limits_chasing(self):
        with open("config.json", "r", encoding="utf-8") as f:
            cfg = json.load(f)

        st = cfg["strategy"]
        self.assertEqual(st["buy_zone_buffer"], 0.035)
        self.assertEqual(st["rsi_buy"], 35)
        self.assertEqual(st["kdj_buy_j"], 10)
        self.assertEqual(st["momentum"]["chase_limit_pct"], 2.5)

    def test_fib_retrace_defaults_to_sixty_bars(self):
        rows = []
        for i in range(70):
            rows.append({"high": 100 + i, "low": 90 + i, "close": 95 + i})
        rows[15]["low"] = 40
        rows[5]["low"] = 10

        hi, lo, _ = fib_retrace(rows)

        self.assertEqual(hi, 169)
        self.assertEqual(lo, 40)

    def test_analyze_returns_volume_ratio(self):
        rows = []
        for i in range(61):
            rows.append({
                "date": f"2026-01-{(i % 28) + 1:02d}",
                "open": 100 + i,
                "high": 102 + i,
                "low": 99 + i,
                "close": 101 + i,
                "volume": 100,
            })
        rows[-1]["volume"] = 50

        res = analyze(rows, "main")

        self.assertEqual(res["volume_ratio"], 0.56)

    def test_buy_decision_scores_long_lower_shadow_and_penalizes_weak_volume_breakout(self):
        base = {
            "close": 101,
            "ma": {20: 100},
            "rsi14": 50,
            "kdj": {"j": 50, "k": 50, "d": 55, "cross": "死叉"},
            "macd": {"hist": 1, "shortening": False},
            "candle": {"is_long_lower_shadow": True},
            "boll": {"upper": 110, "lower": 90},
            "resistances": [{"level": "R1", "price": 100}],
            "volume_ratio": 0.7,
        }

        res = buy_decision(base)

        self.assertIn("探底反转", " ".join(s[1] for s in res["signals"]))
        self.assertIn("缩量反弹", " ".join(s[1] for s in res["signals"]))
        self.assertEqual(res["score"], 1)

    def test_sell_decision_ignores_rsi_overbought_in_strong_uptrend(self):
        analysis = {
            "close": 110,
            "ma": {5: 108, 10: 104, 20: 100},
            "rsi14": 75,
            "kdj": {"j": 50, "k": 50, "d": 45},
            "macd": {"hist": 1, "shortening": False},
            "boll": {"upper": 130},
            "candle": {},
        }

        res = sell_decision(analysis)

        self.assertEqual(res["score"], 0)
        self.assertIn("强多头排列", " ".join(s[1] for s in res["signals"]))

    def test_trade_plan_uses_watch_zone_when_buy_signal_is_weak(self):
        analysis = {
            "close": 100,
            "ma": {20: 98, 60: 90},
            "rsi14": 50,
            "kdj": {"j": 50, "k": 50, "d": 55, "cross": "死叉"},
            "macd": {"hist": 1, "shortening": False},
            "candle": {},
            "boll": {"upper": 110, "lower": 90},
            "supports": [{"level": "S1", "price": 95, "dist_pct": -5.0}],
            "resistances": [{"level": "R1", "price": 105, "dist_pct": 5.0}],
        }
        plan = trade_plan(analysis, {"verdict": "ok"})
        self.assertIsNone(plan["buy_zone"])
        self.assertIsNotNone(plan["watch_zone"])
        self.assertLess(plan["watch_zone"]["high"] - plan["watch_zone"]["low"], 5)
        self.assertIsNotNone(plan["stop_loss"])

    def test_trade_plan_uses_buy_zone_only_when_price_is_actionable(self):
        analysis = {
            "close": 98,
            "ma": {20: 98, 60: 90},
            "rsi14": 25,
            "kdj": {"j": -5, "k": 20, "d": 30, "cross": "金叉"},
            "macd": {"hist": -1, "shortening": True},
            "candle": {},
            "boll": {"upper": 110, "lower": 90},
            "supports": [{"level": "S1", "price": 98, "dist_pct": 0.0}],
            "resistances": [{"level": "R1", "price": 105, "dist_pct": 7.1}],
        }
        plan = trade_plan(analysis, {"verdict": "ok", "score": 2})
        self.assertIsNotNone(plan["buy_zone"])
        self.assertIsNone(plan["watch_zone"])

    def test_trade_plan_uses_ma10_as_support_anchor(self):
        analysis = {
            "close": 142.5,
            "ma": {10: 142, 20: 139, 60: 130},
            "rsi14": 50,
            "kdj": {"j": 50, "k": 50, "d": 55, "cross": "死叉"},
            "macd": {"hist": 1, "shortening": False},
            "candle": {},
            "boll": {"upper": 150, "lower": 136},
            "supports": [{"level": "S1", "price": 139, "dist_pct": -2.5}],
            "resistances": [{"level": "R1", "price": 150, "dist_pct": 5.3}],
        }

        plan = trade_plan(analysis, {"verdict": "ok", "score": 2})

        self.assertEqual(plan["watch_anchor"], {"level": "MA10", "price": 142})

    def test_trade_plan_raises_stop_loss_with_trailing_stop(self):
        analysis = {
            "close": 145,
            "ma": {10: 142, 20: 139, 60: 130},
            "rsi14": 50,
            "kdj": {"j": 50, "k": 50, "d": 55, "cross": "死叉"},
            "macd": {"hist": 1, "shortening": False},
            "candle": {},
            "boll": {"upper": 152, "lower": 140},
            "supports": [{"level": "S1", "price": 139, "dist_pct": -4.1}],
            "resistances": [{"level": "R1", "price": 150, "dist_pct": 3.4}],
            "recent": [
                {"date": "2026-01-01", "close": 144, "high": 146, "low": 140},
                {"date": "2026-01-02", "close": 145, "high": 150, "low": 143},
            ],
        }

        plan = trade_plan(analysis, {"verdict": "ok", "score": 2})

        self.assertEqual(plan["stop_loss"], 144)

    def test_short_term_plan_prefers_nearby_entries(self):
        analysis = {
            "close": 149.05,
            "ma": {5: 157.85, 10: 155.5, 20: 136.2},
            "supports": [{"level": "50%", "price": 139.68, "dist_pct": -6.3}],
            "resistances": [{"level": "MA10", "price": 155.5, "dist_pct": 4.3}],
        }
        plan = short_term_plan(analysis)
        prices = [x["price"] for x in plan["entries"]]
        self.assertLess(abs(prices[0] / 149.05 - 1), 0.02)
        self.assertIn(139.68, [x["price"] for x in plan["deep_supports"]])
        self.assertNotIn(139.68, prices)

    def test_final_action(self):
        buy = {"score": 3}
        sell = {"score": 0}
        confirm = {"score": 2}
        self.assertIn("买入", final_action(buy, sell, confirm))

    def test_classify_zone_uses_support_watch_when_buy_signal_is_weak(self):
        analysis = {
            "supports": [{"level": "38.2%", "price": 152.41}],
            "resistances": [{"level": "MA10", "price": 155.87}],
        }
        zone = classify_zone(
            analysis,
            152.7,
            {"score": 1},
            {"score": 1},
            {"active": False, "extended": False},
            {"active": False},
        )
        self.assertEqual(zone, "🟡支撑观察")

    def test_classify_zone_uses_buy_zone_only_when_buy_signal_is_ready(self):
        analysis = {
            "supports": [{"level": "38.2%", "price": 152.41}],
            "resistances": [{"level": "MA10", "price": 155.87}],
        }
        zone = classify_zone(
            analysis,
            152.7,
            {"score": 2},
            {"score": 0},
            {"active": False, "extended": False},
            {"active": False},
        )
        self.assertEqual(zone, "🟢买点区")

    def test_render_watch_tick_is_compact_and_actionable(self):
        analysis = {
            "close": 100,
            "ma": {20: 98},
            "supports": [{"level": "S1", "price": 98, "dist_pct": -2.0}],
            "resistances": [{"level": "R1", "price": 105, "dist_pct": 5.0}],
        }
        text = render_watch_tick(
            12,
            "10:58:39",
            "07709",
            100,
            "买点区",
            analysis,
            {"verdict": "无买入信号，暂不建议追多"},
            {"verdict": "无见顶信号，暂不卖出"},
            {"verdict": "确认层中性，适合等回撤", "score": 2},
            {
                "stop_loss": 95,
                "note": "观察支撑区而非追价",
                "premomentum": {"verdict": "无预动量信号", "score": 0, "main_pct": 0},
                "momentum": {"verdict": "无动量买入信号", "score": 0, "pct": 0},
                "short_term": {
                    "entries": [{"level": "近端回踩", "price": 98}],
                    "exits": [{"level": "R1", "price": 105}],
                    "deep_supports": [{"level": "S1", "price": 98, "dist_pct": -2.0}],
                    "stop_loss": 95,
                },
            },
            [{"label": "000660.KS", "close": 2628000.0}],
            {"label": "^KS11", "close": 3000.0},
        )
        self.assertIn("[10:58:39] #12 07709=100 买点区", text)
        self.assertIn("000660.KS=2,628,000.00", text)
        self.assertIn("支撑:S1=98(-2.0%)", text)
        self.assertIn("阻力:R1=105(+5.0%)", text)
        self.assertIn("短线买点:近端回踩=98", text)
        self.assertIn("短线卖点:R1=105", text)
        self.assertIn("短线止损:95", text)

    def test_momentum_decision_catches_intraday_jump(self):
        history = [
            {"time": "13:20:10", "price": 148.9},
            {"time": "13:21:25", "price": 157.55},
        ]
        analysis = {
            "resistances": [{"level": "MA5", "price": 153.32, "dist_pct": -2.7}],
        }
        confirm = {"score": 4}
        sell = {"score": 1}
        res = momentum_decision(history, analysis, confirm, sell)
        self.assertFalse(res["active"])
        self.assertTrue(res["extended"])
        self.assertGreaterEqual(res["pct"], 5)
        self.assertIn("谨慎追高", res["verdict"])
        self.assertIn("谨慎追高", final_action_with_momentum({"score": 0}, sell, confirm, res))

    def test_momentum_decision_blocks_large_rebound_from_intraday_low(self):
        history = [
            {"time": "13:20:10", "price": 145.0},
            {"time": "13:21:25", "price": 140.0},
            {"time": "13:22:30", "price": 145.0},
        ]
        analysis = {"resistances": [{"level": "R1", "price": 144, "dist_pct": -0.7}]}
        res = momentum_decision(history, analysis, {"score": 4}, {"score": 1}, {"strategy": {"momentum": {"chase_limit_pct": 10}}})

        self.assertFalse(res["active"])
        self.assertIn("日内低点", " ".join(s[1] for s in res["signals"]))

    def test_premomentum_detects_upstream_lead_before_07709_moves(self):
        main_history = [
            {"time": "13:20:10", "price": 148.9},
            {"time": "13:20:40", "price": 149.5},
        ]
        peers = [
            {"symbol": "000660.KS", "label": "000660.KS", "history": [{"price": 2600000}, {"price": 2691000}]},
            {"symbol": "MU", "label": "MU", "history": [{"price": 1120}, {"price": 1145}]},
            {"symbol": "DRAM", "label": "DRAM", "history": [{"price": 70}, {"price": 71.9}]},
        ]
        market = {"symbol": "^KS11", "label": "^KS11", "history": [{"price": 8400}, {"price": 8561}]}
        res = premomentum_decision(main_history, peers, market)
        self.assertTrue(res["active"])
        self.assertIn("可能补涨", res["verdict"])

    def test_render_watch_tick_handles_premomentum_not_ready(self):
        pm = premomentum_decision([{"time": "09:30:00", "price": 154.25}], [], None)
        text = render_watch_tick(
            1,
            "09:30:00",
            "07709",
            154.25,
            "中性",
            {"close": 154.25, "ma": {20: 150}, "supports": [], "resistances": []},
            {"verdict": "无买入信号，暂不建议追多"},
            {"verdict": "无见顶信号，暂不卖出"},
            {"verdict": "确认层中性，适合等回撤", "score": 2},
            {"note": "观察", "premomentum": pm, "momentum": {"verdict": "动量数据不足", "score": 0, "pct": None}},
        )
        self.assertIn("预动量数据不足", text)
        self.assertIn("07709=N/A", text)

    def test_dashboard_chat_local_reply_uses_feed_context(self):
        feed = {
            "symbol": "07709",
            "price": 153,
            "zone": "买点区",
            "action": "继续观察",
            "confirm_score": 4,
            "main": {
                "buy": {"verdict": "无买入信号，暂不建议追多"},
                "sell": {"verdict": "见顶信号初现，可减部分仓位或上移止盈"},
                "confirm": {"verdict": "确认层支持，趋势环境偏顺"},
                "supports": [{"level": "38.2%", "price": 152.41, "dist_pct": -0.3}],
                "resistances": [{"level": "MA10", "price": 153.32, "dist_pct": 0.3}],
                "peers": [{"label": "000660.KS", "close": 2628000.0}],
            },
        }
        reply = local_reply("现在能买吗？", feed)
        self.assertIn("继续观察", reply)
        self.assertIn("38.2%=152.41", reply)
        self.assertNotIn("你的问题", reply)

    def test_dashboard_external_ai_returns_none_without_endpoint(self):
        self.assertIsNone(call_external_ai({"ai": {}}, "现在能买吗？", {}, []))

    def test_parse_responses_text_reads_output_content(self):
        data = {
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "output_text",
                            "text": "Hello. What would you like to work on?",
                        }
                    ],
                }
            ],
        }
        self.assertEqual(parse_responses_text(data), "Hello. What would you like to work on?")

    def test_parse_stream_text_delta_reads_responses_delta(self):
        data = {"type": "response.output_text.delta", "delta": "实时"}
        self.assertEqual(parse_stream_text_delta("openai_responses", data), "实时")

    def test_parse_stream_text_delta_reads_chat_completion_delta(self):
        data = {"choices": [{"delta": {"content": "分析"}}]}
        self.assertEqual(parse_stream_text_delta("openai_compatible", data), "分析")

    def test_openai_responses_payload_matches_proxy_shape(self):
        payload = minimal_responses_payload("gpt-5.5", "hello")
        self.assertEqual(payload, {"model": "gpt-5.5", "input": "hello"})
        text = build_responses_input("现在能买吗", '{"symbol":"07709"}')
        self.assertIn("当前监控数据", text)
        self.assertIn("用户问题：现在能买吗", text)
        self.assertIn("不要复述用户问题", text)
        self.assertIn("global-stock-data", text)
        self.assertIn("Yahoo chart", text)

    @patch("best_buy_app.web.dashboard_server.post_ai_json", side_effect=[
        {"choices": [{"message": {"content": '{"global_stock_data": {"use_tool": false}, "news_aggregator": {"use_tool": false}}'}}]},
        {"choices": [{"message": {"content": "ok"}}]},
    ])
    def test_openai_compatible_system_prompt_includes_stock_data_skill(self, post_ai_json):
        cfg = {
            "ai": {
                "provider": "openai_compatible",
                "endpoint": "https://example.test/v1",
                "model": "gpt-test",
            }
        }
        self.assertEqual(call_external_ai(cfg, "昨天美股几波涨跌？", {"symbol": "MU"}, []), "ok")
        payload = post_ai_json.call_args.args[2]
        self.assertEqual(payload["messages"][0]["role"], "system")
        self.assertIn("global-stock-data", payload["messages"][0]["content"])
        self.assertIn("分钟 K 线", payload["messages"][0]["content"])
        self.assertIn("当前监控数据", payload["messages"][1]["content"])
        self.assertEqual(post_ai_json.call_count, 2)

    def test_parse_json_object_extracts_json_from_text(self):
        self.assertEqual(parse_json_object('```json\n{"use_tool": true}\n```'), {"use_tool": True})

    def test_global_stock_symbol_normalization(self):
        self.assertEqual(normalize_symbol("07709")["yahoo"], "7709.HK")
        self.assertEqual(normalize_symbol("00700.HK")["code"], "00700")
        self.assertEqual(normalize_symbol("MU")["kind"], "us")

    def test_intraday_swing_summary_counts_direction_changes(self):
        rows = [
            {"date": "2026-06-29 09:30", "open": 100, "high": 101, "low": 99, "close": 100},
            {"date": "2026-06-29 09:35", "open": 100, "high": 106, "low": 100, "close": 105},
            {"date": "2026-06-29 09:40", "open": 105, "high": 105, "low": 96, "close": 97},
            {"date": "2026-06-29 09:45", "open": 97, "high": 103, "low": 97, "close": 102},
        ]
        summary = intraday_swing_summary(rows, threshold_pct=1)
        self.assertEqual(summary["high_pct_from_open"], 6.0)
        self.assertEqual(summary["low_pct_from_open"], -4.0)
        self.assertEqual(summary["swing_count"], 3)

    def test_global_stock_context_skips_unrelated_questions(self):
        self.assertIsNone(build_global_stock_context("现在能买吗？", {"symbol": "07709"}))

    def test_today_stock_summary_uses_daily_context_not_intraday_swings(self):
        plan = default_global_stock_plan("总结今天07709的股票行情", {"symbol": "07709"})

        self.assertEqual(plan["symbols"], ["07709"])
        self.assertEqual(plan["interval"], "1d")
        self.assertEqual(plan["range"], "6mo")
        self.assertIn("indicators", plan["data"])
        self.assertNotIn("intraday_swings", plan["data"])

    def test_intraday_swing_question_uses_five_minute_context(self):
        plan = default_global_stock_plan("07709今天盘中一共几波涨跌？", {"symbol": "07709"})

        self.assertEqual(plan["interval"], "5m")
        self.assertIn("intraday_swings", plan["data"])

    @patch("best_buy_app.web.dashboard_server.stock_quote", return_value={"price": 1145.28, "open": 1100, "high": 1160, "low": 1088})
    @patch("best_buy_app.web.dashboard_server.stock_kline", return_value={
        "source": "yahoo_chart",
        "symbol": "MU",
        "interval": "5m",
        "range": "5d",
        "rows": [
            {"date": "2026-06-29 09:30", "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1},
            {"date": "2026-06-29 09:35", "open": 100, "high": 106, "low": 100, "close": 105, "volume": 1},
            {"date": "2026-06-29 09:40", "open": 105, "high": 105, "low": 96, "close": 97, "volume": 1},
        ],
    })
    def test_global_stock_context_fetches_data_for_stock_questions(self, stock_kline, stock_quote):
        ctx = build_global_stock_context("MU 昨天美股盘中一共几波涨跌？", {"symbol": "07709"})
        self.assertTrue(ctx["triggered"])
        self.assertIn("MU", ctx["symbols"])
        self.assertIn("intraday_swing_summary", ctx["symbols"]["MU"])
        stock_quote.assert_any_call("MU")
        stock_kline.assert_any_call("MU", interval="5m", range_="5d", limit=96)

    @patch("best_buy_app.web.dashboard_server.stock_quote", return_value={"price": 1145.28})
    @patch("best_buy_app.web.dashboard_server.stock_kline", return_value={"source": "yahoo_chart", "symbol": "MU", "interval": "5m", "range": "5d", "rows": []})
    @patch("best_buy_app.web.dashboard_server.post_ai_json", side_effect=[
        {"choices": [{"message": {"content": '{"global_stock_data": {"use_tool": true, "symbols": ["MU"], "data": ["quote", "kline"], "interval": "5m", "range": "5d"}, "news_aggregator": {"use_tool": false}}'}}]},
        {"choices": [{"message": {"content": "ok"}}]},
    ])
    def test_external_ai_payload_includes_enriched_global_stock_context(self, post_ai_json, stock_kline, stock_quote):
        cfg = {"ai": {"provider": "openai_compatible", "endpoint": "https://example.test/v1"}}
        self.assertEqual(call_external_ai(cfg, "MU 昨天行情怎么样？", {"symbol": "07709"}, []), "ok")
        payload = post_ai_json.call_args.args[2]
        self.assertIn("global_stock_data", payload["messages"][1]["content"])
        self.assertIn("MU", payload["messages"][1]["content"])

    @patch("best_buy_app.web.dashboard_server.stock_quote")
    @patch("best_buy_app.web.dashboard_server.stock_kline")
    @patch("best_buy_app.web.dashboard_server.post_ai_json", side_effect=[
        {"choices": [{"message": {"content": '{"global_stock_data": {"use_tool": false}, "news_aggregator": {"use_tool": false}}'}}]},
        {"choices": [{"message": {"content": "ok"}}]},
    ])
    def test_ai_tool_decision_can_skip_stock_fetch(self, post_ai_json, stock_kline, stock_quote):
        cfg = {"ai": {"provider": "openai_compatible", "endpoint": "https://example.test/v1"}}
        self.assertEqual(call_external_ai(cfg, "现在能买吗？", {"symbol": "07709"}, []), "ok")
        stock_quote.assert_not_called()
        stock_kline.assert_not_called()
        payload = post_ai_json.call_args.args[2]
        self.assertNotIn("global_stock_data", payload["messages"][1]["content"])

    def test_news_rss_parser_and_source_groups(self):
        xml = """<?xml version="1.0"?>
        <rss><channel><item><title>AI market update</title><link>https://example.com/a</link><pubDate>Tue, 30 Jun 2026 08:00:00 GMT</pubDate><description>LLM news</description></item></channel></rss>
        """
        items = parse_rss_content(xml, "Example", 5)
        self.assertEqual(items[0]["title"], "AI market update")
        self.assertIn("bbc_world", source_keys_for_request("international"))
        self.assertEqual(len(filter_items(items, "AI")), 1)
        self.assertEqual(filter_items(items, "quantum"), [])

    @patch("best_buy_app.web.dashboard_server.fetch_news", return_value={
        "skill": "news-aggregator-skill",
        "sources": ["ai"],
        "keyword": "AI",
        "limit": 3,
        "items": [{"source": "AIHOT", "title": "AI 新品发布", "url": "https://example.com", "time": "Today", "summary": "摘要"}],
        "errors": [],
    })
    def test_news_context_fetches_news_for_news_questions(self, fetch_news_mock):
        ctx = build_news_context("查 3 条 AI 新闻")
        self.assertTrue(ctx["triggered"])
        self.assertEqual(ctx["items"][0]["title"], "AI 新品发布")
        fetch_news_mock.assert_called_once_with(["ai"], limit=3, keyword="AI")

    @patch("best_buy_app.web.dashboard_server.fetch_news", return_value={
        "skill": "news-aggregator-skill",
        "sources": ["finance"],
        "keyword": "美股",
        "limit": 5,
        "items": [{"source": "Reuters", "title": "Markets move", "url": "https://example.com", "time": "Today", "summary": ""}],
        "errors": [],
    })
    @patch("best_buy_app.web.dashboard_server.stock_quote")
    @patch("best_buy_app.web.dashboard_server.stock_kline")
    @patch("best_buy_app.web.dashboard_server.post_ai_json", side_effect=[
        {"choices": [{"message": {"content": '{"global_stock_data": {"use_tool": false}, "news_aggregator": {"use_tool": true, "sources": ["finance"], "keyword": "美股", "limit": 5}}'}}]},
        {"choices": [{"message": {"content": "ok"}}]},
    ])
    def test_external_ai_payload_includes_news_context(self, post_ai_json, stock_kline, stock_quote, fetch_news_mock):
        cfg = {"ai": {"provider": "openai_compatible", "endpoint": "https://example.test/v1"}}
        self.assertEqual(call_external_ai(cfg, "查一下美股相关新闻", {"symbol": "07709"}, []), "ok")
        payload = post_ai_json.call_args.args[2]
        self.assertIn("news_aggregator", payload["messages"][1]["content"])
        self.assertIn("Markets move", payload["messages"][1]["content"])
        stock_quote.assert_not_called()
        stock_kline.assert_not_called()

    def test_compact_ai_context_keeps_only_relevant_fields(self):
        ctx = compact_ai_context({
            "symbol": "07709",
            "price": 149.05,
            "zone": "中性",
            "action": "继续观察",
            "main": {
                "buy": {"verdict": "等待"},
                "supports": [{"price": 139}, {"price": 136}, {"price": 130}, {"price": 120}],
                "peers": [{"symbol": "A"}, {"symbol": "B"}, {"symbol": "C"}, {"symbol": "D"}, {"symbol": "E"}, {"symbol": "F"}],
            },
            "history": [{"too": "large"}],
        })
        self.assertEqual(ctx["symbol"], "07709")
        self.assertEqual(len(ctx["supports"]), 3)
        self.assertEqual(len(ctx["peers"]), 5)
        self.assertNotIn("history", ctx)


class TestStorage(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self.db_path = self._tmp.name
        self.cfg = {"runtime": {"db_file": self.db_path}}
        storage.init_db(self.cfg)

    def tearDown(self):
        import os
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(self.db_path + suffix)
            except OSError:
                pass

    def _feed(self, symbol="07709", zone="中性", action="继续观察", alerts=None):
        return {
            "time": "2026-06-30 10:00:00",
            "session": "intraday",
            "symbol": symbol,
            "price": 149.0,
            "zone": zone,
            "action": action,
            "confirm_score": 2,
            "buy_score": 1,
            "sell_score": 0,
            "momentum": {"verdict": "无动量", "active": False},
            "premomentum": {"verdict": "无预动量", "active": False},
            "alerts": alerts or [],
        }

    def test_record_tick_and_latest_feed(self):
        feed = self._feed()
        storage.record_watch_tick(self.cfg, feed, prev=None)
        latest = storage.get_latest_feed(self.cfg)
        self.assertEqual(latest["symbol"], "07709")
        self.assertEqual(latest["zone"], "中性")

    def test_watch_ticks_history_ordered(self):
        storage.record_watch_tick(self.cfg, self._feed(), prev=None)
        storage.record_watch_tick(self.cfg, self._feed(zone="买点区"), prev={"zone": "中性", "action": "继续观察"})
        ticks = storage.list_watch_ticks(self.cfg, "07709", limit=10)
        self.assertEqual(len(ticks), 2)
        self.assertEqual(ticks[0]["zone"], "买点区")  # 倒序：最新在前

    def test_decision_events_on_zone_change_and_alert(self):
        prev = {"zone": "中性", "action": "继续观察"}
        storage.record_watch_tick(self.cfg, self._feed(zone="买点区", action="可分批买入", alerts=["RSI 跌破30"]), prev=prev)
        events = storage.list_events(self.cfg, "07709", limit=20)
        types = [e["event_type"] for e in events]
        self.assertIn("zone_change", types)
        self.assertIn("action_change", types)
        self.assertIn("alert", types)

    @patch("best_buy_app.data.storage.load_rows", return_value={"meta": {}, "rows": [{"close": 100}]})
    def test_load_rows_cached_hits_cache(self, load_rows_mock):
        storage.load_rows_cached(self.cfg, "07709", "3mo", ttl_seconds=60)
        storage.load_rows_cached(self.cfg, "07709", "3mo", ttl_seconds=60)
        self.assertEqual(load_rows_mock.call_count, 1)  # 第二次命中缓存

    @patch("best_buy_app.data.storage.load_rows", return_value={"meta": {}, "rows": [{"close": 100}]})
    def test_load_rows_cached_refetches_after_ttl(self, load_rows_mock):
        storage.load_rows_cached(self.cfg, "07709", "3mo", ttl_seconds=0)
        storage.load_rows_cached(self.cfg, "07709", "3mo", ttl_seconds=0)
        self.assertEqual(load_rows_mock.call_count, 2)  # TTL=0 视为过期

    def test_watchlist_add_list_remove(self):
        storage.add_watch(self.cfg, "07709", label="力成", note="测试")
        storage.add_watch(self.cfg, "07709")  # 重复 add 不报错，更新
        items = storage.list_watchlist(self.cfg)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["symbol"], "07709")
        self.assertTrue(storage.remove_watch(self.cfg, "07709"))
        self.assertEqual(storage.list_watchlist(self.cfg), [])


class TestDashboardStorageRoutes(unittest.TestCase):
    def setUp(self):
        import tempfile
        from http.server import ThreadingHTTPServer
        from best_buy_app.web.dashboard_server import DashboardHandler
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self.db_path = self._tmp.name
        self.cfg = {"runtime": {"db_file": self.db_path}, "watch": {"quote_refresh_seconds": 15}}
        storage.init_db(self.cfg)
        DashboardHandler.cfg = self.cfg
        self.DashboardHandler = DashboardHandler
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), DashboardHandler)
        self.port = self.server.server_address[1]
        import threading
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        import os
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(self.db_path + suffix)
            except OSError:
                pass

    def _get(self, path):
        from urllib.request import urlopen
        with urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))

    def _request(self, path, method, body=None):
        from urllib.request import Request, urlopen
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = Request(f"http://127.0.0.1:{self.port}{path}", data=data, method=method,
                      headers={"Content-Type": "application/json"})
        with urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))

    def test_latest_feed_and_watchlist_routes(self):
        # 初始无 feed
        status, feed = self._get("/best_buy_feed.json")
        self.assertEqual(status, 200)
        self.assertEqual(feed, {})
        # 写入一个 tick 后可读取
        storage.record_watch_tick(self.cfg, {
            "time": "2026-06-30 10:00:00", "session": "intraday", "symbol": "07709",
            "price": 149.0, "zone": "中性", "action": "继续观察",
            "confirm_score": 2, "buy_score": 1, "sell_score": 0,
            "momentum": {"verdict": "无", "active": False},
            "premomentum": {"verdict": "无", "active": False}, "alerts": [],
        }, prev=None)
        _, feed = self._get("/best_buy_feed.json")
        self.assertEqual(feed["symbol"], "07709")
        # watchlist 增删查
        _, body = self._request("/api/watchlist", "POST", {"symbol": "07709", "label": "力成"})
        self.assertEqual(body["item"]["symbol"], "07709")
        _, body = self._get("/api/watchlist")
        self.assertEqual(len(body["items"]), 1)
        _, body = self._request("/api/watchlist?symbol=07709", "DELETE")
        self.assertEqual(body["items"], [])
        # history / events
        _, body = self._get("/api/history?symbol=07709")
        self.assertEqual(body["items"][0]["symbol"], "07709")
        _, body = self._get("/api/events?symbol=07709")
        self.assertIsInstance(body["items"], list)



if __name__ == "__main__":
    unittest.main()

import json
import time
import unittest
from unittest.mock import patch

from best_buy_app.core.decision_engine import buy_decision, buy_decision_mtfa, classify_zone, confirmation_score, final_action, final_action_with_momentum, momentum_decision, premomentum_decision, render_watch_tick, sell_decision, short_term_plan, trade_plan
from best_buy_app.core.decision_rules import analysis_change_pct, apply_sentiment_adjustments, attach_relative_strength, sentiment_adjustments
from best_buy_app.core.indicators import analyze, analyze_timeframes, atr, fib_retrace, vwap
from best_buy_app.core.market_hours import intraday_progress
from best_buy_app.cli.best_buy import build_intraday_ctx, parse_leveraged
from best_buy_app.web.dashboard_server import AI_INSTRUCTIONS, TOOL_DECIDER, DashboardHandler, build_global_stock_context, build_news_context, build_responses_input, build_stock_feed, call_external_ai, compact_ai_context, default_global_stock_plan, enrich_feed_for_ai, local_reply, minimal_responses_payload, parse_json_object, parse_responses_text, parse_stream_text_delta
from best_buy_app.data import storage
from best_buy_app.data.global_stock_data import intraday_swing_summary, normalize_symbol, stock_quote
from best_buy_app.data.market_data import fetch_quote
from best_buy_app.data.news_aggregator import filter_items, parse_rss_content, source_keys_for_request


class TestBestBuy(unittest.TestCase):
    def test_dashboard_ticker_hud_shows_price_zone_action_only(self):
        with open("dashboard/index.html", "r", encoding="utf-8") as f:
            html = f.read()
        with open("dashboard/app.js", "r", encoding="utf-8") as f:
            script = f.read()

        ticker_html = html.split('<section class="ticker-strip">', 1)[1].split("</section>", 1)[0]

        self.assertIn('id="price"', ticker_html)
        self.assertIn('id="zone"', ticker_html)
        self.assertIn('id="action"', ticker_html)
        self.assertIn('id="meta"', ticker_html)
        self.assertNotIn('id="volRatioHud"', ticker_html)
        self.assertNotIn('id="confirm"', ticker_html)
        self.assertNotIn("量比", ticker_html)
        self.assertNotIn("确认分", ticker_html)
        self.assertNotIn("volRatioHud", script)

    def test_dashboard_short_panel_uses_execution_map_layout(self):
        with open("dashboard/index.html", "r", encoding="utf-8") as f:
            html = f.read()
        with open("dashboard/app.js", "r", encoding="utf-8") as f:
            script = f.read()
        with open("dashboard/styles.css", "r", encoding="utf-8") as f:
            css = f.read()

        short_html = html.split('<section class="panel short-panel">', 1)[1].split('<section class="panel remote-level-strip"', 1)[0]
        remote_html = html.split('<section class="panel remote-level-strip"', 1)[1].split("</section>", 1)[0]

        self.assertIn("execution-map-hud", short_html)
        self.assertIn("exec-col", short_html)
        self.assertIn("理想买位", short_html)
        self.assertIn("目标阻力", short_html)
        self.assertIn('id="shortEntries"', short_html)
        self.assertIn('id="shortExits"', short_html)
        self.assertNotIn('id="buyLevels"', short_html)
        self.assertNotIn('id="sellLevels"', short_html)
        self.assertIn('id="shortStop"', short_html)
        self.assertIn("short-risk-band", short_html)
        self.assertIn('id="remoteSupports"', remote_html)
        self.assertIn('id="remoteExits"', remote_html)
        self.assertNotIn("short-grid", short_html)
        self.assertNotIn("pill-list", short_html)
        self.assertNotIn("hudLiveStopPill", script)
        self.assertNotIn("nano-pill", script)
        self.assertIn("renderExecutionRows", script)
        self.assertIn("price-pill", script)
        self.assertIn("grid-template-columns: minmax(0, 1fr) minmax(0, 1fr)", css)
        self.assertNotIn("background: #0c111f", css)

    def test_dashboard_position_hud_uses_stop_to_price_life_bar(self):
        with open("dashboard/index.html", "r", encoding="utf-8") as f:
            html = f.read()
        with open("dashboard/app.js", "r", encoding="utf-8") as f:
            script = f.read()
        with open("dashboard/styles.css", "r", encoding="utf-8") as f:
            css = f.read()

        hud_html = html.split('<section class="panel position-blood-hud"', 1)[1].split("</section>", 1)[0]

        self.assertIn("虚拟纸面持仓安全线", hud_html)
        self.assertIn('id="hudStopPrice"', hud_html)
        self.assertIn('id="hudCurrentPrice"', hud_html)
        self.assertIn('id="stopLossBloodBar"', hud_html)
        self.assertIn("life-bar-track", hud_html)
        self.assertNotIn("hudPositionStatus", hud_html)
        self.assertNotIn("hudEntryPeak", hud_html)
        self.assertNotIn("hudStopDistance", hud_html)
        self.assertNotIn("开仓状态", hud_html)
        self.assertNotIn("成本 / 最高", hud_html)
        self.assertIn("hudCurrentPrice", script)
        self.assertIn("updateLifeBar", script)
        self.assertIn("life-bar-fill", css)

    def test_dashboard_position_hud_appears_before_short_panel(self):
        with open("dashboard/index.html", "r", encoding="utf-8") as f:
            html = f.read()

        self.assertLess(
            html.index('class="panel position-blood-hud"'),
            html.index('class="panel short-panel"'),
        )

    def test_dashboard_decision_panel_uses_factor_audit_layout(self):
        with open("dashboard/index.html", "r", encoding="utf-8") as f:
            html = f.read()
        with open("dashboard/app.js", "r", encoding="utf-8") as f:
            script = f.read()
        with open("dashboard/styles.css", "r", encoding="utf-8") as f:
            css = f.read()

        decision_html = html.split('<article class="panel decision-panel">', 1)[1].split("</article>", 1)[0]

        self.assertIn("因子得分拆解", decision_html)
        self.assertIn("decision-audit-panel", decision_html)
        self.assertIn("买入因子", decision_html)
        self.assertIn("卖出因子", decision_html)
        self.assertIn("趋势确认", decision_html)
        self.assertIn("跨境联动", decision_html)
        self.assertIn("盘中动量", decision_html)
        self.assertIn("战术操盘审计备注", decision_html)
        self.assertIn('id="auditBuyScore"', decision_html)
        self.assertIn('id="auditNote"', decision_html)
        self.assertNotIn("decision-list", decision_html)
        self.assertIn("renderAuditScore", script)
        self.assertIn("decision-audit-grid", css)

    def test_dashboard_decision_panel_is_not_collapsible(self):
        with open("dashboard/index.html", "r", encoding="utf-8") as f:
            html = f.read()
        with open("dashboard/app.js", "r", encoding="utf-8") as f:
            script = f.read()

        self.assertNotIn('<details class="panel decision-panel compact-details">', html)
        self.assertNotIn("<summary>", html.split("decision-panel", 1)[1].split("</article>", 1)[0])
        self.assertNotIn('id="decisionSummary"', html)
        self.assertNotIn('id="updatedAt"', html)
        self.assertNotIn("核心判断", html.split("decision-panel", 1)[1].split("</article>", 1)[0])
        self.assertIn("<h2>因子得分拆解</h2>", html)
        self.assertNotIn("updatedAt", script)
        self.assertNotIn("decisionSummary", script)

    def test_dashboard_command_strip_uses_rule_engine_above_position_hud(self):
        with open("dashboard/index.html", "r", encoding="utf-8") as f:
            html = f.read()
        with open("dashboard/app.js", "r", encoding="utf-8") as f:
            script = f.read()
        with open("dashboard/styles.css", "r", encoding="utf-8") as f:
            css = f.read()

        self.assertIn("decision-command-strip", html)
        self.assertIn("核心决策指挥横条", html)
        self.assertIn('id="commandText"', html)
        self.assertLess(
            html.index("decision-command-strip"),
            html.index("position-blood-hud"),
        )
        self.assertIn("function commandDecision(data)", script)
        self.assertIn("commandText", script)
        self.assertIn("momentum.active", script)
        self.assertIn("premomentum.active", script)
        self.assertIn("decision-command-strip", css)
        self.assertNotIn("COMMAND_AI_URL", script)

    def test_dashboard_remote_levels_strip_appears_after_short_panel(self):
        with open("dashboard/index.html", "r", encoding="utf-8") as f:
            html = f.read()
        with open("dashboard/app.js", "r", encoding="utf-8") as f:
            script = f.read()
        with open("dashboard/styles.css", "r", encoding="utf-8") as f:
            css = f.read()

        self.assertIn("remote-level-strip", html)
        self.assertIn("远端备用支撑", html)
        self.assertIn("远端分批止盈", html)
        self.assertIn('id="remoteSupports"', html)
        self.assertIn('id="remoteExits"', html)
        self.assertLess(
            html.index("short-panel"),
            html.index("remote-level-strip"),
        )
        self.assertIn("remoteSupports", script)
        self.assertIn("remoteExits", script)
        self.assertIn("remote-level-strip", css)

    def test_dashboard_deep_support_panel_appears_before_factor_audit_with_uniform_spacing(self):
        with open("dashboard/index.html", "r", encoding="utf-8") as f:
            html = f.read()
        with open("dashboard/styles.css", "r", encoding="utf-8") as f:
            css = f.read()

        self.assertLess(
            html.index("<h2>深回撤备用</h2>"),
            html.index("<h2>因子得分拆解</h2>"),
        )
        self.assertIn("--section-gap: 12px", css)
        self.assertIn(".monitor-stack { display: grid; gap: var(--section-gap); }", css)
        self.assertIn(".grid-main { display: grid; grid-template-columns: .9fr 1.35fr; gap: var(--section-gap); }", css)

    def test_dashboard_stock_tabs_support_long_press_reorder(self):
        with open("dashboard/app.js", "r", encoding="utf-8") as f:
            script = f.read()
        with open("dashboard/styles.css", "r", encoding="utf-8") as f:
            css = f.read()

        self.assertIn("const STOCK_TAB_LONG_PRESS_MS = 450", script)
        self.assertIn('aria-grabbed="false"', script)
        self.assertIn("startStockTabDrag", script)
        self.assertIn("moveStockTabDrag", script)
        self.assertIn("finishStockTabDrag", script)
        self.assertIn("reorderStockTabs", script)
        self.assertIn("saveStockTabs();", script)
        self.assertIn(".stock-tab.dragging", css)
        self.assertIn("touch-action: none", css)
        self.assertIn("-webkit-touch-callout: none", css)
        self.assertIn("cursor: grab", css)
        self.assertIn("btn.setPointerCapture(event.pointerId)", script)
        self.assertIn("event.preventDefault();", script)

    def test_parse_leveraged(self):
        self.assertEqual(parse_leveraged("07709:tencent@ratio=2"), {"symbol": "07709", "source": "tencent", "ratio": 2.0})

    def test_fetch_quote_treats_plain_numeric_symbol_as_hk_quote(self):
        with patch("best_buy_app.data.market_data.hk_quote_tencent") as tencent, \
             patch("best_buy_app.data.market_data.hk_quote_sina", return_value={"source": "sina", "price": 150.25}) as sina, \
             patch("best_buy_app.data.market_data.yahoo_chart") as yahoo:
            quote = fetch_quote("07709")

        self.assertEqual(quote["source"], "sina")
        self.assertEqual(quote["price"], 150.25)
        sina.assert_called_once_with("07709")
        tencent.assert_not_called()
        yahoo.assert_not_called()

    def test_fetch_quote_prefers_sina_for_hk_quote_even_when_tencent_is_available(self):
        with patch("best_buy_app.data.market_data.hk_quote_sina", return_value={"source": "sina", "price": 150.25}), \
             patch("best_buy_app.data.market_data.hk_quote_tencent", return_value={"source": "tencent", "price": 149.05}) as tencent:
            started_at = time.monotonic()
            quote = fetch_quote("07709")
            elapsed = time.monotonic() - started_at

        self.assertEqual(quote["source"], "sina")
        self.assertEqual(quote["price"], 150.25)
        self.assertLess(elapsed, 0.3)
        tencent.assert_not_called()

    def test_fetch_quote_falls_back_to_tencent_when_sina_has_no_hk_price(self):
        with patch("best_buy_app.data.market_data.hk_quote_sina", return_value={}), \
             patch("best_buy_app.data.market_data.hk_quote_tencent", return_value={"source": "tencent", "price": 149.05}) as tencent:
            quote = fetch_quote("07709.HK")

        self.assertEqual(quote["source"], "tencent")
        self.assertEqual(quote["price"], 149.05)
        tencent.assert_called_once_with("07709")

    def test_stock_quote_prefers_sina_for_hk_quote(self):
        with patch("best_buy_app.data.global_stock_data.hk_stock_quote_sina", return_value={"source": "sina", "price": 150.25}), \
             patch("best_buy_app.data.global_stock_data.hk_stock_quote_tencent", return_value={"source": "tencent", "price": 149.05}) as tencent:
            quote = stock_quote("07709")

        self.assertEqual(quote["source"], "sina")
        self.assertEqual(quote["price"], 150.25)
        self.assertEqual(quote["market"], "hk")
        tencent.assert_not_called()

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

    def test_confirmation_score_rewards_market_relative_strength(self):
        main = {"close": 95, "ma": {20: 100}, "rsi14": 40, "label": "main", "change_pct": -1.0}
        market = {"close": 280, "ma": {20: 300}, "rsi14": 40, "label": "market", "change_pct": -4.0}

        res = confirmation_score(main, [], market, {
            "strategy": {"confirmation": {"rs_market_outperform_pct": 2.0}}
        })

        self.assertEqual(res["score"], 1)
        self.assertIn("相对强弱", " ".join(s[1] for s in res["signals"]))
        self.assertEqual(res["relative_strength"]["market_alpha_pct"], 3.0)

    def test_confirmation_score_rewards_peer_relative_strength(self):
        main = {"close": 95, "ma": {20: 100}, "rsi14": 40, "label": "main", "change_pct": 1.0}
        peers = [
            {"close": 90, "ma": {20: 100}, "rsi14": 40, "label": "peer-a", "change_pct": -2.0},
            {"close": 92, "ma": {20: 100}, "rsi14": 40, "label": "peer-b", "change_pct": -1.0},
        ]

        res = confirmation_score(main, peers, None, {
            "strategy": {"confirmation": {"rs_peer_outperform_pct": 2.0}}
        })

        self.assertEqual(res["score"], 1)
        self.assertEqual(res["relative_strength"]["peer_alpha_pct"], 2.5)

    def test_analysis_change_pct_uses_explicit_quote_change(self):
        self.assertEqual(analysis_change_pct({"change_pct": 2.5}), 2.5)

    def test_analysis_change_pct_falls_back_to_recent_closes(self):
        analysis = {
            "recent": [
                {"date": "2026-01-01", "close": 100},
                {"date": "2026-01-02", "close": 104},
                {"date": "2026-01-03", "close": 106.08},
            ]
        }

        self.assertEqual(analysis_change_pct(analysis), 2.0)

    def test_analysis_change_pct_returns_none_without_enough_data(self):
        self.assertIsNone(analysis_change_pct({"recent": [{"close": 100}]}))

    def test_attach_relative_strength_returns_analysis_copy(self):
        analysis = {"close": 100}
        confirm = {"relative_strength": {"market_alpha_pct": 2.5}}

        enriched = attach_relative_strength(analysis, confirm)

        self.assertEqual(enriched["relative_strength"], {"market_alpha_pct": 2.5})
        self.assertNotIn("relative_strength", analysis)

    def test_default_strategy_relaxes_bottom_entry_and_limits_chasing(self):
        with open("config.json", "r", encoding="utf-8") as f:
            cfg = json.load(f)

        st = cfg["strategy"]
        self.assertEqual(st["buy_zone_buffer"], 0.035)
        self.assertEqual(st["rsi_buy"], 35)
        self.assertEqual(st["kdj_buy_j"], 10)
        self.assertEqual(st["momentum"]["chase_limit_pct"], 2.5)
        self.assertEqual(st["volatility_adaptive"], {
            "enabled": True,
            "atr_period": 14,
            "buy_zone_buffer_atr_multiplier": 1.0,
            "buy_zone_max_width_atr_multiplier": 1.0,
            "min_buffer_pct": 0.01,
            "max_buffer_pct": 0.06,
        })
        self.assertEqual(st["relative_strength"], {
            "support_bonus_alpha_pct": 2.0,
            "support_near_pct": 0.005,
        })
        self.assertEqual(st["confirmation"]["rs_market_outperform_pct"], 2.0)
        self.assertEqual(st["confirmation"]["rs_peer_outperform_pct"], 2.0)
        self.assertEqual(st["mtfa"], {
            "enabled": True,
            "intraday_rsi_buy": 30,
            "bonus_score": 2,
        })
        self.assertEqual(st["momentum"]["vwap_volume_ratio_min"], 1.2)
        self.assertEqual(st["momentum"]["intraday_rebound_limit_pct"], 3.5)
        self.assertEqual(st["short_term"]["trailing_stop_atr_multiplier"], 2.0)
        self.assertEqual(st["sentiment_alpha"], {
            "enabled": True,
            "positive_score": 3,
            "negative_score": -3,
            "rsi_buy_relax": 5,
            "chase_limit_tighten_pct": 1.0,
            "decay_hours": 12,
            "decay_floor": 0.5,
        })

    def test_fib_retrace_defaults_to_sixty_bars(self):
        rows = []
        for i in range(70):
            rows.append({"high": 100 + i, "low": 90 + i, "close": 95 + i})
        rows[15]["low"] = 40
        rows[5]["low"] = 10

        hi, lo, _ = fib_retrace(rows)

        self.assertEqual(hi, 169)
        self.assertEqual(lo, 40)

    def test_atr_uses_true_range_over_period(self):
        rows = [
            {"high": 10, "low": 8, "close": 9},
            {"high": 13, "low": 11, "close": 12},
            {"high": 12, "low": 7, "close": 8},
        ]

        self.assertEqual(atr(rows, 2), 4.5)

    def test_analyze_returns_atr_percent(self):
        rows = []
        for i in range(15):
            rows.append({
                "date": f"2026-01-{i + 1:02d}",
                "open": 100,
                "high": 103,
                "low": 97,
                "close": 100,
                "volume": 100,
            })

        res = analyze(rows, "main")

        self.assertEqual(res["atr14"], 6)
        self.assertEqual(res["atr14_pct"], 0.06)

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

        # vol_5 剔除未收盘当根 → 5 根全 100 → 100；量比=50/100=0.5
        self.assertEqual(res["volume_ratio"], 0.5)

    def test_intraday_progress_handles_hk_morning_and_lunch(self):
        from zoneinfo import ZoneInfo
        from datetime import datetime as _dt
        hk = ZoneInfo("Asia/Hong_Kong")
        morning = intraday_progress("hk", _dt(2026, 7, 2, 11, 0, tzinfo=hk))
        self.assertTrue(morning["is_open"])
        self.assertFalse(morning["paused"])
        self.assertEqual(morning["elapsed_minutes"], 90)
        self.assertEqual(morning["total_minutes"], 330)
        # 港股午休 12:00-13:00 视为未开盘，elapsed 保留上午整段 150 分钟；paused=True 保持投影连续
        lunch = intraday_progress("hk", _dt(2026, 7, 2, 12, 30, tzinfo=hk))
        self.assertFalse(lunch["is_open"])
        self.assertTrue(lunch["paused"])
        self.assertEqual(lunch["elapsed_minutes"], 150)
        self.assertAlmostEqual(lunch["progress"], 150 / 330, places=4)

    def test_intraday_progress_paused_false_after_close_and_premarket(self):
        from zoneinfo import ZoneInfo
        from datetime import datetime as _dt
        hk = ZoneInfo("Asia/Hong_Kong")
        after_close = intraday_progress("hk", _dt(2026, 7, 2, 16, 30, tzinfo=hk))
        self.assertFalse(after_close["is_open"])
        self.assertFalse(after_close["paused"])
        pre_market = intraday_progress("hk", _dt(2026, 7, 2, 9, 0, tzinfo=hk))
        self.assertFalse(pre_market["is_open"])
        self.assertFalse(pre_market["paused"])

    def test_build_intraday_ctx_honors_paused_with_frozen_progress(self):
        from zoneinfo import ZoneInfo
        from datetime import datetime as _dt
        hk = ZoneInfo("Asia/Hong_Kong")
        lunch_now = _dt(2026, 7, 2, 12, 30, tzinfo=hk)
        original = intraday_progress

        def fake(market, now=None):
            return original(market, lunch_now)

        with patch("best_buy_app.cli.best_buy.intraday_progress", side_effect=fake):
            ctx = build_intraday_ctx("hk", {"enabled": True})
        self.assertIsNotNone(ctx)
        self.assertTrue(ctx["is_open"])
        self.assertTrue(ctx["paused"])
        self.assertAlmostEqual(ctx["progress"], 150 / 330, places=4)

    def test_build_intraday_ctx_none_after_close(self):
        from zoneinfo import ZoneInfo
        from datetime import datetime as _dt
        hk = ZoneInfo("Asia/Hong_Kong")
        after_close = _dt(2026, 7, 2, 16, 30, tzinfo=hk)
        original = intraday_progress

        def fake(market, now=None):
            return original(market, after_close)

        with patch("best_buy_app.cli.best_buy.intraday_progress", side_effect=fake):
            ctx = build_intraday_ctx("hk", {"enabled": True})
        self.assertIsNone(ctx)

    def test_intraday_progress_us_morning(self):
        from zoneinfo import ZoneInfo
        from datetime import datetime as _dt
        ny = ZoneInfo("America/New_York")
        prog = intraday_progress("us", _dt(2026, 7, 2, 10, 0, tzinfo=ny))
        self.assertTrue(prog["is_open"])
        self.assertEqual(prog["elapsed_minutes"], 30)
        self.assertEqual(prog["total_minutes"], 390)

    def test_analyze_volume_projection_morning_is_conservative(self):
        rows = []
        for i in range(61):
            rows.append({"date": f"2026-01-{(i % 28) + 1:02d}", "open": 100, "high": 102, "low": 99, "close": 101, "volume": 100})
        rows[-1]["volume"] = 10  # 当日上午仅成交 10
        ctx = {
            "is_open": True, "progress": 30 / 390, "elapsed_minutes": 30, "total_minutes": 390,
            "morning_cutoff_progress": 0.5, "morning_deflation": 0.7, "max_ratio": 3.0,
        }
        res = analyze(rows, "main", intraday_ctx=ctx)
        # vol_5 剔除未收盘当根 → rows[-6:-1] 5 根全 100 → 100；投影全天量=10/(30/390)*0.7=91.0；量比=91/100=0.91
        # 纯线性(无保守化)应为 130/100=1.30，上午保守化显著低于线性
        self.assertEqual(res["volume_ratio"], 0.91)
        self.assertEqual(res["volume_projected"], 91.0)

    def test_analyze_volume_projection_clamps_to_max_ratio(self):
        rows = []
        for i in range(61):
            rows.append({"date": f"2026-01-{(i % 28) + 1:02d}", "open": 100, "high": 102, "low": 99, "close": 101, "volume": 100})
        rows[-1]["volume"] = 50
        ctx = {
            "is_open": True, "progress": 30 / 390, "elapsed_minutes": 30, "total_minutes": 390,
            "morning_cutoff_progress": 0.5, "morning_deflation": 0.7, "max_ratio": 3.0,
        }
        res = analyze(rows, "main", intraday_ctx=ctx)
        # 50/(30/390)*0.7/100 = 4.55 → 封顶 3.0
        self.assertEqual(res["volume_ratio"], 3.0)

    def test_analyze_vol5_excludes_inprogress_bar(self):
        # 当根 volume=10（盘中未收盘），前 5 根全 100；vol_5 应=100，不被当根摊薄
        rows = []
        for i in range(61):
            rows.append({"date": f"2026-01-{(i % 28) + 1:02d}", "open": 100, "high": 102, "low": 99, "close": 101, "volume": 100})
        rows[-1]["volume"] = 10
        res = analyze(rows, "main", intraday_ctx={"is_open": False, "progress": 0})
        # 量比 = 10 / 100 = 0.10；若误把当根算进 vol_5 会得 10/82≈0.12
        self.assertEqual(res["volume_ratio"], 0.10)

    def test_analyze_volume_projection_disabled_when_closed(self):
        rows = []
        for i in range(61):
            rows.append({"date": f"2026-01-{(i % 28) + 1:02d}", "open": 100, "high": 102, "low": 99, "close": 101, "volume": 100})
        rows[-1]["volume"] = 10
        res = analyze(rows, "main", intraday_ctx={"is_open": False, "progress": 0})
        # 收盘后不投影，用真实量；vol_5 剔除当根 → 5 根全 100 → 100；10/100=0.10
        self.assertEqual(res["volume_ratio"], 0.10)
        self.assertIsNone(res["volume_projected"])

    def test_analyze_timeframes_returns_keyed_analyses(self):
        rows_by_tf = {
            "1d": [
                {"date": f"2026-01-{i + 1:02d}", "open": 100 + i, "high": 102 + i, "low": 99 + i, "close": 101 + i, "volume": 100}
                for i in range(61)
            ],
            "15m": [
                {"date": f"2026-01-01 10:{i:02d}", "open": 100, "high": 102, "low": 99, "close": 101, "volume": 100}
                for i in range(30)
            ],
        }

        analyses = analyze_timeframes(rows_by_tf, "main")

        self.assertEqual(set(analyses), {"1d", "15m"})
        self.assertEqual(analyses["1d"]["timeframe"], "1d")
        self.assertEqual(analyses["15m"]["label"], "main:15m")

    def test_buy_decision_mtfa_rewards_daily_uptrend_and_intraday_oversold(self):
        daily = {
            "close": 110,
            "ma": {5: 108, 10: 104, 20: 100},
            "rsi14": 55,
            "kdj": {"j": 50, "k": 50, "d": 45, "cross": "金叉"},
            "macd": {"hist": 1, "shortening": False},
            "candle": {},
            "boll": {"upper": 120, "lower": 95},
            "supports": [{"level": "MA20", "price": 100}],
            "resistances": [],
        }
        intraday = dict(daily)
        intraday.update({
            "close": 105,
            "ma": {5: 104, 10: 103, 20: 102},
            "rsi14": 28,
            "kdj": {"j": 15, "k": 30, "d": 35, "cross": "死叉"},
        })

        res = buy_decision_mtfa({"1d": daily, "15m": intraday})

        self.assertGreaterEqual(res["score"], 3)
        self.assertIn("多周期共振", " ".join(s[1] for s in res["signals"]))

    def test_buy_decision_mtfa_falls_back_to_daily_decision(self):
        daily = {
            "close": 100,
            "ma": {20: 100},
            "rsi14": 25,
            "kdj": {"j": -5, "k": 20, "d": 30, "cross": "金叉"},
            "macd": {"hist": -1, "shortening": True},
            "candle": {},
            "boll": {"upper": 110, "lower": 90},
            "supports": [],
            "resistances": [],
        }

        self.assertEqual(buy_decision_mtfa({"1d": daily})["score"], buy_decision(daily)["score"])

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

    def test_buy_decision_rewards_positive_relative_strength_near_support(self):
        analysis = {
            "close": 100,
            "ma": {20: 100},
            "rsi14": 50,
            "kdj": {"j": 50, "k": 50, "d": 55, "cross": "死叉"},
            "macd": {"hist": 1, "shortening": False},
            "candle": {},
            "boll": {"upper": 110, "lower": 90},
            "supports": [{"level": "S1", "price": 99.8, "dist_pct": -0.2}],
            "resistances": [{"level": "R1", "price": 105, "dist_pct": 5.0}],
            "relative_strength": {"market_alpha_pct": 2.4, "peer_alpha_pct": 1.0},
        }

        res = buy_decision(analysis, {
            "strategy": {"relative_strength": {"support_bonus_alpha_pct": 2.0, "support_near_pct": 0.005}}
        })

        self.assertEqual(res["score"], 2)
        self.assertIn("相对强弱", " ".join(s[1] for s in res["signals"]))

    def test_buy_decision_keeps_missing_relative_strength_neutral(self):
        analysis = {
            "close": 100,
            "ma": {20: 100},
            "rsi14": 50,
            "kdj": {"j": 50, "k": 50, "d": 55, "cross": "死叉"},
            "macd": {"hist": 1, "shortening": False},
            "candle": {},
            "boll": {"upper": 110, "lower": 90},
            "supports": [{"level": "S1", "price": 99.8, "dist_pct": -0.2}],
            "resistances": [{"level": "R1", "price": 105, "dist_pct": 5.0}],
        }

        res = buy_decision(analysis)

        self.assertEqual(res["score"], 1)
        self.assertNotIn("相对强弱", " ".join(s[1] for s in res["signals"]))

    def test_positive_sentiment_relaxes_rsi_buy_threshold(self):
        cfg = {"strategy": {"rsi_buy": 30, "sentiment_alpha": {"positive_score": 3, "rsi_buy_relax": 5}}}
        adjusted = apply_sentiment_adjustments(cfg, {"score": 4})

        self.assertEqual(adjusted["strategy"]["rsi_buy"], 35)

    def test_buy_decision_uses_positive_sentiment_to_relax_rsi_threshold(self):
        analysis = {
            "close": 100,
            "ma": {20: 100},
            "rsi14": 34,
            "kdj": {"j": 50, "k": 50, "d": 55, "cross": "死叉"},
            "macd": {"hist": 1, "shortening": False},
            "candle": {},
            "boll": {"upper": 110, "lower": 90},
            "supports": [],
            "resistances": [],
            "sentiment": {"score": 4},
        }

        res = buy_decision(analysis, {
            "strategy": {"rsi_buy": 30, "sentiment_alpha": {"positive_score": 3, "rsi_buy_relax": 5}}
        })

        self.assertIn("RSI14=34 超卖", " ".join(s[1] for s in res["signals"]))

    def test_sentiment_adjustments_tighten_momentum_on_negative_score(self):
        adj = sentiment_adjustments({"score": -4}, {"negative_score": -3, "chase_limit_tighten_pct": 1.0})

        self.assertEqual(adj["momentum_chase_limit_delta"], -1.0)

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

    def test_trade_plan_no_position_ignores_swing_high_for_stop(self):
        # 核心踩踏场景：标的从 160 跌到 145，swing_high=160 仍在 60 周期窗内。
        # 未持仓时绝不能用 swing_high 算吊灯止损，否则 stop=160-3*2=154 > 现价145，开仓即割肉。
        # 必须回退到支撑位止损：supports[0]=139*0.985=136.915 → 136.92。
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
            "swing_high": 160,
            "recent": [
                {"date": "2026-01-01", "close": 144, "high": 146, "low": 140},
                {"date": "2026-01-02", "close": 145, "high": 150, "low": 143},
            ],
        }

        plan = trade_plan(analysis, {"verdict": "ok", "score": 2})

        self.assertEqual(plan["stop_loss"], 136.91)
        self.assertLess(plan["stop_loss"], analysis["close"])
        self.assertNotEqual(plan["stop_loss"], 154)

    def test_trade_plan_trailing_stop_uses_position_highest_not_swing_high(self):
        # 持仓后吊灯止损必须基于 position_highest（持仓以来最高），而非历史 swing_high。
        # atr_like=(152-140)/4=3；position_highest=155、swing_high=160（须被忽略）。
        # 默认乘数 2.0：155-3*2=149；supports 止损 136.92，取 149。
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
            "swing_high": 160,
            "recent": [
                {"date": "2026-01-01", "close": 144, "high": 146, "low": 140},
                {"date": "2026-01-02", "close": 145, "high": 150, "low": 143},
            ],
        }

        plan = trade_plan(analysis, {"verdict": "ok", "score": 2}, position_highest=155)

        self.assertEqual(plan["stop_loss"], 149)
        self.assertEqual(plan["position_highest"], 155)

    def test_trade_plan_trailing_stop_reads_config_multiplier(self):
        # atr_like=(152-140)/4=3, position_highest=150；乘数 3.0 → 150-3*3=141
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
        cfg = {"strategy": {"short_term": {"trailing_stop_atr_multiplier": 3.0}}}

        plan = trade_plan(analysis, {"verdict": "ok", "score": 2}, cfg, position_highest=150)

        self.assertEqual(plan["stop_loss"], 141)

    def test_trade_plan_position_highest_ratchets_stop_up(self):
        # 持仓峰值上移，吊灯止损随之抬高（锁盈）；不传 position_highest 则退回静态止损。
        base = {
            "close": 150,
            "ma": {10: 148, 20: 145, 60: 130},
            "rsi14": 55,
            "kdj": {"j": 60, "k": 60, "d": 55, "cross": "金叉"},
            "macd": {"hist": 1, "shortening": False},
            "candle": {},
            "boll": {"upper": 156, "lower": 144},
            "supports": [{"level": "S1", "price": 145, "dist_pct": -3.3}],
            "resistances": [{"level": "R1", "price": 156, "dist_pct": 4.0}],
        }
        # atr_like=(156-144)/4=3；position_highest=152 → 152-6=146；position_highest=158 → 158-6=152
        low_plan = trade_plan(dict(base), {"verdict": "ok", "score": 2}, position_highest=152)
        high_plan = trade_plan(dict(base), {"verdict": "ok", "score": 2}, position_highest=158)
        self.assertEqual(low_plan["stop_loss"], 146)
        self.assertEqual(high_plan["stop_loss"], 152)
        self.assertGreater(high_plan["stop_loss"], low_plan["stop_loss"])

    def test_trade_plan_narrows_buy_zone_when_atr_is_low(self):
        analysis = {
            "close": 100,
            "ma": {10: 100, 20: 98, 60: 90},
            "rsi14": 25,
            "kdj": {"j": -5, "k": 20, "d": 30, "cross": "金叉"},
            "macd": {"hist": -1, "shortening": True},
            "candle": {},
            "boll": {"upper": 110, "lower": 90},
            "supports": [{"level": "S1", "price": 100, "dist_pct": 0.0}],
            "resistances": [{"level": "R1", "price": 105, "dist_pct": 5.0}],
            "atr14_pct": 0.012,
        }
        cfg = {"strategy": {"buy_zone_buffer": 0.035, "buy_zone_max_width_pct": 0.035}}

        plan = trade_plan(analysis, {"verdict": "ok", "score": 2}, cfg)

        self.assertEqual(plan["buy_zone"], {"low": 98.8, "high": 101.2})

    def test_trade_plan_widens_buy_zone_when_atr_is_high_with_cap(self):
        analysis = {
            "close": 100,
            "ma": {10: 100, 20: 98, 60: 90},
            "rsi14": 25,
            "kdj": {"j": -5, "k": 20, "d": 30, "cross": "金叉"},
            "macd": {"hist": -1, "shortening": True},
            "candle": {},
            "boll": {"upper": 120, "lower": 80},
            "supports": [{"level": "S1", "price": 100, "dist_pct": 0.0}],
            "resistances": [{"level": "R1", "price": 105, "dist_pct": 5.0}],
            "atr14_pct": 0.08,
        }
        cfg = {"strategy": {"buy_zone_buffer": 0.035, "buy_zone_max_width_pct": 0.035}}

        plan = trade_plan(analysis, {"verdict": "ok", "score": 2}, cfg)

        self.assertEqual(plan["buy_zone"], {"low": 94.0, "high": 106.0})

    def test_trade_plan_falls_back_to_static_zone_when_atr_is_missing(self):
        analysis = {
            "close": 100,
            "ma": {10: 100, 20: 98, 60: 90},
            "rsi14": 25,
            "kdj": {"j": -5, "k": 20, "d": 30, "cross": "金叉"},
            "macd": {"hist": -1, "shortening": True},
            "candle": {},
            "boll": {"upper": 110, "lower": 90},
            "supports": [{"level": "S1", "price": 100, "dist_pct": 0.0}],
            "resistances": [{"level": "R1", "price": 105, "dist_pct": 5.0}],
            "atr14_pct": None,
        }
        cfg = {"strategy": {"buy_zone_buffer": 0.035, "buy_zone_max_width_pct": 0.035}}

        plan = trade_plan(analysis, {"verdict": "ok", "score": 2}, cfg)

        self.assertEqual(plan["buy_zone"], {"low": 98.25, "high": 101.75})

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

    def test_vwap_prefers_amount_when_available(self):
        rows = [
            {"high": 11, "low": 9, "close": 10, "volume": 100, "amount": 1000},
            {"high": 22, "low": 18, "close": 20, "volume": 100, "amount": 2200},
        ]

        self.assertEqual(vwap(rows), 16.0)

    def test_vwap_estimates_from_typical_price_without_amount(self):
        rows = [
            {"high": 12, "low": 9, "close": 9, "volume": 100},
            {"high": 21, "low": 18, "close": 21, "volume": 100},
        ]

        self.assertEqual(vwap(rows), 15.0)

    def test_momentum_decision_blocks_jump_below_vwap(self):
        history = [
            {"time": "13:20:10", "price": 100.0},
            {"time": "13:21:25", "price": 104.0},
        ]
        analysis = {
            "resistances": [{"level": "R1", "price": 103, "dist_pct": -1.0}],
            "intraday": {"vwap": 105.0},
            "volume_ratio": 1.5,
        }

        res = momentum_decision(history, analysis, {"score": 4}, {"score": 1}, {"strategy": {"momentum": {"chase_limit_pct": 10}}})

        self.assertFalse(res["active"])
        self.assertIn("VWAP", " ".join(s[1] for s in res["signals"]))

    def test_momentum_decision_rewards_above_vwap_with_volume(self):
        history = [
            {"time": "13:20:10", "price": 100.0},
            {"time": "13:21:25", "price": 103.2},
        ]
        analysis = {
            "resistances": [{"level": "R1", "price": 103, "dist_pct": -0.2}],
            "intraday": {"vwap": 101.0},
            "volume_ratio": 1.5,
        }

        res = momentum_decision(history, analysis, {"score": 4}, {"score": 1}, {"strategy": {"momentum": {"chase_limit_pct": 10}}})

        self.assertTrue(res["active"])
        self.assertIn("站上VWAP", " ".join(s[1] for s in res["signals"]))

    def test_momentum_decision_tightens_chase_limit_on_negative_sentiment(self):
        history = [
            {"time": "13:20:10", "price": 100.0},
            {"time": "13:21:25", "price": 102.2},
        ]
        analysis = {
            "resistances": [{"level": "R1", "price": 102, "dist_pct": -0.2}],
            "sentiment": {"score": -4},
        }
        cfg = {
            "strategy": {
                "momentum": {"trigger_pct": 2.0, "chase_limit_pct": 3.0},
                "sentiment_alpha": {"negative_score": -3, "chase_limit_tighten_pct": 1.0},
            }
        }

        res = momentum_decision(history, analysis, {"score": 4}, {"score": 1}, cfg)

        self.assertFalse(res["active"])
        self.assertTrue(res["extended"])

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

    def test_momentum_decision_detects_rebound_from_morning_low_outside_window(self):
        # 早盘探底 140（tick 0-9），随后 20+ 根回升到 150。20-tick 微窗看不到 140，
        # 旧逻辑会漏判；新逻辑扫描全日队列应被"日内低点"拦截。
        history = [{"time": f"09:{30 + i // 60:02d}:{i % 60:02d}", "price": 140.0} for i in range(10)]
        history += [{"time": f"14:{i:02d}:00", "price": 145.0 + i * 0.2} for i in range(25)]
        history[-1]["price"] = 150.0
        analysis = {"resistances": [{"level": "R1", "price": 149, "dist_pct": -0.7}]}
        res = momentum_decision(history, analysis, {"score": 4}, {"score": 1}, {"strategy": {"momentum": {"chase_limit_pct": 10}}})

        self.assertFalse(res["active"])
        self.assertIn("日内低点", " ".join(s[1] for s in res["signals"]))

    def test_momentum_rebound_limit_reads_config(self):
        # 反弹 4%：默认阈值 3.5 会拦截；将阈值上调到 5.0 后应放行
        history = [
            {"time": "13:20:10", "price": 100.0},
            {"time": "13:21:25", "price": 104.0},
        ]
        analysis = {
            "resistances": [{"level": "R1", "price": 103, "dist_pct": -1.0}],
            "volume_ratio": 1.5,
        }
        cfg = {"strategy": {"momentum": {"chase_limit_pct": 10, "intraday_rebound_limit_pct": 5.0}}}
        res = momentum_decision(history, analysis, {"score": 4}, {"score": 1}, cfg)

        self.assertNotIn("日内低点", " ".join(s[1] for s in res["signals"]))
        self.assertTrue(res["active"])

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

    @patch("best_buy_app.web.dashboard_server.storage.get_latest_sentiment", return_value={"score": 4, "summary": "利好"})
    @patch("best_buy_app.web.dashboard_server.stock_kline")
    @patch("best_buy_app.web.dashboard_server.load_related_snapshots", return_value=[])
    @patch("best_buy_app.web.dashboard_server.load_symbol_snapshot")
    def test_build_stock_feed_includes_mtfa_vwap_and_sentiment(self, load_snapshot, load_related, stock_kline, latest_sentiment):
        DashboardHandler.cfg = {
            "strategy": {
                "mtfa": {"enabled": True, "intraday_rsi_buy": 30, "bonus_score": 2},
                "sentiment_alpha": {"enabled": True, "positive_score": 3, "rsi_buy_relax": 5},
            },
            "defaults": {"peers": [], "market": []},
            "watch": {},
        }
        base_analysis = {
            "label": "07709",
            "close": 110,
            "ma": {5: 108, 10: 104, 20: 100},
            "rsi14": 55,
            "kdj": {"j": 50, "k": 50, "d": 45, "cross": "金叉"},
            "macd": {"hist": 1, "shortening": False},
            "candle": {},
            "boll": {"upper": 120, "lower": 95},
            "supports": [{"level": "MA20", "price": 100}],
            "resistances": [],
            "recent": [{"date": "2026-01-01", "close": 108, "high": 111, "low": 107}],
        }
        load_snapshot.return_value = {"symbol": "07709", "quote": {"price": 110}, "rows": [], "analysis": base_analysis}

        def fake_stock_kline(_symbol, interval="1d", range_="5d", limit=None):
            if interval == "5m":
                return {"rows": [
                    {"date": "2026-01-01 10:00", "open": 100, "high": 102, "low": 99, "close": 101, "volume": 100},
                    {"date": "2026-01-01 10:05", "open": 101, "high": 103, "low": 100, "close": 102, "volume": 100},
                ]}
            return {"rows": [
                {"date": f"2026-01-01 10:{i:02d}", "open": 100, "high": 102, "low": 99, "close": 101, "volume": 100}
                for i in range(30)
            ]}

        stock_kline.side_effect = fake_stock_kline

        feed = build_stock_feed("07709", DashboardHandler.cfg)

        self.assertIn("15m", feed["main"]["analyses"])
        self.assertEqual(feed["main"]["intraday"]["rows"], 2)
        self.assertEqual(feed["main"]["sentiment"]["score"], 4)

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

    def test_ai_instructions_constants_integration(self):
        self.assertIn("buy_score", AI_INSTRUCTIONS)
        self.assertIn("confirm_score", AI_INSTRUCTIONS)
        self.assertIn("short_stop", AI_INSTRUCTIONS)
        self.assertIn("全球半导体与存储芯片板块", AI_INSTRUCTIONS)
        self.assertIn("volume_ratio", AI_INSTRUCTIONS)

        self.assertIn("global_stock_data", TOOL_DECIDER)
        self.assertIn("news_aggregator", TOOL_DECIDER)
        self.assertIn("intraday_swings", TOOL_DECIDER)
        self.assertIn("use_tool", TOOL_DECIDER)

    def test_openai_responses_payload_matches_proxy_shape(self):
        payload = minimal_responses_payload("gpt-5.5", "hello")
        self.assertEqual(payload, {"model": "gpt-5.5", "input": "hello"})
        text = build_responses_input("现在能买吗", '{"symbol":"07709"}')
        self.assertIn("全球半导体与存储芯片板块", text)
        self.assertIn("用户问题：现在能买吗", text)
        self.assertIn("量化交易员", text)
        self.assertIn("short_stop", text)

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
        self.assertIn("顶级量化交易员", payload["messages"][0]["content"])
        self.assertIn("buy_score", payload["messages"][0]["content"])
        self.assertIn("short_stop", payload["messages"][0]["content"])
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

    def test_position_open_get_close_roundtrip(self):
        self.assertIsNone(storage.get_position(self.cfg, "07709"))
        storage.open_position(self.cfg, "07709", 149.0)
        pos = storage.get_position(self.cfg, "07709")
        self.assertIsNotNone(pos)
        self.assertEqual(pos["entry_price"], 149.0)
        self.assertEqual(pos["highest_since_entry"], 149.0)
        self.assertTrue(storage.close_position(self.cfg, "07709"))
        self.assertIsNone(storage.get_position(self.cfg, "07709"))

    def test_position_peak_is_monotonic_non_decreasing(self):
        storage.open_position(self.cfg, "07709", 149.0)
        updated = storage.update_position_peak(self.cfg, "07709", 152.0)
        self.assertEqual(updated["highest_since_entry"], 152.0)
        # 回落的新价不得下移峰值
        updated = storage.update_position_peak(self.cfg, "07709", 150.5)
        self.assertEqual(updated["highest_since_entry"], 152.0)
        # entry_price 不被峰值更新改动
        self.assertEqual(updated["entry_price"], 149.0)

    def test_position_peak_update_returns_none_when_no_position(self):
        self.assertIsNone(storage.update_position_peak(self.cfg, "07709", 150.0))

    def test_open_position_does_not_clobber_existing_without_force(self):
        storage.open_position(self.cfg, "07709", 149.0)
        storage.update_position_peak(self.cfg, "07709", 155.0)
        # 无 force：保留既有 entry 与峰值，不被新价覆盖
        storage.open_position(self.cfg, "07709", 160.0)
        pos = storage.get_position(self.cfg, "07709")
        self.assertEqual(pos["entry_price"], 149.0)
        self.assertEqual(pos["highest_since_entry"], 155.0)

    def test_open_position_force_overwrites_for_external_sync(self):
        storage.open_position(self.cfg, "07709", 149.0)
        # 实盘对接：外部真实持仓强制覆写虚拟仓位
        storage.open_position(self.cfg, "07709", 158.0, force=True)
        pos = storage.get_position(self.cfg, "07709")
        self.assertEqual(pos["entry_price"], 158.0)
        self.assertEqual(pos["highest_since_entry"], 158.0)

    def test_close_position_returns_false_when_absent(self):
        self.assertFalse(storage.close_position(self.cfg, "07709"))

    def test_sentiment_score_round_trip_returns_latest(self):
        storage.record_sentiment(self.cfg, "07709", 2, "利好订单", [{"title": "news1"}])
        storage.record_sentiment(self.cfg, "07709", -4, "重大利空", [{"title": "news2"}])

        latest = storage.get_latest_sentiment(self.cfg, "07709")

        self.assertEqual(latest["symbol"], "07709")
        self.assertEqual(latest["score"], -4)
        self.assertEqual(latest["raw_score"], -4)
        self.assertEqual(latest["effective_score"], -4)  # 刚入库未衰减
        self.assertFalse(latest["decayed"])
        self.assertEqual(latest["summary"], "重大利空")
        self.assertEqual(latest["items"], [{"title": "news2"}])

    def test_sentiment_decays_after_halflife(self):
        storage.record_sentiment(self.cfg, "07709", -4, "重大利空")
        with patch("best_buy_app.data.storage.time.time", return_value=time.time() + 12 * 3600):
            latest = storage.get_latest_sentiment(self.cfg, "07709")
        # -4 * 0.5^(12/12) = -2.0；|−2|>=0.5 未截断
        self.assertEqual(latest["raw_score"], -4)
        self.assertEqual(latest["score"], -4)
        self.assertAlmostEqual(latest["effective_score"], -2.0, places=2)
        self.assertFalse(latest["decayed"])

    def test_sentiment_truncates_below_floor(self):
        storage.record_sentiment(self.cfg, "07709", 1, "微弱利好")
        with patch("best_buy_app.data.storage.time.time", return_value=time.time() + 48 * 3600):
            latest = storage.get_latest_sentiment(self.cfg, "07709")
        # 1 * 0.5^(48/12) = 0.0625 < 0.5 → 截断归零
        self.assertEqual(latest["effective_score"], 0)
        self.assertTrue(latest["decayed"])
        self.assertEqual(latest["raw_score"], 1)

    def test_sentiment_adjustments_uses_effective_score_when_present(self):
        # effective_score 衰减到 1.5 后不再触发 positive_score=3 的放宽
        adj = sentiment_adjustments({"score": 4, "effective_score": 1.5}, {"positive_score": 3, "rsi_buy_relax": 5})
        self.assertEqual(adj["rsi_buy_delta"], 0)


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

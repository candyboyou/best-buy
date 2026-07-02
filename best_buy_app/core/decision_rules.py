#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from best_buy_app.core.common_utils import fmt_num


def _strategy(cfg=None):
    return (cfg or {}).get("strategy", {})


def _symbol_key(text):
    return str(text or "").upper()


def _group_for_symbol(symbol, label, groups):
    haystack = f"{symbol or ''} {label or ''}".upper()
    for group, names in (groups or {}).items():
        for name in names:
            if str(name).upper() in haystack:
                return group
    return "other"


def _history_pct(history, lookback):
    points = [x for x in (history or []) if x.get("price") is not None]
    if len(points) < 2:
        return None
    window = points[-max(2, lookback):]
    base = window[0]["price"]
    current = window[-1]["price"]
    if not base:
        return None
    return (current / base - 1) * 100


def analysis_change_pct(analysis):
    if not analysis or "error" in analysis:
        return None
    if analysis.get("change_pct") is not None:
        return analysis.get("change_pct")
    recent = [r for r in analysis.get("recent", []) if r.get("close") is not None]
    if len(recent) < 2:
        return None
    base = recent[-2]["close"]
    current = recent[-1]["close"]
    if not base:
        return None
    return round((current / base - 1) * 100, 2)


def attach_relative_strength(analysis, confirmation):
    if not analysis:
        return analysis
    enriched = dict(analysis)
    rs = (confirmation or {}).get("relative_strength")
    if rs:
        enriched["relative_strength"] = rs
    return enriched


def sentiment_adjustments(sentiment, cfg=None):
    cfg = cfg or {}
    raw = sentiment or {}
    # 优先用衰减后的 effective_score（来自 storage 层指数衰减），缺失则回退原始 score
    score = raw.get("effective_score")
    if score is None:
        score = raw.get("score")
    adjustments = {"rsi_buy_delta": 0, "momentum_chase_limit_delta": 0}
    if score is None:
        return adjustments
    if score >= cfg.get("positive_score", 3):
        adjustments["rsi_buy_delta"] = cfg.get("rsi_buy_relax", 5)
    if score <= cfg.get("negative_score", -3):
        adjustments["momentum_chase_limit_delta"] = -abs(cfg.get("chase_limit_tighten_pct", 1.0))
    return adjustments


def apply_sentiment_adjustments(cfg, sentiment):
    if not cfg:
        cfg = {}
    adjusted = {
        key: (dict(value) if isinstance(value, dict) else value)
        for key, value in cfg.items()
    }
    strategy = dict(adjusted.get("strategy", {}))
    sentiment_cfg = strategy.get("sentiment_alpha", {})
    adj = sentiment_adjustments(sentiment, sentiment_cfg)
    if adj["rsi_buy_delta"]:
        strategy["rsi_buy"] = strategy.get("rsi_buy", 30) + adj["rsi_buy_delta"]
    if adj["momentum_chase_limit_delta"]:
        momentum = dict(strategy.get("momentum", {}))
        momentum["chase_limit_pct"] = max(
            0,
            momentum.get("chase_limit_pct", 4.0) + adj["momentum_chase_limit_delta"],
        )
        strategy["momentum"] = momentum
    adjusted["strategy"] = strategy
    return adjusted


def _empty_premomentum(verdict, active=False):
    return {
        "signals": [],
        "score": 0,
        "main_pct": None,
        "group_scores": {},
        "verdict": verdict,
        "active": active,
    }


def _clamp(value, min_value=None, max_value=None):
    if min_value is not None:
        value = max(value, min_value)
    if max_value is not None:
        value = min(value, max_value)
    return value


def _adaptive_zone_settings(analysis, st):
    zone_buffer = st.get("buy_zone_buffer", st.get("buy_ma_tolerance", 0.02))
    max_zone_width = st.get("buy_zone_max_width_pct", 0.035)
    adaptive = st.get("volatility_adaptive", {})
    atr_period = adaptive.get("atr_period", 14)
    atr_pct = (analysis or {}).get(f"atr{atr_period}_pct")
    if atr_pct is None and atr_period == 14:
        atr_pct = (analysis or {}).get("atr14_pct")
    if not adaptive.get("enabled", True) or atr_pct is None:
        return zone_buffer, max_zone_width

    min_buffer = adaptive.get("min_buffer_pct", 0.01)
    max_buffer = adaptive.get("max_buffer_pct", 0.06)
    zone_buffer = _clamp(
        atr_pct * adaptive.get("buy_zone_buffer_atr_multiplier", 1.0),
        min_buffer,
        max_buffer,
    )
    max_zone_width = _clamp(
        atr_pct * adaptive.get("buy_zone_max_width_atr_multiplier", 1.0),
        min_buffer,
        max_buffer,
    ) * 2
    return zone_buffer, max_zone_width


def buy_decision(a, cfg=None):
    if "error" in a:
        return {"signals": [], "score": 0, "verdict": "数据不足"}
    cfg = apply_sentiment_adjustments(cfg, a.get("sentiment"))
    st = _strategy(cfg)
    rs_cfg = st.get("relative_strength", {})
    sigs = []
    rsi_v = a["rsi14"]
    j = a["kdj"]["j"]
    hist = a["macd"]["hist"]
    shortening = a["macd"]["shortening"]
    close = a["close"]
    kdj_cross = a["kdj"]["cross"]
    rsi_buy = st.get("rsi_buy", 30)
    kdj_buy_j = st.get("kdj_buy_j", 0)
    ma_tolerance = st.get("buy_ma_tolerance", 0.02)
    buy_ma = st.get("buy_ma", 20)
    target_ma = a["ma"].get(buy_ma) if a.get("ma") else None
    score_adjust = 0
    if rsi_v is not None and rsi_v < rsi_buy:
        sigs.append(("✅", f"RSI14={rsi_v} 超卖"))
    else:
        sigs.append(("❌", f"RSI14={rsi_v} 未超卖(需<{rsi_buy})"))
    if j < kdj_buy_j:
        sigs.append(("✅", f"KDJ J={j} 极端超卖"))
    else:
        sigs.append(("❌", f"KDJ J={j} 未到极值(需<{kdj_buy_j})"))
    if hist is not None and hist < 0 and shortening:
        sigs.append(("✅", "MACD 绿柱缩短"))
    else:
        sigs.append(("❌", f"MACD {'仍是红柱' if hist and hist > 0 else '未现绿柱缩短'}"))
    if target_ma and abs(close - target_ma) / target_ma <= ma_tolerance:
        sigs.append(("✅", f"价格回到MA{buy_ma}支撑区({target_ma})"))
    elif target_ma and close > target_ma * (1 + ma_tolerance):
        sigs.append(("❌", f"仍在MA{buy_ma}上方{(close/target_ma-1)*100:.1f}%，未到支撑"))
    else:
        sigs.append(("➖", f"价格在MA{buy_ma}附近"))
    if kdj_cross == "金叉":
        sigs.append(("✅", "KDJ 金叉上行"))
    else:
        sigs.append(("❌", "KDJ 死叉下行"))
    candle = a.get("candle", {})
    if candle.get("is_long_lower_shadow"):
        sigs.append(("✅", "日内出现长下影线，资金承接强(探底反转)"))
    else:
        sigs.append(("➖", "无明显底部反转K线形态"))

    rs = a.get("relative_strength") or {}
    rs_alpha_values = [
        v for v in (rs.get("market_alpha_pct"), rs.get("peer_alpha_pct"))
        if v is not None
    ]
    best_alpha = max(rs_alpha_values) if rs_alpha_values else None
    first_support = (a.get("supports") or [None])[0]
    support_near_pct = rs_cfg.get("support_near_pct", 0.005)
    near_support = bool(
        first_support and close and first_support.get("price")
        and close <= first_support["price"] * (1 + support_near_pct)
    )
    if best_alpha is not None and near_support and best_alpha >= rs_cfg.get("support_bonus_alpha_pct", 2.0):
        sigs.append(("✅", f"相对强弱为正({best_alpha:+.1f}%)，弱市中守住支撑"))

    first_res = (a.get("resistances") or [None])[0]
    broke_resistance = bool(first_res and close >= first_res.get("price", close + 1) * 0.998)
    if broke_resistance and a.get("volume_ratio", 1) < 0.8:
        score_adjust -= 1
        sigs.append(("❌", f"缩量反弹/突破，量比={a.get('volume_ratio')}，削弱买入可信度"))

    score = max(0, sum(1 for s in sigs if s[0] == "✅") + score_adjust)
    verdict = (
        f"可考虑分批轻仓试探（{score}/6 信号达标）"
        if score >= 2
        else ("信号不足，继续等待" if score == 1 else "无买入信号，暂不建议追多")
    )
    return {"signals": sigs, "score": score, "verdict": verdict}


def buy_decision_mtfa(analyses, cfg=None):
    if not isinstance(analyses, dict):
        return buy_decision(analyses, cfg)
    daily = analyses.get("1d") or analyses.get("daily")
    if not daily:
        return {"signals": [], "score": 0, "verdict": "数据不足"}
    result = buy_decision(daily, cfg)
    intraday = analyses.get("15m") or analyses.get("60m") or analyses.get("5m")
    if not intraday:
        return result

    mas = daily.get("ma", {})
    ma5, ma10, ma20 = mas.get(5), mas.get(10), mas.get(20)
    daily_bullish = bool(
        daily.get("close") and ma20 and daily["close"] >= ma20
        and (not ma5 or not ma10 or ma5 >= ma10 >= ma20)
    )
    intraday_rsi = intraday.get("rsi14")
    intraday_reversal = bool((intraday.get("candle") or {}).get("is_long_lower_shadow"))
    intraday_oversold = intraday_rsi is not None and intraday_rsi < _strategy(cfg).get("mtfa", {}).get("intraday_rsi_buy", 30)
    if daily_bullish and (intraday_oversold or intraday_reversal):
        enriched = dict(result)
        signals = list(result.get("signals", []))
        signals.append(("✅", "多周期共振：日线多头，短周期出现超卖/探底买点"))
        enriched["signals"] = signals
        enriched["score"] = result.get("score", 0) + _strategy(cfg).get("mtfa", {}).get("bonus_score", 2)
        enriched["verdict"] = f"多周期共振买点，可分批试探（{enriched['score']}分）"
        return enriched
    return result


def sell_decision(a, cfg=None):
    if "error" in a:
        return {"signals": [], "score": 0, "verdict": "数据不足"}
    st = _strategy(cfg)
    sigs = []
    rsi_v = a["rsi14"]
    j = a["kdj"]["j"]
    k = a["kdj"]["k"]
    d = a["kdj"]["d"]
    hist = a["macd"]["hist"]
    shortening = a["macd"]["shortening"]
    bu = a["boll"]["upper"]
    close = a["close"]
    candle = a["candle"]
    rsi_sell = st.get("rsi_sell", 70)
    kdj_sell_j = st.get("kdj_sell_j", 100)
    sell_boll_tolerance = st.get("sell_boll_tolerance", 0.98)
    mas = a.get("ma", {})
    ma5, ma10, ma20 = mas.get(5), mas.get(10), mas.get(20)
    is_strong_uptrend = bool(ma5 and ma10 and ma20 and (ma5 > ma10 > ma20))
    if rsi_v is not None and rsi_v > rsi_sell:
        if is_strong_uptrend:
            sigs.append(("➖", f"RSI14={rsi_v}超买，但处于强多头排列，忽略超买卖压"))
        else:
            sigs.append(("✅", f"RSI14={rsi_v} 超买"))
    else:
        sigs.append(("❌", f"RSI14={rsi_v} 未超买(需>{rsi_sell})"))
    if j > kdj_sell_j or (k > 80 and k < d):
        sigs.append(("✅", f"KDJ J={j} K={k} 高位死叉/极值"))
    else:
        sigs.append(("❌", f"KDJ J={j} 未到高位极值"))
    if hist is not None and hist < 0:
        sigs.append(("✅", "MACD 死叉(绿柱)"))
    elif shortening:
        sigs.append(("⚠️", "MACD 红柱缩短(接近死叉)"))
    else:
        sigs.append(("❌", "MACD 红柱未缩短"))
    if bu and close >= bu * sell_boll_tolerance:
        sigs.append(("✅", f"触及布林上轨({bu})"))
    else:
        sigs.append(("❌", f"未触及布林上轨({bu})" if bu else "布林数据不足"))
    if candle.get("is_long_upper_shadow") or candle.get("is_doji"):
        sigs.append(("✅", f"K线见顶形态({'长上影' if candle.get('is_long_upper_shadow') else '十字星'})"))
    else:
        sigs.append(("❌", "无见顶K线形态"))
    score = sum(1 for s in sigs if s[0] == "✅")
    if score >= 2:
        verdict = f"见顶信号明显，建议分批减仓（{score}/5）"
    elif score == 1 or any(s[0] == "⚠️" for s in sigs):
        verdict = "见顶信号初现，可减部分仓位或上移止盈"
    else:
        verdict = "无见顶信号，暂不卖出"
    return {"signals": sigs, "score": score, "verdict": verdict}


def momentum_decision(price_history, analysis, confirmation, sell=None, cfg=None):
    cfg = apply_sentiment_adjustments(cfg, (analysis or {}).get("sentiment"))
    st = _strategy(cfg).get("momentum", {})
    if not st.get("enabled", True):
        return {"signals": [], "score": 0, "pct": None, "verdict": "动量规则关闭", "active": False}
    history = [x for x in (price_history or []) if x.get("price") is not None]
    if len(history) < 2:
        return {"signals": [], "score": 0, "pct": None, "verdict": "动量数据不足", "active": False}

    lookback = max(1, st.get("lookback_ticks", 20))
    window = history[-lookback:]
    current = window[-1]["price"]
    base_item = min(window[:-1], key=lambda x: x["price"]) if len(window) > 1 else window[0]
    base = base_item["price"]
    pct = (current / base - 1) * 100 if base else 0
    # 日内最低用自开盘以来全量分时队列（CLI 跨日已清空，保证同日），而非 20-tick 微窗。
    # 否则早盘暴跌探底、午后暴力反转时，微窗只看到拉升前横盘，反弹幅度被严重低估。
    intraday_low = min(x["price"] for x in history)
    rebound_from_low = (current / intraday_low - 1) * 100 if intraday_low else 0
    trigger_pct = st.get("trigger_pct", 3.0)
    strong_pct = st.get("strong_pct", 5.0)
    chase_limit_pct = st.get("chase_limit_pct", 4.0)
    confirm_score = (confirmation or {}).get("score", 0)
    sell_score = (sell or {}).get("score", 0)
    confirm_min = st.get("confirm_min", 2)
    sell_block = st.get("sell_score_block", 3)
    intraday = (analysis or {}).get("intraday") or {}
    vwap_value = intraday.get("vwap")
    vwap_volume_min = st.get("vwap_volume_ratio_min", 1.2)

    signals = []
    score = 0
    rebound_limit = st.get("intraday_rebound_limit_pct", 3.5)
    blocked_by_rebound = rebound_from_low >= rebound_limit
    if blocked_by_rebound:
        score -= 2
        signals.append(("❌", f"距日内低点已反弹+{rebound_from_low:.1f}%，放弃追高动量"))
    if pct >= trigger_pct:
        score += 1
        signals.append(("✅", f"盘中动量+{pct:.1f}%"))
    else:
        signals.append(("❌", f"盘中动量不足({pct:+.1f}%，需+{trigger_pct:.1f}%)"))
    if confirm_score >= confirm_min:
        score += 1
        signals.append(("✅", f"确认层分数={confirm_score}"))
    else:
        signals.append(("❌", f"确认层分数={confirm_score}，不足{confirm_min}"))
    if sell_score < sell_block:
        score += 1
        signals.append(("✅", "未被强卖出信号拦截"))
    else:
        signals.append(("❌", f"卖出分={sell_score}，不适合追动量"))

    blocked_by_vwap = bool(vwap_value and current < vwap_value)
    if blocked_by_vwap:
        score -= 2
        signals.append(("❌", f"价格仍在VWAP下方({fmt_num(vwap_value)})，拦截诱多拉升"))
    elif vwap_value and current >= vwap_value and (analysis or {}).get("volume_ratio", 1) >= vwap_volume_min:
        score += 1
        signals.append(("✅", f"站上VWAP({fmt_num(vwap_value)})且量比={(analysis or {}).get('volume_ratio')}"))

    first_res = (analysis or {}).get("resistances", [None])[0]
    broke_resistance = bool(first_res and current >= first_res.get("price", current + 1) * 0.998)
    if broke_resistance:
        score += 1
        signals.append(("✅", f"触及/突破近端阻力{first_res['level']}={fmt_num(first_res['price'])}"))
    elif first_res:
        signals.append(("➖", f"未突破近端阻力{first_res['level']}={fmt_num(first_res['price'])}"))

    active = not blocked_by_rebound and not blocked_by_vwap and score >= 3 and trigger_pct <= pct < chase_limit_pct
    extended = blocked_by_rebound or (score >= 3 and pct >= chase_limit_pct)
    if extended:
        verdict = f"强动量已拉升，谨慎追高，等回踩或下一次预动量（+{pct:.1f}%）"
    elif active and pct >= strong_pct:
        verdict = f"强动量启动，可小仓跟随并严控止损（+{pct:.1f}%）"
    elif active:
        verdict = f"动量买入信号，可轻仓跟随（+{pct:.1f}%）"
    else:
        verdict = "无动量买入信号"
    return {"signals": signals, "score": score, "pct": round(pct, 2), "verdict": verdict, "active": active, "extended": extended, "base_price": base}


def premomentum_decision(main_history, peer_histories, market_history=None, cfg=None):
    st = _strategy(cfg)
    pm = st.get("premomentum", {})
    if not pm.get("enabled", True):
        return _empty_premomentum("预动量规则关闭")
    lookback = st.get("momentum", {}).get("lookback_ticks", 20)
    main_pct = _history_pct(main_history, lookback)
    if main_pct is None:
        return _empty_premomentum("预动量数据不足")

    groups = st.get("confirmation", {}).get("groups", {})
    if not groups:
        groups = {
            "underlying": ["000660.KS", "000660", "SK海力士"],
            "korea_semis": ["005930.KS", "005930", "07747", "三星电子"],
            "us_storage": ["DRAM", "MU", "MRVL", "SNDK", "SNXX", "SOXX", "SMH", "WDC", "STX"],
            "market": ["^KS11", ".KOSPI", "KOSPI"],
        }
    weights = pm.get("lead_weights") or {"underlying": 35, "korea_semis": 20, "us_storage": 30, "market": 15}
    trigger = pm.get("upstream_trigger_pct", 0.8)
    main_lag_max = pm.get("main_lag_max_pct", 1.5)
    grouped = {}
    for item in peer_histories or []:
        pct = _history_pct(item.get("history"), lookback)
        if pct is None:
            continue
        group = _group_for_symbol(item.get("symbol"), item.get("label"), groups)
        grouped.setdefault(group, []).append(pct)
    if market_history:
        pct = _history_pct(market_history.get("history"), lookback)
        if pct is not None:
            grouped.setdefault("market", []).append(pct)

    signals = []
    weighted = 0.0
    total_weight = 0.0
    group_scores = {}
    for group, weight in weights.items():
        pcts = grouped.get(group, [])
        if not pcts:
            group_scores[group] = {"pct": None, "score": 0, "weight": weight}
            signals.append(("➖", f"{group} 缺少短线数据"))
            continue
        avg_pct = sum(pcts) / len(pcts)
        if avg_pct >= trigger:
            group_score = 100
            signals.append(("✅", f"{group} 先动 +{avg_pct:.1f}%"))
        elif avg_pct > 0:
            group_score = 50
            signals.append(("⚠️", f"{group} 小幅走强 +{avg_pct:.1f}%"))
        else:
            group_score = 0
            signals.append(("❌", f"{group} 未走强 {avg_pct:+.1f}%"))
        weighted += group_score * weight
        total_weight += weight
        group_scores[group] = {"pct": round(avg_pct, 2), "score": group_score, "weight": weight}

    score = round(weighted / total_weight, 1) if total_weight else 0
    lagging = main_pct <= main_lag_max
    if lagging:
        signals.append(("✅", f"07709 尚未充分跟涨({main_pct:+.1f}%)"))
    else:
        signals.append(("❌", f"07709 已明显跟涨({main_pct:+.1f}%)，不是提前买点"))

    strong_score = pm.get("strong_score", 70)
    watch_score = pm.get("watch_score", 50)
    active = score >= strong_score and lagging
    if active:
        verdict = f"预动量强：上游先动，07709可能补涨（上游分={score}，07709={main_pct:+.1f}%）"
    elif score >= watch_score and lagging:
        verdict = f"预动量观察：上游偏强，等07709放量/突破（上游分={score}，07709={main_pct:+.1f}%）"
    elif score >= strong_score:
        verdict = f"上游强但07709已跟涨，避免追高（上游分={score}，07709={main_pct:+.1f}%）"
    else:
        verdict = "无预动量信号"
    return {
        "signals": signals,
        "score": score,
        "main_pct": round(main_pct, 2),
        "group_scores": group_scores,
        "verdict": verdict,
        "active": active,
    }


def confirmation_score(main, peers, market=None, cfg=None):
    signals = []
    score = 0
    if not main or "error" in main:
        return {"signals": [], "score": 0, "verdict": "主标的数据不足"}
    st = _strategy(cfg).get("confirmation", {})
    main_close = main.get("close")
    main_ma20 = main.get("ma", {}).get(20)
    main_rsi = main.get("rsi14")
    if main_close and main_ma20 and main_close >= main_ma20:
        score += 1
        signals.append(("✅", "主标的站上MA20"))
    else:
        signals.append(("❌", "主标的未站稳MA20"))
    if main_rsi is not None and st.get("main_rsi_min", 45) <= main_rsi <= st.get("main_rsi_max", 65):
        score += 1
        signals.append(("✅", f"主标的RSI={main_rsi} 处于健康区"))
    elif main_rsi is not None and main_rsi < st.get("main_rsi_min", 45) - 10:
        signals.append(("⚠️", f"主标的RSI={main_rsi} 偏弱"))
    else:
        signals.append(("❌", f"主标的RSI={main_rsi} 偏强或偏弱"))

    def score_group(items, label, peer_rsi_min=50):
        group_score = 0
        count = 0
        for peer in items or []:
            if not peer or "error" in peer:
                continue
            count += 1
            p_close = peer.get("close")
            p_ma20 = peer.get("ma", {}).get(20)
            p_rsi = peer.get("rsi14")
            if p_close and p_ma20 and p_close >= p_ma20:
                group_score += 1
            if p_rsi is not None and p_rsi >= peer_rsi_min:
                group_score += 1
            signals.append(("ℹ️", f"{label}:{peer['label']} 收盘={fmt_num(p_close)} MA20={fmt_num(p_ma20)} RSI={p_rsi}"))
        if count:
            signals.append(("ℹ️", f"{label} 评分={group_score}/{count * 2}"))
        return group_score, count

    group_scores = {}
    total_peer_score = 0
    if isinstance(peers, dict):
        for group_name, items in peers.items():
            if group_name == "market":
                continue
            group_score, count = score_group(items, group_name, st.get("peer_rsi_min", 50))
            group_scores[group_name] = {"score": group_score, "count": count}
            if count and group_score >= count:
                total_peer_score += 1
    else:
        group_score, count = score_group(peers, "peers", st.get("peer_rsi_min", 50))
        group_scores["peers"] = {"score": group_score, "count": count}
        if count and group_score >= count:
            total_peer_score += 1

    main_pct = analysis_change_pct(main)
    market_alpha = None
    peer_alpha = None
    rs_score = 0
    peer_pct_values = []
    peer_items = []
    if isinstance(peers, dict):
        for group_name, items in peers.items():
            if group_name != "market":
                peer_items.extend(items or [])
    else:
        peer_items = peers or []
    for peer in peer_items:
        pct = analysis_change_pct(peer)
        if pct is not None:
            peer_pct_values.append(pct)
    if main_pct is not None and peer_pct_values:
        peer_avg = sum(peer_pct_values) / len(peer_pct_values)
        peer_alpha = round(main_pct - peer_avg, 2)
        if peer_alpha >= st.get("rs_peer_outperform_pct", 2.0):
            rs_score = 1
            signals.append(("✅", f"相对强弱：主标的跑赢同组均值{peer_alpha:+.1f}%"))

    market_score = 0
    if market and "error" not in market:
        m_close = market.get("close")
        m_ma20 = market.get("ma", {}).get(20)
        m_rsi = market.get("rsi14")
        if m_close and m_ma20 and m_close >= m_ma20:
            market_score += 1
        if m_rsi is not None and st.get("market_rsi_min", 45) <= m_rsi <= st.get("market_rsi_max", 65):
            market_score += 1
        signals.append(("ℹ️", f"market:{market['label']} 收盘={fmt_num(m_close)} MA20={fmt_num(m_ma20)} RSI={m_rsi}"))
        signals.append(("ℹ️", f"market 评分={market_score}/2"))
        if market_score >= 2:
            score += 1
        market_pct = analysis_change_pct(market)
        if main_pct is not None and market_pct is not None:
            market_alpha = round(main_pct - market_pct, 2)
            if market_alpha >= st.get("rs_market_outperform_pct", 2.0):
                rs_score = 1
                signals.append(("✅", f"相对强弱：主标的跑赢市场{market_alpha:+.1f}%"))
    if total_peer_score:
        score += 1
        signals.append(("✅", "确认对象整体偏强"))
    elif peers:
        signals.append(("❌", "确认对象分化或偏弱"))
    score += rs_score
    if score >= 3:
        verdict = "确认层支持，趋势环境偏顺"
    elif score == 2:
        verdict = "确认层中性，适合等回撤"
    else:
        verdict = "确认层偏弱，先看不追"
    return {
        "signals": signals,
        "score": score,
        "verdict": verdict,
        "group_scores": group_scores,
        "market_score": market_score,
        "relative_strength": {
            "main_pct": main_pct,
            "market_alpha_pct": market_alpha,
            "peer_alpha_pct": peer_alpha,
            "score": rs_score,
        },
    }


def trade_plan(analysis, confirmation, cfg=None, position_highest=None):
    if not analysis or "error" in analysis:
        return {"buy_zone": None, "watch_zone": None, "stop_loss": None, "take_profit": None, "note": "主标的数据不足", "position_highest": position_highest}
    st = _strategy(cfg)
    close = analysis.get("close")
    supports = analysis.get("supports", [])
    resistances = analysis.get("resistances", [])
    mas = analysis.get("ma", {})
    ma60 = mas.get(60)
    buy_score = buy_decision(analysis, cfg).get("score", 0)
    confirm_score = (confirmation or {}).get("score", 0)
    zone_buffer, max_zone_width = _adaptive_zone_settings(analysis, st)
    entry_cfg = st.get("backtest", {})
    entry_buy_score = st.get("entry_buy_score", entry_cfg.get("entry_buy_score", 2))
    entry_confirm_score = st.get("entry_confirm_score", entry_cfg.get("entry_confirm_score", 2))
    atr_like = None
    if analysis.get("boll") and analysis["boll"].get("upper") and analysis["boll"].get("lower"):
        atr_like = (analysis["boll"]["upper"] - analysis["boll"]["lower"]) / 4

    support_candidates = []
    for s in supports:
        price = s.get("price")
        if price:
            support_candidates.append({"level": s.get("level", "支撑"), "price": price})
    for p in (10, 20):
        ma_val = mas.get(p)
        if ma_val:
            support_candidates.append({"level": f"MA{p}", "price": ma_val})

    anchor = None
    if close and support_candidates:
        below_or_near = [s for s in support_candidates if s["price"] <= close * (1 + zone_buffer)]
        candidates = below_or_near or support_candidates
        anchor = min(candidates, key=lambda s: abs(close - s["price"]))
    elif support_candidates:
        anchor = support_candidates[0]

    def zone_around(price):
        if not price:
            return None
        low = price * (1 - zone_buffer)
        high = price * (1 + zone_buffer)
        if max_zone_width and (high / low - 1) > max_zone_width:
            half = max_zone_width / 2
            low = price * (1 - half)
            high = price * (1 + half)
        return {"low": round(low, 2), "high": round(high, 2)}

    watch_zone = zone_around(anchor["price"]) if anchor else None
    close_in_zone = bool(close and watch_zone and watch_zone["low"] <= close <= watch_zone["high"])
    buy_ready = buy_score >= entry_buy_score and confirm_score >= entry_confirm_score
    buy_zone = watch_zone if buy_ready and close_in_zone else None

    stop_loss = None
    if supports:
        stop_loss = round(supports[0]["price"] * (1 - st.get("stop_loss_support_buffer", 0.015)), 2)
    elif ma60:
        stop_loss = round(ma60 * (1 - st.get("stop_loss_ma60_buffer", 0.02)), 2)
    elif atr_like and close:
        stop_loss = round(close - atr_like * st.get("stop_loss_atr_multiplier", 1.5), 2)
    # 吊灯追踪止损只在已持仓时激活：锚点为持仓以来最高价（position_highest），单调非递减。
    # 未持仓时绝不使用历史 swing_high/recent_high 计算止损——否则标的深跌后 swing_high 仍高悬，
    # 算出的 trailing_stop 会高于现价，导致一开仓就被止损踩踏割肉。
    if position_highest and atr_like:
        trailing_mult = st.get("short_term", {}).get("trailing_stop_atr_multiplier", 2.0)
        trailing_stop = round(position_highest - atr_like * trailing_mult, 2)
        if stop_loss is None or trailing_stop > stop_loss:
            stop_loss = trailing_stop
    take_profit = []
    tp_count = st.get("take_profit_count", 3)
    for r in resistances[:tp_count]:
        take_profit.append({"level": r["level"], "price": r["price"]})
    if not take_profit and close:
        take_profit.append({"level": "目标价", "price": round(close * 1.05, 2)})
    note = confirmation.get("verdict") if confirmation else "未做确认"
    if not buy_zone:
        if buy_score < entry_buy_score:
            note = f"{note}；买入信号不足，观察支撑区而非追价"
        elif not close_in_zone:
            note = f"{note}；价格未回到可操作支撑区"
    return {
        "buy_zone": buy_zone,
        "watch_zone": watch_zone if not buy_zone else None,
        "watch_anchor": anchor,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "note": note,
        "buy_ready": buy_ready,
        "position_highest": position_highest,
    }


def short_term_plan(analysis, cfg=None):
    """超短线精密操盘矩阵：
    等距向下三级接飞刀网格 (每级吸纳20%) ↔ 等距向上三级吃肉止盈网格 (每级派发20%)
    """
    if not analysis or "error" in analysis:
        return {"entries": [], "exits": [], "deep_supports": [], "stop_loss": None, "note": "主标的数据不足"}
    st = _strategy(cfg).get("short_term", {})
    close = analysis.get("close")
    if close is None:
        return {"entries": [], "exits": [], "deep_supports": [], "stop_loss": None, "note": "价格数据不足"}

    # 步长由 config.json 的 pullback_pct 弹性控宽（默认 1.5%）
    step_pct = st.get("pullback_pct", 0.015)
    stop_loss_pct = st.get("stop_loss_pct", 0.018)
    max_primary_pullback = st.get("max_primary_pullback_pct", 0.03)
    supports = analysis.get("supports", [])

    # 左栏：向下等距阶梯跌幅吸纳网格
    entries = [
        {"level": "一档回踩 (吸纳20%)", "price": round(close * (1 - step_pct * 1), 2), "kind": "pullback"},
        {"level": "二档极限 (吸纳20%)", "price": round(close * (1 - step_pct * 2), 2), "kind": "pullback"},
        {"level": "三档铁底 (重仓20%)", "price": round(close * (1 - step_pct * 3), 2), "kind": "pullback"},
    ]

    # 右栏：向上等距阶梯涨幅止盈网格
    exits = [
        {"level": "一档阻力 (派发20%)", "price": round(close * (1 + step_pct * 1), 2), "kind": "target"},
        {"level": "二档高压 (派发20%)", "price": round(close * (1 + step_pct * 2), 2), "kind": "target"},
        {"level": "三档波段 (清仓20%)", "price": round(close * (1 + step_pct * 3), 2), "kind": "target"},
    ]

    # 远端深回撤备用安全垫（跌幅超过 max_primary_pullback 的历史密集区）
    deep_supports = [s for s in supports if s.get("dist_pct", 0) < -max_primary_pullback * 100][:4]

    # 硬性风控割肉线
    stop_loss = round(close * (1 - stop_loss_pct), 2)

    return {
        "entries": entries,
        "exits": exits,
        "deep_supports": deep_supports,
        "stop_loss": stop_loss,
        "note": "日内等距仓位网格已激活：每级间距测算弹性，挂单严格对称",
    }


def final_action(buy, sell, confirmation):
    if not buy or not sell or not confirmation:
        return "数据不足，先观察"
    if sell["score"] >= 3 and confirmation["score"] <= 1:
        return "优先减仓/止盈"
    if buy["score"] >= 3 and confirmation["score"] >= 2:
        return "可分批买入"
    if buy["score"] >= 2 and confirmation["score"] >= 2:
        return "可等回撤分批试探"
    if confirmation["score"] == 0:
        return "外围偏弱，暂不追"
    return "继续观察，等确认"


def final_action_with_momentum(buy, sell, confirmation, momentum, premomentum=None):
    if premomentum and premomentum.get("active"):
        return "上游先动，07709可能补涨，可提前小仓关注"
    if momentum and momentum.get("extended"):
        return "已大幅拉升，谨慎追高，等回踩"
    if momentum and momentum.get("active"):
        if sell and sell.get("score", 0) >= 3:
            return "动量强但卖压也强，只能小仓或等回踩"
        return "动量突破，可轻仓跟随"
    return final_action(buy, sell, confirmation)


def classify_zone(analysis, price, buy=None, sell=None, momentum=None, premomentum=None):
    if premomentum and premomentum.get("active"):
        return "🟢预动量"
    if momentum and momentum.get("active"):
        return "🟢动量买点"
    if momentum and momentum.get("extended"):
        return "🟡已拉升"

    supports = (analysis or {}).get("supports", [])
    resistances = (analysis or {}).get("resistances", [])
    buy_score = (buy or {}).get("score", 0)
    sell_score = (sell or {}).get("score", 0)
    near_support = bool(supports and price is not None and price <= supports[0]["price"] * 1.005)
    near_resistance = bool(resistances and price is not None and price >= resistances[0]["price"] * 0.995)

    if near_support and buy_score >= 2 and sell_score < 2:
        return "🟢买点区"
    if near_support:
        return "🟡支撑观察"
    if near_resistance:
        return "🔴卖点区"
    return "中性"


def leverage_map(underlying, leveraged, ratio=2):
    if "error" in underlying or "error" in leveraged:
        return None
    uc = underlying["close"]
    lc = leveraged["close"]
    if not uc or not lc:
        return None
    factor = lc / uc

    def map_levels(levels):
        out = []
        for lv in levels:
            out.append(
                {
                    "level": lv["level"],
                    "underlying": lv["price"],
                    "leveraged": round(lv["price"] * factor, 2),
                    "dist_pct": lv["dist_pct"],
                }
            )
        return out

    return {
        "ratio": ratio,
        "factor": round(factor, 4),
        "supports": map_levels(underlying["supports"]),
        "resistances": map_levels(underlying["resistances"]),
        "note": f"杠杆衍生品价位≈底层价位×{factor:.3f}(当前价比)；{ratio}x杠杆下涨跌幅约为底层{ratio}倍，存在日间损耗",
    }

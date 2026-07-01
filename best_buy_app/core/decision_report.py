#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from datetime import datetime

from best_buy_app.core.decision_rules import fmt_num, leverage_map


def _fmt_level(item):
    return f"{item['level']}={fmt_num(item['price'])}({item['dist_pct']:+.1f}%)"


def _fmt_prices(levels):
    return " | ".join(fmt_num(x["price"]) for x in levels) if levels else "N/A"


def _fmt_named_prices(levels):
    return " | ".join(f"{x['level']}={fmt_num(x['price'])}" for x in levels) if levels else "N/A"


def _fmt_pct(v):
    return f"{v:+.1f}%" if isinstance(v, (int, float)) else "N/A"


def _peer_price_line(peer_analyses, limit=3):
    parts = []
    for peer in (peer_analyses or [])[:limit]:
        if peer and "error" not in peer:
            parts.append(f"{peer['label']}={fmt_num(peer.get('close'))}")
    return " | ".join(parts)


def _market_price_line(market_analysis):
    if not market_analysis or "error" in market_analysis:
        return ""
    return f"{market_analysis['label']}={fmt_num(market_analysis.get('close'))}"


def render_watch_tick(tick, ts, symbol, price, zone, analysis, buy, sell, confirm, plan, peer_analyses=None, market_analysis=None):
    ma20 = analysis.get("ma", {}).get(20)
    ma20_dist = ""
    if price is not None and ma20:
        ma20_dist = f" 距MA20{(price / ma20 - 1) * 100:+.1f}%"

    ref_parts = [p for p in (_peer_price_line(peer_analyses), _market_price_line(market_analysis)) if p]
    ref_text = f" | {' | '.join(ref_parts)}" if ref_parts else ""

    supports = analysis.get("supports", [])[:4]
    resistances = analysis.get("resistances", [])[:5]
    momentum = plan.get("momentum") if isinstance(plan, dict) else None
    premomentum = plan.get("premomentum") if isinstance(plan, dict) else None
    short_plan = plan.get("short_term", {}) if isinstance(plan, dict) else {}
    short_entries = short_plan.get("entries", [])
    short_exits = short_plan.get("exits", [])
    deep_supports = short_plan.get("deep_supports", supports)

    lines = [
        f"[{ts}] #{tick} {symbol}={fmt_num(price)} {zone}{ref_text}",
        f"买:{buy['verdict']}{ma20_dist}",
        f"卖:{sell['verdict']}",
        f"确认:{confirm['verdict']}({confirm['score']}) 动作:{plan.get('note', '')}",
        f"预动量:{premomentum.get('verdict')} 分={premomentum.get('score', 0)} 07709={_fmt_pct(premomentum.get('main_pct'))}" if premomentum else "预动量:未计算",
        f"动量:{momentum.get('verdict')} 分={momentum.get('score', 0)} 涨幅={_fmt_pct(momentum.get('pct'))}" if momentum else "动量:未计算",
        f"支撑:{' | '.join(_fmt_level(x) for x in supports) if supports else 'N/A'}",
        f"阻力:{' | '.join(_fmt_level(x) for x in resistances) if resistances else 'N/A'}",
        f"短线买点:{_fmt_named_prices(short_entries)}",
        f"短线卖点:{_fmt_named_prices(short_exits or resistances)}",
        f"短线止损:{fmt_num(short_plan.get('stop_loss'))}",
        f"深回撤备用:{_fmt_named_prices(deep_supports)}",
    ]
    return "\n".join(lines)


def render_report(symbol, quote, analysis, lev_analysis, buy, sell, mode, leverage_spec):
    lines = []
    lines.append("=" * 64)
    lines.append(f"best-buy 决策报告  |  {symbol}  |  生成于 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("=" * 64)
    lines.append("\n【实时行情】")
    if quote:
        for k, v in quote.items():
            if v is not None and k not in ("source",):
                lines.append(f"  {k}: {v}")
    else:
        lines.append("  (未能获取实时行情)")
    a = analysis
    if "error" in a:
        lines.append(f"\n[分析失败] {a['error']}")
        return "\n".join(lines)

    def block(name, an):
        live = an.get("live_price")
        ts = an.get("live_timestamp")
        if live is not None and live != an.get("close"):
            head = f"最新(实时)={fmt_num(live)}"
        elif live is not None:
            head = f"最新={fmt_num(live)}(实时)"
        else:
            head = f"最新={fmt_num(an['close'])}"
        date_tag = f"行情={ts}" if ts else f"日期={an['last_date']}"
        lines.append(f"\n【{name}】 {head}  {date_tag}  (n={an['n']})")
        lines.append(f"  布林: 上={fmt_num(an['boll']['upper'])} 中={fmt_num(an['boll']['mid'])} 下={fmt_num(an['boll']['lower'])}")
        ma_str = "  ".join(f"MA{p}={fmt_num(an['ma'][p])}" for p in (5, 10, 20, 60) if an['ma'][p])
        lines.append(f"  {ma_str}")
        lines.append(f"  RSI14={an['rsi14']}  KDJ: K={an['kdj']['k']} D={an['kdj']['d']} J={an['kdj']['j']} ({an['kdj']['cross']})")
        m = an["macd"]
        lines.append(f"  MACD: HIST={m['hist']} DIF={m['dif']} DEA={m['dea']} ({m['bar']}{('·缩短' if m['shortening'] else '') if m['bar'] else ''})")
        c = an["candle"]
        if c:
            lines.append(f"  K线: 实体{c['body_pct']}% 上影{c['upper_shadow_pct']}% 下影{c['lower_shadow_pct']}%{' (十字星)' if c.get('is_doji') else ''}")
        lines.append("  支撑位(由高到低):")
        for s in an["supports"][:6]:
            lines.append(f"    {s['level']:<14} {fmt_num(s['price']):>12}  ({s['dist_pct']:+.1f}%)")
        lines.append("  阻力位(由低到高):")
        for r in an["resistances"][:6]:
            lines.append(f"    {r['level']:<14} {fmt_num(r['price']):>12}  ({r['dist_pct']:+.1f}%)")

    block(a["label"], a)
    if lev_analysis:
        block(lev_analysis["label"], lev_analysis)
    if leverage_spec and lev_analysis and "error" not in lev_analysis:
        lm = leverage_map(a, lev_analysis, leverage_spec.get("ratio", 2))
        if lm:
            lines.append(f"\n【杠杆映射】 底层{a['label']} ↔ 衍生品{lev_analysis['label']}  因子={lm['factor']}")
            lines.append(f"  {lm['note']}")
            lines.append("  底层支撑 → 衍生品对应价:")
            for s in lm["supports"][:4]:
                lines.append(f"    {s['level']:<14} 底层{fmt_num(s['underlying'])} → 衍生品{fmt_num(s['leveraged'])}")
            lines.append("  底层阻力 → 衍生品对应价:")
            for r in lm["resistances"][:4]:
                lines.append(f"    {r['level']:<14} 底层{fmt_num(r['underlying'])} → 衍生品{fmt_num(r['leveraged'])}")
    if mode in ("buy", "both"):
        lines.append(f"\n【买入评估】 {buy['verdict']}  ({buy['score']}/5)")
        for mark, txt in buy["signals"]:
            lines.append(f"  {mark} {txt}")
        if a["ma"].get(20):
            d1 = (a["ma"][20] / a["close"] - 1) * 100
            lines.append(f"  距第一支撑(MA20={fmt_num(a['ma'][20])}) 还需 {d1:+.1f}%")
    if mode in ("sell", "both"):
        lines.append(f"\n【卖出评估】 {sell['verdict']}  ({sell['score']}/5)")
        for mark, txt in sell["signals"]:
            lines.append(f"  {mark} {txt}")
        if a["resistances"]:
            r1 = a["resistances"][0]
            lines.append(f"  近端第一阻力: {r1['level']}={fmt_num(r1['price'])} ({r1['dist_pct']:+.1f}%)")
    lines.append("\n【分批建议】")
    if mode in ("buy", "both"):
        sup = a["supports"]
        lines.append("  买点(由近到远):")
        for i, s in enumerate(sup[:4], 1):
            tag = ["试探20%", "主力30%", "重仓30%", "大底20%"][i - 1] if i <= 4 else ""
            lines.append(f"    第{i}档 {tag}: {s['level']}={fmt_num(s['price'])} ({s['dist_pct']:+.1f}%)")
    if mode in ("sell", "both"):
        res = a["resistances"]
        lines.append("  卖点(由近到远):")
        for i, r in enumerate(res[:4], 1):
            tag = ["减仓30-40%", "主力30-40%", "清仓", "捂牛20%"][i - 1] if i <= 4 else ""
            lines.append(f"    第{i}档 {tag}: {r['level']}={fmt_num(r['price'])} ({r['dist_pct']:+.1f}%)")
    lines.append("  移动止盈: 收盘跌破MA5减半；跌破MA10+MACD死叉清仓；或自高点回撤8-10%止盈")
    lines.append("\n⚠️ 以上为技术面推演，不构成投资建议。杠杆产品风险极高，单日波动可达±6%~±23%。")
    return "\n".join(lines)

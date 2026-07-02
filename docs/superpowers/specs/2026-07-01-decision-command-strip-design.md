# Decision Command Strip Design

## Goal
Add a real-time `核心决策指挥横条` above the virtual paper position safety bar.

## Decision
Use the existing rule engine output from `/api/stocks/feed`. Do not call an LLM for this primary command strip.

## Rationale
The command strip is an execution signal, so it must stay consistent with `buy_score`, `sell_score`, `confirm_score`, `zone`, `action`, `premomentum`, and `momentum`. The dashboard feed already refreshes frequently. An LLM refreshed once per minute would add latency, cost, failure modes, and possible text that conflicts with the rule engine.

## UI
Place a compact panel immediately below the ticker strip and above `虚拟纸面持仓安全线`.

Display format:

`🚦 核心决策指挥横条: 🟢 多周期顺风共振，可分批轻仓试探布局`

The strip uses a tone class:
- `good` for constructive long-side command
- `warn` for observation or waiting
- `bad` for defensive or sell-risk command
- `info` for neutral status

## Rule Mapping
Generate the strip from frontend data already present in the feed:
- `momentum.active`: `⚡ 盘中动量触发，右侧突破优先`
- `premomentum.active`: `📡 跨境联动预启动，观察主标的补涨`
- High sell risk: `🔴 卖出/见顶风险抬升，优先减仓或防守`
- Strong confirmation plus usable buy signal: `🟢 多周期顺风共振，可分批轻仓试探布局`
- Strong confirmation but weak buy signal: `🟡 趋势环境偏顺，等待理想买点分批试探`
- Fallback: use the existing `action` text as a neutral command.

## Implementation Scope
Modify only:
- `dashboard/index.html`
- `dashboard/app.js`
- `dashboard/styles.css`
- `test_best_buy.py`

## Testing
Add a static dashboard test that verifies:
- the command strip exists above `position-blood-hud`
- it has command text and tone DOM nodes
- JS contains a rule-based command function
- no AI endpoint is introduced for the strip

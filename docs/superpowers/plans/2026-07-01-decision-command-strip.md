# Decision Command Strip Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a real-time rule-engine command strip above the virtual paper position safety bar.

**Architecture:** The dashboard keeps using `/api/stocks/feed`. Frontend `render(data)` calls a small pure helper that maps existing feed fields to `{ tone, icon, text }`, then writes the command strip DOM.

**Tech Stack:** Static HTML, vanilla JavaScript, CSS, Python unittest static checks.

---

### Task 1: Static Contract Test

**Files:**
- Modify: `test_best_buy.py`

- [ ] **Step 1: Add a failing static dashboard test**

Add `test_dashboard_command_strip_uses_rule_engine_above_position_hud` to assert that:
- `decision-command-strip` exists in `dashboard/index.html`
- it appears before `position-blood-hud`
- `commandDecision` exists in `dashboard/app.js`
- no new AI endpoint URL exists for this strip

- [ ] **Step 2: Run the focused test**

Run: `python3 -m unittest test_best_buy.TestBestBuy.test_dashboard_command_strip_uses_rule_engine_above_position_hud`

Expected: FAIL because the command strip is not implemented yet.

### Task 2: Markup, Logic, and Styles

**Files:**
- Modify: `dashboard/index.html`
- Modify: `dashboard/app.js`
- Modify: `dashboard/styles.css`

- [ ] **Step 1: Add markup**

Insert a panel between ticker strip and position HUD:

```html
<section class="panel decision-command-strip" aria-label="核心决策指挥横条">
  <span class="command-label">核心决策指挥横条</span>
  <strong id="commandText" class="info">--</strong>
</section>
```

- [ ] **Step 2: Add rule helper**

Implement `commandDecision(data)` in `dashboard/app.js` using existing feed fields. Priority:
1. `momentum.active`
2. `premomentum.active`
3. sell score stronger than buy score
4. strong confirm plus buy signal
5. strong confirm only
6. fallback to `data.action`

- [ ] **Step 3: Render the strip**

In `render(data)`, call `commandDecision(data)` and write `commandText.textContent` and `commandText.className`.

- [ ] **Step 4: Add compact strip styles**

Style `.decision-command-strip` as a horizontal command panel with mobile-safe wrapping.

### Task 3: Verification

**Files:**
- Test: `test_best_buy.py`

- [ ] **Step 1: Run focused test**

Run: `python3 -m unittest test_best_buy.TestBestBuy.test_dashboard_command_strip_uses_rule_engine_above_position_hud`

Expected: PASS.

- [ ] **Step 2: Run full test suite**

Run: `python3 -m unittest test_best_buy.py`

Expected: all tests pass.

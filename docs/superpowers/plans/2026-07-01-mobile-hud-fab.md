# 移动端 HUD 与悬浮 AI 按钮 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `dashboard` 改造成手机端单屏 HUD 视窗，并把 AI 入口改为右下角常驻悬浮按钮，同时保留现有 `app.js` 的数据绑定。

**Architecture:** 保留现有页面的核心 ID 和数据流，新增一层移动端 HUD 壳层来重排视觉结构。CSS 只在小屏幕下接管布局和抽屉表现，JS 只补充 FAB 开关与抽屉 Tab 切换，不改后端接口，也不改现有刷新逻辑。

**Tech Stack:** 原生 HTML、CSS Media Queries、原生 JavaScript。

---

### Task 1: 重排 `dashboard/index.html` 为双层结构

**Files:**
- Modify: `dashboard/index.html`

- [ ] **Step 1: 保留现有核心绑定点**

```html
<!-- 需要继续保留：#pageTitle #stockTabs #openStockModal #symbol #price #zone #action #confirm #meta
     #decisionSummary #updatedAt #buyText #ma20Text #sellText #confirmText #premomentumText #momentumText
     #noteText #shortEntries #shortExits #shortStop #refs #feed #aiContextSummary #aiContext #stockSearchStatus
     #chatLog #chatForm #chatInput #clearChat -->
```

- [ ] **Step 2: 用移动端 HUD 结构包住旧内容**

```html
<main class="shell-hud-mobile">
  <section class="multi-stock-deck" aria-label="自选监控标的">
    <div class="stock-grid-scroll" id="stockTabs"></div>
    <button class="add-stock-btn-hud" type="button" id="openStockModal" aria-label="添加自选标的">+</button>
  </section>

  <section class="main-hud-panel">
    <div class="strip-hud">
      <div class="ticker-box">
        <span id="symbol">--</span>
        <strong id="price">--</strong>
      </div>
      <div class="meta-box"><span>量比</span><strong id="volRatioHud" class="good">--</strong></div>
      <div class="meta-box"><span>确认分</span><strong id="confirm">--</strong></div>
      <div class="time-box" id="meta">--:--:--</div>
    </div>

    <div class="decision-banner-hud" id="zoneBanner">
      <span class="lbl">状态:</span>
      <strong id="zone">--</strong>
      <em id="action">--</em>
    </div>

    <div class="position-blood-hud" id="positionHud">
      <div class="hud-head">
        <span>📊 纸面持仓: <strong id="hudPositionStatus" class="info">未持仓</strong></span>
        <span class="muted">成本/最高: <em id="hudEntryPeak">-- / --</em></span>
      </div>
      <div class="blood-bar-wrapper">
        <div class="blood-labels">
          <span class="bad">止损线 <em id="hudStopPrice">--</em></span>
          <span class="muted">距离清仓: <em id="hudStopDistance" class="bad">--</em></span>
          <span class="good">现价</span>
        </div>
        <div class="blood-container">
          <div class="blood-fill animate-pulse" id="stopLossBloodBar" style="width: 100%"></div>
        </div>
      </div>
    </div>

    <div class="execution-map-hud">
      <div class="exec-col">
        <div class="exec-head">📍 理想买位 (回踩/突破)</div>
        <div class="pill-vertical-list" id="shortEntries"></div>
      </div>
      <div class="exec-col">
        <div class="exec-head">🎯 目标阻力 (分批止盈)</div>
        <div class="pill-vertical-list" id="shortExits"></div>
      </div>
    </div>
  </section>

  <section class="cabin-drawer-hud">
    <div class="drawer-tabs">
      <button class="drawer-tab active" type="button" id="tabTriggerRefs">📡 先导矩阵</button>
      <button class="drawer-tab" type="button" id="tabTriggerEvents">📋 监控事件</button>
    </div>
    <div class="drawer-content active" id="cabin-refs"><div id="refs" class="refs-hud-list"></div></div>
    <div class="drawer-content" id="cabin-events"><div id="feed" class="events-hud-list"></div></div>
  </section>
</main>
```

- [ ] **Step 3: 将 AI 入口改为悬浮按钮并保留原聊天容器**

```html
<button type="button" class="ai-fab-btn" id="tacticalAiFabTrigger" aria-label="唤起战术AI">
  <span class="ai-fab-icon">🤖</span>
  <span class="ai-fab-text">问AI</span>
</button>
```

**Success Criteria:** 手机端页面仍能被 `app.js` 直接渲染，现有 ID 不丢失，AI 不再占据主视图。
**Tests:** 打开页面后检查 `document.getElementById(...)` 对旧 ID 和新增 ID 都能取到。

### Task 2: 追加手机端 HUD 覆盖样式

**Files:**
- Modify: `dashboard/styles.css`

- [ ] **Step 1: 保留桌面样式，追加 `@media (max-width: 480px)` 覆盖**

```css
@media (max-width: 480px) {
  html, body {
    overflow: hidden !important;
    height: 100vh;
    background: #060910;
    color: #f3f4f6;
    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  }

  .shell-hud-mobile { display: flex; flex-direction: column; height: 100vh; padding: 4px; gap: 4px; }
  .multi-stock-deck, .main-hud-panel, .cabin-drawer-hud { background: #0d1322; border: 1px solid #1e2942; border-radius: 4px; }
  .stock-grid-scroll { display: flex; gap: 4px; overflow-x: auto; scrollbar-width: none; }
  .stock-grid-scroll::-webkit-scrollbar { display: none; }
  .stock-tab { flex: 0 0 31%; min-height: 35px; padding: 3px 4px; border-radius: 4px; }
  .stock-tab-close { display: none !important; }
  .add-stock-btn-hud { width: 35px; height: 35px; border-radius: 4px; }
  .strip-hud, .decision-banner-hud, .position-blood-hud, .execution-map-hud, .drawer-tabs { margin: 0; }
  .execution-map-hud { display: grid; grid-template-columns: 1fr 1fr; gap: 4px; }
  .ai-fab-btn {
    position: fixed;
    bottom: 24px;
    right: 20px;
    z-index: 99;
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 10px 15px;
    border-radius: 24px;
  }
  .ai-drawer-backdrop { position: fixed; inset: 0; z-index: 150; background: rgba(0, 0, 0, 0.75); display: flex; flex-direction: column; justify-content: flex-end; }
  .ai-drawer-backdrop[hidden] { display: none; }
  .ai-drawer-body { height: 80vh; border-radius: 12px 12px 0 0; }
}
```

- [ ] **Step 2: 把聊天气泡、搜索弹窗、抽屉内容压缩到手机屏可读**

```css
.msg { max-width: 88%; padding: 6px 10px; font-size: 12px; border-radius: 6px; }
.modal-backdrop { padding-top: 40px; }
.modal { width: 96%; }
```

**Success Criteria:** 小屏幕禁止整页滚动，顶部自选股横向滑动可用，AI 按钮悬浮在右下角。
**Tests:** 480px 宽度下页面无纵向滚动条，FAB 可点击，抽屉可覆盖主界面。

### Task 3: 追加 FAB、抽屉和 HUD 字段渲染逻辑

**Files:**
- Modify: `dashboard/app.js`

- [ ] **Step 1: 保留现有渲染函数，在末尾追加 FAB 与抽屉事件绑定**

```javascript
(function() {
  const fabBtn = document.getElementById('tacticalAiFabTrigger');
  const aiOverlay = document.getElementById('aiDrawerOverlay');
  const closeBtn = document.getElementById('closeAiDrawer');
  const btnRefs = document.getElementById('tabTriggerRefs');
  const btnEvents = document.getElementById('tabTriggerEvents');
  const cabinRefs = document.getElementById('cabin-refs');
  const cabinEvents = document.getElementById('cabin-events');

  if (fabBtn && aiOverlay) {
    fabBtn.addEventListener('click', function() {
      aiOverlay.removeAttribute('hidden');
      const scroller = document.getElementById('chatLog');
      if (scroller) scroller.scrollTop = scroller.scrollHeight;
    });
  }

  if (closeBtn && aiOverlay) {
    closeBtn.addEventListener('click', function() {
      aiOverlay.setAttribute('hidden', '');
    });
  }

  if (btnRefs && btnEvents && cabinRefs && cabinEvents) {
    btnRefs.addEventListener('click', function() {
      btnEvents.classList.remove('active');
      btnRefs.classList.add('active');
      cabinEvents.classList.remove('active');
      cabinRefs.classList.add('active');
    });

    btnEvents.addEventListener('click', function() {
      btnRefs.classList.remove('active');
      btnEvents.classList.add('active');
      cabinRefs.classList.remove('active');
      cabinEvents.classList.remove('active');
    });
  }
})();
```

- [ ] **Step 2: 在 `render()` 里补充量比和持仓 HUD 的保守映射**

```javascript
const volumeRatio = main.analysis && main.analysis.volume_ratio;
el('volRatioHud').textContent = volumeRatio == null ? '--' : String(volumeRatio);
const pos = data.position || null;
el('hudPositionStatus').textContent = pos ? '已持仓' : '未持仓';
el('hudEntryPeak').textContent = pos ? `${fmt(pos.entry_price)} / ${fmt(pos.highest_since_entry)}` : '-- / --';
el('hudStopPrice').textContent = fmt(plan.stop_loss);
```

**Success Criteria:** FAB 能打开和关闭 AI 抽屉，移动端 Tab 可切换，HUD 字段不因缺数据报错。
**Tests:** 手工刷新页面后打开 FAB、切换抽屉 Tab、确认 `render()` 不抛异常。

### Task 4: 做本地验证

**Files:**
- None

- [ ] **Step 1: 运行前端相关检查或最小可视验证**

```bash
python3 -m unittest
```

- [ ] **Step 2: 在浏览器里确认移动端布局**

```text
检查项：无纵向滚动、FAB 常驻右下角、AI 抽屉覆盖主内容、旧 ID 仍可被脚本找到。
```

**Success Criteria:** 页面可打开，新增 HUD 组件正常显示，旧交互不回退。
**Tests:** 运行测试并在浏览器中做一次手机宽度检查。

const CHAT_URL = '/api/chat';
const CHAT_STREAM_URL = '/api/chat/stream';
const STOCK_SEARCH_URL = '/api/stocks/search';
const STOCK_FEED_URL = '/api/stocks/feed';
const WATCHLIST_URL = '/api/watchlist';
const REFRESH_MS = 4000;
const CHAT_KEY = 'best-buy-chat-history';
const STOCKS_KEY = 'best-buy-stock-tabs';

const el = (id) => document.getElementById(id);
let latestData = null;
let chatMessages = loadChat();
let stockTabs = loadStockTabs();
let activeSymbol = stockTabs[0].symbol;
let searchTimer = null;
let refreshSeq = 0;
let refreshRequest = null;

function setPageTitle(symbol) {
  const title = el('pageTitle');
  if (title) title.textContent = symbol || '--';
}

function fmt(v) {
  if (v === null || v === undefined || v === '') return '--';
  if (typeof v === 'number' && Math.abs(v) >= 1000) return v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return String(v);
}

function clsFor(text) {
  const value = String(text || '');
  if (value.includes('买入') || value.includes('支持') || value.includes('偏顺')) return 'good';
  if (value.includes('减仓') || value.includes('卖') || value.includes('偏弱')) return 'bad';
  if (value.includes('等待') || value.includes('中性') || value.includes('观察')) return 'warn';
  return 'info';
}

function pct(price, base) {
  if (!price || !base) return '';
  return `${((price / base - 1) * 100).toFixed(1)}%`;
}

function levelRow(item) {
  return `
    <div class="level-row">
      <strong>${fmt(item.level)}</strong>
      <span class="price">${fmt(item.price)}</span>
      <span class="dist">${item.dist_pct === undefined ? '--' : `${Number(item.dist_pct).toFixed(1)}%`}</span>
    </div>
  `;
}

function renderLevels(target, levels) {
  el(target).innerHTML = (levels || []).slice(0, 5).map(levelRow).join('') || '<div class="muted">暂无数据</div>';
}

function renderPills(target, items) {
  el(target).innerHTML = (items || []).map((item) => `
    <div class="price-pill">
      <span>${fmt(item.level)}</span>
      <strong>${fmt(item.price)}</strong>
    </div>
  `).join('') || '<div class="muted">暂无数据</div>';
}

function renderContext(data) {
  const main = data.main || {};
  const buy = main.buy || {};
  const sell = main.sell || {};
  const confirm = main.confirm || {};
  const premomentum = main.premomentum || data.premomentum || {};
  const momentum = main.momentum || data.momentum || {};
  const plan = main.plan || {};
  const rows = [
    ['标的', `${fmt(data.symbol)} = ${fmt(data.price)}`],
    ['区间', data.zone],
    ['买', buy.verdict],
    ['卖', sell.verdict],
    ['确认', `${fmt(confirm.verdict)} (${fmt(data.confirm_score)})`],
    ['预动量', premomentum.verdict],
    ['动量', momentum.verdict],
    ['动作', data.action],
    ['备注', plan.note],
  ];
  el('aiContext').innerHTML = rows.map(([k, v]) => `
    <div class="context-row"><span class="muted">${k}</span><strong>${fmt(v)}</strong></div>
  `).join('');
  el('aiContextSummary').textContent = `${fmt(data.symbol)}=${fmt(data.price)} ${fmt(data.zone)} ${fmt(data.action)}`;
}

function render(data) {
  latestData = data;
  const main = data.main || {};
  const buy = main.buy || {};
  const sell = main.sell || {};
  const confirm = main.confirm || {};
  const premomentum = main.premomentum || data.premomentum || {};
  const momentum = main.momentum || data.momentum || {};
  const plan = main.plan || {};
  const shortPlan = main.short_term || plan.short_term || {};
  const analysis = main.analysis || {};
  const supports = main.supports || analysis.supports || [];
  const resistances = main.resistances || analysis.resistances || [];
  const ma20 = main.ma20 || (analysis.ma && analysis.ma[20]);

  el('symbol').textContent = data.symbol || '--';
  setPageTitle(data.symbol || activeSymbol);
  el('price').textContent = fmt(data.price);
  el('zone').textContent = fmt(data.zone);
  el('action').textContent = fmt(data.action);
  el('confirm').textContent = fmt(data.confirm_score);
  const quote = data.quote || {};
  const quoteMeta = [quote.source, quote.timestamp].filter(Boolean).join(' ');
  el('meta').textContent = quoteMeta ? `${fmt(data.time)} · ${quoteMeta}` : fmt(data.time);
  el('updatedAt').textContent = `最后刷新 ${new Date().toLocaleTimeString()}`;

  el('buyText').textContent = fmt(buy.verdict);
  el('buyText').className = clsFor(buy.verdict);
  el('ma20Text').textContent = ma20 ? `距MA20 ${pct(data.price, ma20)}` : '--';
  el('sellText').textContent = fmt(sell.verdict);
  el('sellText').className = clsFor(sell.verdict);
  el('confirmText').textContent = `${fmt(confirm.verdict)} (${fmt(data.confirm_score)})`;
  el('confirmText').className = clsFor(confirm.verdict);
  el('premomentumText').textContent = premomentum.verdict ? `${premomentum.verdict} (${fmt(premomentum.score)})` : '--';
  el('premomentumText').className = clsFor(premomentum.verdict);
  el('momentumText').textContent = momentum.verdict ? `${momentum.verdict} (${fmt(momentum.pct)}%)` : '--';
  el('momentumText').className = clsFor(momentum.verdict);
  el('noteText').textContent = fmt(plan.note || data.action);
  el('decisionSummary').textContent = `${fmt(data.zone)} | 买:${fmt(buy.verdict)} | 卖:${fmt(sell.verdict)}`;

  renderPills('shortEntries', shortPlan.entries);
  renderPills('shortExits', shortPlan.exits);
  el('shortStop').textContent = fmt(shortPlan.stop_loss);
  el('shortNote').textContent = fmt(shortPlan.note);

  renderLevels('supports', shortPlan.deep_supports || supports);
  renderLevels('resistances', resistances);

  const buyPrices = (shortPlan.deep_supports || supports).slice(0, 4).map((x) => fmt(x.price));
  el('buyLevels').textContent = buyPrices.join(' | ') || '--';
  el('sellLevels').textContent = (shortPlan.exits || resistances).slice(0, 5).map((x) => fmt(x.price)).join(' | ') || '--';

  const refs = [];
  (main.peers || []).forEach((p) => refs.push(p));
  if (main.market) refs.push(main.market);
  el('refs').innerHTML = refs.slice(0, 8).map((p) => {
    const close = p.close;
    const ma = p.ma20 || (p.ma && p.ma[20]);
    const trend = close && ma ? (close >= ma ? '偏强' : '偏弱') : '--';
    return `
      <div class="ref-row">
        <strong>${fmt(p.label || p.symbol)}</strong>
        <span class="value">${fmt(close)}</span>
        <span class="${clsFor(trend)}">${trend}</span>
        <span class="muted">RSI ${fmt(p.rsi14)}</span>
      </div>
    `;
  }).join('') || '<div class="muted">暂无确认对象</div>';

  const history = (data.history || []).slice(-5).reverse();
  el('feed').innerHTML = history.map((item) => `
    <div class="history-row">
      <strong>${fmt(item.time)}</strong>
      <p>${fmt(item.symbol)}=${fmt(item.price)} ${fmt(item.zone)} | 买:${fmt(item.buy)} | 卖:${fmt(item.sell)}${item.quote_source ? ` | 源:${fmt(item.quote_source)}` : ''}${item.quote_timestamp ? ` ${fmt(item.quote_timestamp)}` : ''}</p>
    </div>
  `).join('') || '<div class="muted">暂无记录</div>';

  renderContext(data);
}

function normalizeSymbol(symbol) {
  return String(symbol || '').trim().toUpperCase();
}

function loadStockTabs() {
  try {
    const saved = JSON.parse(localStorage.getItem(STOCKS_KEY) || '[]');
    if (Array.isArray(saved) && saved.length) {
      return saved.filter((item) => item && item.symbol).map((item) => ({
        symbol: normalizeSymbol(item.symbol),
        name: item.name || '',
        market: item.market || item.market_name || '',
      }));
    }
  } catch (_) {
    // Fall through to default tab.
  }
  return [{ symbol: '07709', name: '07709', market: 'HK' }];
}

function saveStockTabs() {
  localStorage.setItem(STOCKS_KEY, JSON.stringify(stockTabs));
}

function labelForStock(stock) {
  const name = stock.name && stock.name !== stock.symbol ? ` ${stock.name}` : '';
  return `${stock.symbol}${name}`;
}

function renderStockTabs() {
  setPageTitle(activeSymbol);
  el('stockTabs').innerHTML = stockTabs.map((stock) => `
    <button class="stock-tab ${stock.symbol === activeSymbol ? 'active' : ''}" type="button" data-symbol="${escapeHtml(stock.symbol)}" title="${escapeHtml(labelForStock(stock))}">
      <strong>${escapeHtml(stock.symbol)}</strong>
      ${stock.name ? `<span>${escapeHtml(stock.name)}</span>` : ''}
      <span class="stock-tab-close" role="button" aria-label="移除关注" data-remove="${escapeHtml(stock.symbol)}">×</span>
    </button>
  `).join('');
  document.querySelectorAll('.stock-tab').forEach((btn) => {
    btn.addEventListener('click', (event) => {
      if (event.target.dataset.remove) return;
      activeSymbol = btn.dataset.symbol;
      setPageTitle(activeSymbol);
      renderStockTabs();
      refresh();
    });
  });
  document.querySelectorAll('.stock-tab-close').forEach((btn) => {
    btn.addEventListener('click', (event) => {
      event.stopPropagation();
      removeStockTab(btn.dataset.remove);
    });
  });
}

function addStockTab(stock) {
  const symbol = normalizeSymbol(stock.symbol);
  if (!symbol) return;
  const existing = stockTabs.find((item) => item.symbol === symbol);
  if (!existing) {
    stockTabs.push({
      symbol,
      name: stock.name || '',
      market: stock.market_name || stock.market || '',
    });
    saveStockTabs();
  }
  activeSymbol = symbol;
  setPageTitle(activeSymbol);
  renderStockTabs();
  closeStockModal();
  persistWatchAdd(stock);
  refresh();
}

function removeStockTab(symbol) {
  const normalized = normalizeSymbol(symbol);
  stockTabs = stockTabs.filter((item) => item.symbol !== normalized);
  if (!stockTabs.length) {
    stockTabs = [{ symbol: '07709', name: '07709', market: 'HK' }];
  }
  if (activeSymbol === normalized) {
    activeSymbol = stockTabs[0].symbol;
  }
  saveStockTabs();
  setPageTitle(activeSymbol);
  renderStockTabs();
  persistWatchRemove(normalized);
  refresh();
}

async function persistWatchAdd(stock) {
  const symbol = normalizeSymbol(stock.symbol);
  if (!symbol) return;
  try {
    await fetch(WATCHLIST_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ symbol, label: stock.name || '' }),
    });
  } catch (_) {
    // 服务端记录失败不影响本地使用。
  }
}

async function persistWatchRemove(symbol) {
  try {
    await fetch(`${WATCHLIST_URL}?symbol=${encodeURIComponent(symbol)}`, { method: 'DELETE' });
  } catch (_) {
    // 忽略删除失败。
  }
}

async function hydrateWatchlistFromServer() {
  try {
    const res = await fetch(WATCHLIST_URL, { cache: 'no-store' });
    if (!res.ok) return;
    const data = await res.json();
    const items = data.items || [];
    let changed = false;
    items.forEach((item) => {
      const symbol = normalizeSymbol(item.symbol);
      if (symbol && !stockTabs.find((tab) => tab.symbol === symbol)) {
        stockTabs.push({ symbol, name: item.label || '', market: '' });
        changed = true;
      }
    });
    if (changed) {
      saveStockTabs();
      renderStockTabs();
    }
  } catch (_) {
    // 离线/服务未启动时静默。
  }
}

function openStockModal() {
  el('stockModal').hidden = false;
  el('stockSearchInput').value = '';
  el('stockSearchResults').innerHTML = '';
  el('stockSearchStatus').textContent = '输入关键词开始搜索';
  setTimeout(() => el('stockSearchInput').focus(), 0);
}

function closeStockModal() {
  el('stockModal').hidden = true;
}

function renderSearchResults(items) {
  if (!items.length) {
    el('stockSearchResults').innerHTML = '';
    el('stockSearchStatus').textContent = '没有找到匹配股票';
    return;
  }
  el('stockSearchStatus').textContent = `找到 ${items.length} 个结果`;
  el('stockSearchResults').innerHTML = items.map((item) => `
    <button class="search-result" type="button" data-symbol="${escapeHtml(item.symbol)}">
      <strong>${escapeHtml(item.symbol)}</strong>
      <span>${escapeHtml(item.name || '--')}</span>
      <em>${escapeHtml(item.market_name || item.security_type || '')}</em>
    </button>
  `).join('');
  document.querySelectorAll('.search-result').forEach((btn) => {
    btn.addEventListener('click', () => {
      const item = items.find((x) => x.symbol === btn.dataset.symbol);
      if (item) addStockTab(item);
    });
  });
}

async function searchStocks(keyword) {
  const q = keyword.trim();
  if (!q) {
    el('stockSearchResults').innerHTML = '';
    el('stockSearchStatus').textContent = '输入关键词开始搜索';
    return;
  }
  el('stockSearchStatus').textContent = '搜索中...';
  try {
    const res = await fetch(`${STOCK_SEARCH_URL}?q=${encodeURIComponent(q)}`, { cache: 'no-store' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    renderSearchResults(data.items || []);
  } catch (err) {
    el('stockSearchResults').innerHTML = '';
    el('stockSearchStatus').textContent = `搜索失败: ${err.message}`;
  }
}

function loadChat() {
  try {
    return JSON.parse(localStorage.getItem(CHAT_KEY) || '[]');
  } catch (_) {
    return [];
  }
}

function saveChat() {
  localStorage.setItem(CHAT_KEY, JSON.stringify(chatMessages.slice(-80)));
}

function renderChat() {
  el('chatLog').innerHTML = chatMessages.map((msg) => `<div class="msg ${msg.role}"><span class="role">${msg.role === 'user' ? '你' : 'AI'}</span><span class="content">${escapeHtml(msg.content)}</span></div>`).join('') || '<div class="muted">暂无对话。可以直接问：现在能不能买？如果已持仓止盈放哪里？</div>';
  el('chatLog').scrollTop = el('chatLog').scrollHeight;
}

function escapeHtml(text) {
  return String(text || '').replace(/[&<>"']/g, (ch) => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  }[ch]));
}

function compactContext(data) {
  if (!data) return {};
  const main = data.main || {};
  return {
    symbol: data.symbol,
    price: data.price,
    zone: data.zone,
    action: data.action,
    buy: main.buy && main.buy.verdict,
    sell: main.sell && main.sell.verdict,
    confirm: main.confirm && main.confirm.verdict,
    confirm_score: data.confirm_score,
    premomentum: (main.premomentum || data.premomentum) && (main.premomentum || data.premomentum).verdict,
    momentum: (main.momentum || data.momentum) && (main.momentum || data.momentum).verdict,
    supports: main.supports,
    resistances: main.resistances,
    peers: main.peers,
    market: main.market,
  };
}

async function askAi(question) {
  const payload = {
    message: question,
    context: latestData || compactContext(latestData),
    history: chatMessages.slice(-12),
  };
  try {
    return await askAiStream(payload);
  } catch (_) {
    // Fall back to the existing JSON endpoint when streaming is not available.
  }
  try {
    const res = await fetch(CHAT_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    const suffix = data.ai_error ? `\n\n后端已连接，但外部 AI 请求失败：${data.ai_error}` : '';
    return (data.reply || data.message || 'AI 没有返回内容。') + suffix;
  } catch (err) {
    return localContextReply(question, err);
  }
}

async function askAiStream(payload) {
  const res = await fetch(CHAT_STREAM_URL, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Accept': 'text/event-stream' },
    body: JSON.stringify(payload),
  });
  if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let reply = '';
  let aiError = '';
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parsed = consumeSseBuffer(buffer);
    buffer = parsed.rest;
    for (const event of parsed.events) {
      if (event.event === 'delta') {
        const text = event.data.text || '';
        reply += text;
        updateStreamingReply(reply || '正在结合当前监控数据分析...');
      } else if (event.event === 'error') {
        aiError = event.data.message || aiError;
      } else if (event.event === 'done') {
        if (event.data.reply) reply = event.data.reply;
        if (event.data.ai_error) aiError = event.data.ai_error;
      }
    }
  }
  buffer += decoder.decode();
  const tail = consumeSseBuffer(buffer + '\n\n');
  for (const event of tail.events) {
    if (event.event === 'delta') {
      reply += event.data.text || '';
      updateStreamingReply(reply);
    } else if (event.event === 'done') {
      if (event.data.reply) reply = event.data.reply;
      if (event.data.ai_error) aiError = event.data.ai_error;
    }
  }
  if (!reply) throw new Error('empty stream');
  const suffix = aiError ? `\n\n后端已连接，但外部 AI 请求失败：${aiError}` : '';
  return reply + suffix;
}

function consumeSseBuffer(buffer) {
  const events = [];
  let rest = buffer;
  let sep = rest.indexOf('\n\n');
  while (sep !== -1) {
    const raw = rest.slice(0, sep);
    rest = rest.slice(sep + 2);
    const event = parseSseEvent(raw);
    if (event) events.push(event);
    sep = rest.indexOf('\n\n');
  }
  return { events, rest };
}

function parseSseEvent(raw) {
  const lines = raw.split(/\r?\n/);
  let event = 'message';
  const dataLines = [];
  lines.forEach((line) => {
    if (line.startsWith('event:')) event = line.slice(6).trim();
    if (line.startsWith('data:')) dataLines.push(line.slice(5).trimStart());
  });
  if (!dataLines.length) return null;
  const dataText = dataLines.join('\n');
  try {
    return { event, data: JSON.parse(dataText) };
  } catch (_) {
    return { event, data: { text: dataText } };
  }
}

function updateStreamingReply(content) {
  if (!chatMessages.length) return;
  chatMessages[chatMessages.length - 1] = { role: 'assistant', content };
  renderChat();
}

function localContextReply(_question, err) {
  const data = latestData || {};
  const main = data.main || {};
  const buy = main.buy || {};
  const sell = main.sell || {};
  const supports = main.supports || [];
  const resistances = main.resistances || [];
  const firstSupport = supports[0] ? `${supports[0].level}=${fmt(supports[0].price)}(${Number(supports[0].dist_pct).toFixed(1)}%)` : '暂无支撑数据';
  const firstResistance = resistances[0] ? `${resistances[0].level}=${fmt(resistances[0].price)}(${Number(resistances[0].dist_pct).toFixed(1)}%)` : '暂无阻力数据';
  return [
    `AI 接口暂时不可用，以下是页面本地监控摘要：`,
    `动作：${fmt(data.action)}。`,
    `买入：${fmt(buy.verdict)}；卖出：${fmt(sell.verdict)}。`,
    `短线参考：支撑 ${firstSupport}；阻力 ${firstResistance}。`,
    `错误：${err ? err.message : 'unknown'}`,
  ].join('\n');
}

async function refresh() {
  const symbol = activeSymbol;
  if (refreshRequest && refreshRequest.symbol === symbol) {
    return refreshRequest.promise;
  }
  if (refreshRequest) {
    refreshRequest.controller.abort();
  }

  const seq = ++refreshSeq;
  const controller = new AbortController();
  const promise = (async () => {
    try {
      const url = `${STOCK_FEED_URL}?symbol=${encodeURIComponent(symbol)}&t=${Date.now()}`;
      const res = await fetch(url, { cache: 'no-store', signal: controller.signal });
      if (seq !== refreshSeq || symbol !== activeSymbol) return;
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (seq !== refreshSeq || symbol !== activeSymbol) return;
      if (data.error) throw new Error(data.error);
      render(data);
    } catch (err) {
      if (err.name === 'AbortError') return;
      if (seq !== refreshSeq || symbol !== activeSymbol) return;
      el('updatedAt').textContent = `读取失败: ${err.message}`;
    } finally {
      if (refreshRequest && refreshRequest.controller === controller) {
        refreshRequest = null;
      }
    }
  })();
  refreshRequest = { symbol, controller, promise };
  return promise;
}

document.querySelectorAll('.tab').forEach((btn) => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach((x) => x.classList.remove('active'));
    document.querySelectorAll('.tab-page').forEach((x) => x.classList.remove('active'));
    btn.classList.add('active');
    el(`tab-${btn.dataset.tab}`).classList.add('active');
  });
});

el('chatForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  const input = el('chatInput');
  const text = input.value.trim();
  if (!text) return;
  input.value = '';
  chatMessages.push({ role: 'user', content: text });
  chatMessages.push({ role: 'assistant', content: '正在结合当前监控数据分析...' });
  saveChat();
  renderChat();
  const reply = await askAi(text);
  chatMessages[chatMessages.length - 1] = { role: 'assistant', content: reply };
  saveChat();
  renderChat();
});

el('clearChat').addEventListener('click', () => {
  chatMessages = [];
  saveChat();
  renderChat();
});

el('openStockModal').addEventListener('click', openStockModal);
el('closeStockModal').addEventListener('click', closeStockModal);
el('stockModal').addEventListener('click', (event) => {
  if (event.target === el('stockModal')) closeStockModal();
});
el('stockSearchInput').addEventListener('input', (event) => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => searchStocks(event.target.value), 250);
});
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && !el('stockModal').hidden) closeStockModal();
});

renderStockTabs();
renderChat();
refresh();
hydrateWatchlistFromServer();
setInterval(refresh, REFRESH_MS);

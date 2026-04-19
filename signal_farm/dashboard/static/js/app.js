/**
 * Main app: routing, sidebar, search, trade list, TF selector.
 */
import * as API from "./api.js";
import { renderPriceChart, zoomToTrade, highlightTradeOnChart } from "./chart-price.js";
import { renderEquityChart } from "./chart-equity.js";
import { renderStats } from "./chart-stats.js";
import { renderCorrelationMatrix, renderRollingCorrelation } from "./chart-correlation.js";
import { renderCompareEquityChart, renderCompareMetrics } from "./chart-compare.js";

let currentBacktest = null;
let backtestList = [];
let backtestCache = {};
let activeTradeIdx = null;
let activeTab = "chart";

// ─── ROUTER ───────────────────────────────────────────────────
// Routes: #/backtest/NAME  |  #/correlation  |  #/compare
function navigate(path) {
  window.location.hash = path;
}

function parseHash() {
  const hash = window.location.hash.replace(/^#\/?/, ""); // strip #/
  const [view, ...rest] = hash.split("/");
  return { view: view || "", param: rest.join("/") };
}

async function handleRoute() {
  const { view, param } = parseHash();

  if (view === "backtest" && param) {
    await selectBacktest(param, false); // false = don't push history again
  } else if (view === "correlation") {
    showView("correlation", false);
  } else if (view === "compare") {
    showView("compare", false);
  }
  // empty hash = stay on landing
}

// ─── TABS ──────────────────────────────────────────────────────
function setupTabs() {
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => switchTab(btn.dataset.tab));
  });
}

function switchTab(tab) {
  activeTab = tab;
  document.querySelectorAll(".tab-btn").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  document.querySelectorAll(".tab-panel").forEach((p) => p.classList.toggle("hidden", p.id !== `tab-${tab}`));

  if (tab === "equity" && currentBacktest) {
    // Re-render equity when switching to its tab so sizing is correct
    renderEquityChart(currentBacktest, selectTrade);
  }
  if (tab === "stats" && currentBacktest) {
    renderStats(currentBacktest);
  }
}

// ─── INIT ──────────────────────────────────────────────────────
async function init() {
  setupSidebar();
  setupTabs();

  try {
    backtestList = await API.listBacktests();
    renderBacktestList(backtestList);
    populateCompareSelect();
  } catch (err) {
    console.error("Failed to load backtest list:", err);
  }

  document.getElementById("compare-btn").addEventListener("click", loadCompareView);
  document.getElementById("normalize-checkbox").addEventListener("change", loadCompareView);

  document.getElementById("search-input").addEventListener("input", (e) => {
    const q = e.target.value.toLowerCase();
    const filtered = backtestList.filter((n) => n.toLowerCase().includes(q));
    renderBacktestList(filtered);
  });

  // Handle browser back/forward
  window.addEventListener("hashchange", handleRoute);

  // Handle initial URL on page load
  await handleRoute();
}

// ─── SIDEBAR ──────────────────────────────────────────────────
function setupSidebar() {
  const toggle = document.getElementById("sidebar-toggle");
  const sidebar = document.getElementById("sidebar");

  toggle.addEventListener("click", () => {
    const collapsed = sidebar.classList.toggle("collapsed");
    document.body.classList.toggle("sidebar-collapsed", collapsed);
  });
}

// ─── BACKTEST LIST ─────────────────────────────────────────────
function renderBacktestList(list) {
  const container = document.getElementById("backtest-list");
  if (list.length === 0) {
    container.innerHTML = '<p class="muted" style="font-size:0.85em;padding:0.4rem 0.2rem;">No results</p>';
    return;
  }
  container.innerHTML = list.map((name) =>
    `<div class="backtest-item" data-name="${name}" onclick="window.app.selectBacktest('${name}')">${name}</div>`
  ).join("");
}

// ─── SELECT BACKTEST ───────────────────────────────────────────
async function selectBacktest(name, pushHistory = true) {
  try {
    let data = backtestCache[name];
    if (!data) {
      data = await API.getBacktest(name);
      backtestCache[name] = data;
    }
    currentBacktest = data;
    activeTradeIdx = null;
    if (pushHistory) navigate(`/backtest/${name}`);
    updateUI();
    showView("backtest", false);
  } catch (err) {
    console.error("Failed to load backtest:", err);
  }
}

function updateUI() {
  if (!currentBacktest) return;
  const meta = currentBacktest.meta || {};

  // Header
  document.getElementById("backtest-title").textContent =
    `${meta.ticker} · Variant ${meta.variant}`;
  document.getElementById("backtest-meta").textContent =
    `${meta.period_start} → ${meta.period_end}  |  ${meta.bars_count} bars`;

  // Highlight sidebar item
  document.querySelectorAll(".backtest-item").forEach((el) => {
    el.classList.toggle("active", el.dataset.name === `${meta.ticker}_${meta.variant}_${meta.period}`);
  });

  // TF selector
  renderTfSelector(currentBacktest);

  // Reset to chart tab on new backtest load
  switchTab("chart");

  // Components — only render active tab content immediately
  renderPriceChart(currentBacktest, onMarkerClick);
  renderTradeList(currentBacktest);
  renderParams(currentBacktest);
}

// ─── TF SELECTOR ──────────────────────────────────────────────
function getTimeframeLabel(data) {
  if (!data.ohlc || data.ohlc.length < 2) return "?";
  const diff = (data.ohlc[1].t - data.ohlc[0].t) / 60;
  const map = { 30: "30m", 60: "1h", 240: "4h", 1440: "1d" };
  return map[diff] || `${diff}m`;
}

function renderTfSelector(data) {
  const wrap = document.getElementById("tf-selector");
  const current = getTimeframeLabel(data);
  const allTf = ["30m", "1h", "4h", "1d"];

  wrap.innerHTML = allTf.map((tf) => {
    const active = tf === current ? "active" : "";
    const title = tf !== current ? "Export this TF first" : "";
    const disabled = tf !== current ? "disabled-tf" : "";
    return `<button class="tf-btn ${active} ${disabled}" title="${title}">${tf}</button>`;
  }).join("");

  // Active TF is clickable (no-op, already loaded); others show tooltip
  wrap.querySelectorAll(".tf-btn:not(.active)").forEach((btn) => {
    btn.style.opacity = "0.35";
    btn.style.cursor = "not-allowed";
  });
}

// ─── TRADE LIST ────────────────────────────────────────────────
function renderTradeList(data) {
  const container = document.getElementById("trade-list");
  const countEl = document.getElementById("trades-count");
  const trades = data.trades || [];

  countEl.textContent = `(${trades.length})`;

  if (trades.length === 0) {
    container.innerHTML = '<p class="muted" style="padding:1rem;font-size:0.85em;">No trades</p>';
    return;
  }

  container.innerHTML = trades.map((t, idx) => {
    const date = new Date(t.entry_time * 1000).toLocaleDateString("en-GB", {
      day: "2-digit", month: "short", year: "2-digit",
    });
    const dirClass = t.direction === "LONG" ? "long" : "short";
    const pnlClass = t.pnl_r > 0 ? "win" : "loss";
    const pnlText = `${t.pnl_r > 0 ? "+" : ""}${t.pnl_r.toFixed(2)}R`;

    return `
      <div class="trade-row" data-idx="${idx}" onclick="window.app.selectTrade(${idx})">
        <span class="tr-date" style="grid-column:1/-1">${date}</span>
        <span class="tr-dir ${dirClass}">${t.direction}</span>
        <span class="tr-exit">${t.exit_reason}</span>
        <span class="tr-pnl ${pnlClass}">${pnlText}</span>
      </div>
    `;
  }).join("");
}

function selectTrade(idx) {
  activeTradeIdx = idx;

  // Highlight row
  document.querySelectorAll(".trade-row").forEach((row) => {
    row.classList.toggle("active", parseInt(row.dataset.idx) === idx);
  });

  // Tell chart to highlight + zoom
  highlightTradeOnChart(idx);
  zoomToTrade(idx);
}

// Called from chart-price.js when a marker is clicked
function onMarkerClick(idx) {
  activeTradeIdx = idx;

  // Highlight row in list and scroll to it (no page scroll)
  const rows = document.querySelectorAll(".trade-row");
  rows.forEach((row) => {
    row.classList.toggle("active", parseInt(row.dataset.idx) === idx);
  });

  const activeRow = document.querySelector(`.trade-row[data-idx="${idx}"]`);
  if (activeRow) {
    activeRow.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }
}

// ─── VIEWS ────────────────────────────────────────────────────
function showView(viewName, pushHistory = true) {
  document.querySelectorAll(".view").forEach((v) => v.classList.add("hidden"));
  const view = document.getElementById(`view-${viewName}`);
  if (view) view.classList.remove("hidden");

  if (pushHistory && viewName !== "backtest") navigate(`/${viewName}`);
  if (viewName === "correlation") loadCorrelation();
}

async function loadCorrelation() {
  try {
    const data = await API.getCorrelation();
    if (data.error) {
      document.getElementById("correlation-heatmap").innerHTML =
        `<div style="color:var(--accent-red);padding:1rem;">${data.error}</div>`;
      return;
    }
    renderCorrelationMatrix(data);
    renderRollingCorrelation(data);
  } catch (err) {
    console.error("Failed to load correlation:", err);
  }
}

function populateCompareSelect() {
  const select = document.getElementById("compare-select");
  select.innerHTML = backtestList.map((name) =>
    `<option value="${name}">${name}</option>`
  ).join("");
}

async function loadCompareView() {
  showView("compare");
  const select = document.getElementById("compare-select");
  const selected = Array.from(select.selectedOptions).map((o) => o.value);
  if (selected.length === 0) {
    document.getElementById("compare-metrics-table").innerHTML =
      '<p class="muted">Select at least one backtest</p>';
    return;
  }
  const backtests = [];
  for (const name of selected) {
    let data = backtestCache[name];
    if (!data) {
      data = await API.getBacktest(name);
      backtestCache[name] = data;
    }
    backtests.push(data);
  }
  const normalize = document.getElementById("normalize-checkbox").checked;
  renderCompareEquityChart(backtests, normalize);
  renderCompareMetrics(backtests);
}

// ─── TRADE MODAL ──────────────────────────────────────────────
function openTradeModal() {
  if (!currentBacktest) return;
  const trades = currentBacktest.trades || [];
  const meta = currentBacktest.meta || {};

  document.getElementById("modal-title").textContent =
    `All Trades — ${meta.ticker} ${meta.variant} (${trades.length})`;

  const tbody = document.getElementById("modal-trade-body");
  tbody.innerHTML = trades.map((t, idx) => {
    const entryDate = new Date(t.entry_time * 1000).toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "2-digit" });
    const exitDate  = new Date(t.exit_time  * 1000).toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "2-digit" });
    const dirClass  = t.direction === "LONG" ? "long" : "short";
    const pnlClass  = t.pnl_r > 0 ? "win" : "loss";
    const pnlText   = `${t.pnl_r > 0 ? "+" : ""}${t.pnl_r.toFixed(2)}R`;
    const active    = idx === activeTradeIdx ? "active" : "";
    return `<tr class="${active}" onclick="window.app.selectTradeFromModal(${idx})">
      <td>${idx + 1}</td>
      <td>${entryDate}</td>
      <td>${exitDate}</td>
      <td class="tr-dir ${dirClass}">${t.direction}</td>
      <td>${t.entry_price.toFixed(2)}</td>
      <td>${t.exit_price.toFixed(2)}</td>
      <td style="color:var(--accent-red)">${t.stop.toFixed(2)}</td>
      <td style="color:var(--accent-green)">${t.target.toFixed(2)}</td>
      <td class="tr-pnl ${pnlClass}">${pnlText}</td>
      <td class="${pnlClass}">${t.pnl_pct.toFixed(2)}%</td>
      <td>${t.exit_reason}</td>
      <td>${t.signal_score || "—"}</td>
    </tr>`;
  }).join("");

  document.getElementById("trades-modal").classList.remove("hidden");
}

function closeTradeModal() {
  document.getElementById("trades-modal").classList.add("hidden");
}

function selectTradeFromModal(idx) {
  closeTradeModal();
  selectTrade(idx);
  // Scroll panel trade into view
  const row = document.querySelector(`.trade-row[data-idx="${idx}"]`);
  if (row) row.scrollIntoView({ block: "nearest", behavior: "smooth" });
}

// ─── PARAMS ───────────────────────────────────────────────────
function renderParams(data) {
  const container = document.getElementById("params-panel");
  if (!container) return;
  const params = data?.params;
  if (!params) {
    container.innerHTML = '<p class="muted" style="padding:1rem;">No params data — re-export the backtest.</p>';
    return;
  }

  const groups = [
    {
      title: "Timeframes",
      keys: [
        ["Director TF", params.director_interval],
        ["Executor TF", params.executor_interval],
      ],
    },
    {
      title: "Trend / MA",
      keys: [
        ["SMA Fast", params.sma_fast],
        ["SMA Slow", params.sma_slow],
        ["Keltner EMA", params.keltner_ema],
        ["Keltner ATR", params.keltner_atr],
        ["Keltner Mult", params.keltner_mult],
      ],
    },
    {
      title: "RSI",
      keys: [
        ["RSI Period", params.rsi_period],
        ["RSI Zone (long)", params.rsi_zone],
      ],
    },
    {
      title: "Risk / Sizing",
      keys: [
        ["ATR Period", params.atr_period],
        ["ATR Stop Mult", params.atr_stop_mult],
        ["RR Ratio", params.rr_ratio],
        ["Min Score", params.min_score],
        ["Max Concurrent", params.max_concurrent],
      ],
    },
    {
      title: "Filters",
      keys: [
        ["Allowed Dirs", Array.isArray(params.allowed_directions) ? params.allowed_directions.join(", ") : params.allowed_directions],
        ["Pullback Lookback", params.pullback_lookback],
        ["Keltner Lookback", params.keltner_lookback],
      ],
    },
  ];

  container.innerHTML = groups.map((g) => {
    const rows = g.keys
      .filter(([, v]) => v !== undefined && v !== null)
      .map(([k, v]) => `
        <div class="params-row">
          <span class="p-key">${k}</span>
          <span class="p-value">${v}</span>
        </div>
      `).join("");
    if (!rows) return "";
    return `
      <div class="params-group">
        <div class="params-group-title">${g.title}</div>
        ${rows}
      </div>
    `;
  }).join("");
}

// ─── EXPORTS ──────────────────────────────────────────────────
window.app = { selectBacktest, selectTrade, showView, loadCompareView, openTradeModal, closeTradeModal, selectTradeFromModal };

init();

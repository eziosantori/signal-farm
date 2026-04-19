/**
 * Statistics panel: metric cards + mini charts.
 */

const miniChartInstances = {};

function destroyMiniCharts() {
  Object.values(miniChartInstances).forEach((c) => c.destroy());
  Object.keys(miniChartInstances).forEach((k) => delete miniChartInstances[k]);
}

export function renderStats(data) {
  const container = document.getElementById("stats-panel");
  if (!data || !data.metrics) { container.innerHTML = ""; return; }

  destroyMiniCharts();

  const m = data.metrics;
  const trades = data.trades || [];

  // ── Avg score: winners vs losers ───────────────────────────
  const winners = trades.filter((t) => t.pnl_r > 0);
  const losers  = trades.filter((t) => t.pnl_r <= 0);
  const avgScoreWin  = winners.length ? winners.reduce((s, t) => s + (t.signal_score || 0), 0) / winners.length : 0;
  const avgScoreLoss = losers.length  ? losers.reduce((s, t)  => s + (t.signal_score || 0), 0) / losers.length  : 0;

  // ── Metric cards ────────────────────────────────────────────
  const stats = [
    { label: "Total Return",   value: `${m.total_return_pct}%`,  neg: m.total_return_pct < 0 },
    { label: "Max Drawdown",   value: `${m.max_drawdown_pct}%`,  neg: true },
    { label: "Sharpe",         value: `${m.sharpe_ratio}` },
    { label: "Profit Factor",  value: `${m.profit_factor}` },
    { label: "Total Trades",   value: `${m.total_trades}` },
    { label: "Win Rate",       value: `${m.win_rate}%` },
    { label: "Expectancy R",   value: `${m.expectancy_r}`,       neg: m.expectancy_r < 0 },
    { label: "Avg Win R",      value: `${m.avg_win_r}` },
    { label: "Avg Loss R",     value: `${m.avg_loss_r}`,         neg: true },
    { label: "Max Cons. Wins", value: `${m.max_consecutive_wins}` },
    { label: "Max Cons. Loss", value: `${m.max_consecutive_losses}`, neg: true },
    { label: "Avg Duration",   value: `${m.avg_trade_duration_bars}b` },
    { label: "Score Winners",  value: avgScoreWin.toFixed(1) },
    { label: "Score Losers",   value: avgScoreLoss.toFixed(1),   neg: true },
  ];

  const cards = stats.map(({ label, value, neg }) => `
    <div class="stat-card">
      <div class="stat-label">${label}</div>
      <div class="stat-value${neg ? " negative" : ""}">${value}</div>
    </div>
  `).join("");

  // ── Mini chart containers ────────────────────────────────────
  const miniSection = `
    <div class="mini-charts-row">
      <div class="mini-chart-wrap">
        <div class="mini-chart-title">PnL Distribution (R)</div>
        <canvas id="miniPnl"></canvas>
      </div>
      <div class="mini-chart-wrap">
        <div class="mini-chart-title">Signal Score Distribution</div>
        <canvas id="miniScore"></canvas>
      </div>
      <div class="mini-chart-wrap">
        <div class="mini-chart-title">Exit Reasons</div>
        <canvas id="miniExit"></canvas>
      </div>
    </div>
  `;

  container.innerHTML = cards + miniSection;

  // Render mini charts after DOM is updated
  requestAnimationFrame(() => {
    renderPnlDistribution(trades);
    renderScoreDistribution(trades);
    renderExitPie(trades);
  });
}

// ── PnL histogram ──────────────────────────────────────────────
function renderPnlDistribution(trades) {
  const canvas = document.getElementById("miniPnl");
  if (!canvas || !trades.length) return;

  const pnls = trades.map((t) => t.pnl_r);
  const min = Math.floor(Math.min(...pnls));
  const max = Math.ceil(Math.max(...pnls));

  // Buckets of 0.5R width
  const bucketSize = 0.5;
  const buckets = {};
  for (let b = min; b <= max; b += bucketSize) {
    buckets[b.toFixed(1)] = 0;
  }
  pnls.forEach((r) => {
    const key = (Math.floor(r / bucketSize) * bucketSize).toFixed(1);
    if (buckets[key] !== undefined) buckets[key]++;
  });

  const labels = Object.keys(buckets);
  const counts = Object.values(buckets);
  const colors = labels.map((l) => parseFloat(l) >= 0 ? "rgba(38,166,154,0.7)" : "rgba(239,83,80,0.7)");

  miniChartInstances.pnl = new Chart(canvas.getContext("2d"), {
    type: "bar",
    data: { labels, datasets: [{ data: counts, backgroundColor: colors, borderWidth: 0 }] },
    options: miniChartOptions("R", false),
  });
}

// ── Score histogram ────────────────────────────────────────────
function renderScoreDistribution(trades) {
  const canvas = document.getElementById("miniScore");
  if (!canvas || !trades.length) return;

  const scored = trades.filter((t) => t.signal_score);
  if (!scored.length) return;

  // Buckets 0-10, 10-20 … 90-100
  const buckets = Array.from({ length: 10 }, (_, i) => ({ label: `${i * 10}`, wins: 0, losses: 0 }));
  scored.forEach((t) => {
    const idx = Math.min(Math.floor(t.signal_score / 10), 9);
    if (t.pnl_r > 0) buckets[idx].wins++;
    else              buckets[idx].losses++;
  });

  miniChartInstances.score = new Chart(canvas.getContext("2d"), {
    type: "bar",
    data: {
      labels: buckets.map((b) => b.label),
      datasets: [
        { label: "Win",  data: buckets.map((b) => b.wins),   backgroundColor: "rgba(38,166,154,0.7)", borderWidth: 0 },
        { label: "Loss", data: buckets.map((b) => b.losses), backgroundColor: "rgba(239,83,80,0.7)",  borderWidth: 0 },
      ],
    },
    options: miniChartOptions("Score", true),
  });
}

// ── Exit reason pie ────────────────────────────────────────────
function renderExitPie(trades) {
  const canvas = document.getElementById("miniExit");
  if (!canvas || !trades.length) return;

  const reasons = {};
  trades.forEach((t) => {
    reasons[t.exit_reason] = (reasons[t.exit_reason] || 0) + 1;
  });

  const palette = { target: "#26a69a", stop: "#ef5350", forced: "#ffa726", end_of_data: "#5a6380" };
  const labels = Object.keys(reasons);
  const values = Object.values(reasons);
  const colors = labels.map((l) => palette[l] || "#4fc3f7");

  miniChartInstances.exit = new Chart(canvas.getContext("2d"), {
    type: "doughnut",
    data: {
      labels,
      datasets: [{ data: values, backgroundColor: colors, borderWidth: 0 }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { position: "right", labels: { color: "#808080", boxWidth: 10, font: { size: 10 } } },
        tooltip: miniTooltip(),
      },
    },
  });
}

// ── Shared option helpers ──────────────────────────────────────
function miniChartOptions(xLabel, stacked) {
  return {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: stacked
        ? { labels: { color: "#808080", boxWidth: 8, font: { size: 10 } } }
        : { display: false },
      tooltip: miniTooltip(),
    },
    scales: {
      x: {
        stacked,
        grid:  { display: false },
        ticks: { color: "#5a6380", font: { size: 10 }, maxRotation: 0 },
      },
      y: {
        stacked,
        grid:  { color: "rgba(22,28,48,0.8)" },
        ticks: { color: "#5a6380", font: { size: 10 } },
      },
    },
  };
}

function miniTooltip() {
  return {
    backgroundColor: "rgba(6,8,16,0.92)",
    titleColor: "#c8cdd8",
    bodyColor:  "#c8cdd8",
    borderColor: "#161c30",
    borderWidth: 1,
    padding: 6,
  };
}

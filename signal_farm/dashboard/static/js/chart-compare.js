/**
 * Multi-backtest comparison charts and metrics.
 */

let compareChartInstance = null;

const colors = [
  "#4fc3f7",
  "#26a69a",
  "#ff9800",
  "#e91e63",
  "#9c27b0",
  "#673ab7",
];

export function renderCompareEquityChart(backtests, normalize = false) {
  const container = document.getElementById("compare-equity-chart");
  const canvas = container.querySelector("canvas");

  if (!canvas) {
    container.innerHTML = '<canvas id="compareCanvas"></canvas>';
  }

  if (backtests.length === 0) return;

  if (compareChartInstance) {
    compareChartInstance.destroy();
  }

  const ctx = document.getElementById("compareCanvas").getContext("2d");

  // Find common date range
  const allTimestamps = new Set();
  backtests.forEach((bt) => {
    if (bt.equity) {
      bt.equity.forEach((point) => {
        allTimestamps.add(point.t);
      });
    }
  });

  const sortedTimestamps = Array.from(allTimestamps).sort((a, b) => a - b);
  const labels = sortedTimestamps.map((t) =>
    new Date(t * 1000).toLocaleDateString()
  );

  const datasets = backtests.map((bt, idx) => {
    const meta = bt.meta || {};
    const equity = bt.equity || [];

    // Create a map of timestamp -> value
    const equityMap = {};
    equity.forEach((point) => {
      equityMap[point.t] = point.v;
    });

    // Fill in values for each timestamp, forward-filling gaps
    let lastValue = equity[0]?.v || 100000;
    const values = sortedTimestamps.map((t) => {
      if (equityMap[t]) {
        lastValue = equityMap[t];
        return equityMap[t];
      }
      return lastValue;
    });

    // Normalize if requested
    let displayValues = values;
    if (normalize) {
      const startValue = values[0];
      displayValues = values.map((v) => (v / startValue) * 100);
    }

    return {
      label: `${meta.ticker} ${meta.variant}`,
      data: displayValues,
      borderColor: colors[idx % colors.length],
      backgroundColor: `rgba(${colors[idx % colors.length].slice(1).match(/.{1,2}/g).map((x) => parseInt(x, 16)).join(", ")}, 0.1)`,
      borderWidth: 2,
      fill: true,
      pointRadius: 0,
      pointHoverRadius: 4,
      tension: 0.1,
    };
  });

  compareChartInstance = new Chart(ctx, {
    type: "line",
    data: {
      labels: labels,
      datasets: datasets,
    },
    options: {
      responsive: true,
      maintainAspectRatio: true,
      interaction: {
        mode: "index",
        intersect: false,
      },
      plugins: {
        legend: {
          labels: {
            color: "#e0e0e0",
          },
        },
        tooltip: {
          backgroundColor: "rgba(26, 26, 46, 0.9)",
          titleColor: "#e0e0e0",
          bodyColor: "#e0e0e0",
          borderColor: "#2a2a4a",
          borderWidth: 1,
          padding: 8,
          callbacks: {
            label: function (context) {
              const value = context.parsed.y;
              return `${context.dataset.label}: ${normalize ? value.toFixed(2) : "$" + value.toLocaleString()}`;
            },
          },
        },
      },
      scales: {
        x: {
          grid: {
            color: "rgba(42, 42, 74, 0.3)",
          },
          ticks: {
            color: "#a0a0a0",
            maxTicksLimit: 10,
          },
        },
        y: {
          grid: {
            color: "rgba(42, 42, 74, 0.3)",
          },
          ticks: {
            color: "#a0a0a0",
            callback: function (value) {
              return normalize ? value.toFixed(0) : "$" + value.toLocaleString();
            },
          },
        },
      },
    },
  });
}

export function renderCompareMetrics(backtests) {
  const container = document.getElementById("compare-metrics-table");

  if (backtests.length === 0) {
    container.innerHTML = "<p>Select backtests to compare</p>";
    return;
  }

  const metrics = [
    "total_return_pct",
    "max_drawdown_pct",
    "sharpe_ratio",
    "profit_factor",
    "total_trades",
    "win_rate",
    "avg_rr",
    "expectancy_r",
  ];

  const rows = metrics.map((metric) => {
    const cells = backtests.map((bt) => {
      const val = bt.metrics?.[metric] ?? "-";
      return `<td>${typeof val === "number" ? val.toFixed(2) : val}</td>`;
    });
    return `<tr><th>${metric}</th>${cells.join("")}</tr>`;
  });

  const headers = backtests
    .map((bt) => `<th>${bt.meta.ticker} ${bt.meta.variant}</th>`)
    .join("");

  const html = `
    <h2>Metrics Comparison</h2>
    <div class="trade-table-container">
      <table>
        <thead>
          <tr><th>Metric</th>${headers}</tr>
        </thead>
        <tbody>${rows.join("")}</tbody>
      </table>
    </div>
  `;

  container.innerHTML = html;
}

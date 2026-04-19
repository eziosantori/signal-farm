/**
 * Equity curve + drawdown + trade exit markers.
 */

let equityChartInstance = null;
let tradeIndexByEquityIdx = {};

export function renderEquityChart(data, onTradeClick = null) {
  const container = document.getElementById("equity-chart");
  if (!data || !data.equity) return;

  if (equityChartInstance) { equityChartInstance.destroy(); equityChartInstance = null; }
  container.innerHTML = "";

  // Create canvas sized to container
  const canvas = document.createElement("canvas");
  const rect = container.getBoundingClientRect();
  canvas.width  = rect.width  || container.offsetWidth  || 800;
  canvas.height = rect.height || container.offsetHeight || 320;
  canvas.style.display = "block";
  container.appendChild(canvas);

  const ctx = canvas.getContext("2d");

  const equityData    = data.equity   || [];
  const drawdownData  = data.drawdown || [];
  const trades        = data.trades   || [];

  const labels         = equityData.map((p) => new Date(p.t * 1000).toLocaleDateString());
  const equityValues   = equityData.map((p) => p.v);
  const drawdownValues = drawdownData.map((p) => p.v);

  // timestamp → equity array index
  const tsToIdx = {};
  equityData.forEach((p, i) => { tsToIdx[p.t] = i; });

  function closestIdx(ts) {
    if (tsToIdx[ts] !== undefined) return tsToIdx[ts];
    for (let d = 1; d <= 48; d++) {
      if (tsToIdx[ts + d * 1800] !== undefined) return tsToIdx[ts + d * 1800];
    }
    return null;
  }

  const pointRadii  = equityValues.map(() => 0);
  const pointColors = equityValues.map(() => "transparent");
  tradeIndexByEquityIdx = {};

  trades.forEach((t, tradeIdx) => {
    const idx = closestIdx(t.exit_time);
    if (idx === null) return;
    pointRadii[idx]  = 5;
    pointColors[idx] = t.pnl_r > 0 ? "#26a69a" : "#ef5350";
    tradeIndexByEquityIdx[idx] = tradeIdx;
  });

  const darkTooltip = {
    backgroundColor: "rgba(6,8,16,0.92)",
    titleColor: "#c8cdd8",
    bodyColor:  "#c8cdd8",
    borderColor: "#161c30",
    borderWidth: 1,
    padding: 8,
  };

  equityChartInstance = new Chart(ctx, {
    data: {
      labels,
      datasets: [
        {
          type: "line",
          label: "Equity",
          data: equityValues,
          borderColor: "#4fc3f7",
          backgroundColor: "rgba(79,195,247,0.07)",
          borderWidth: 2,
          fill: true,
          pointRadius: pointRadii,
          pointBackgroundColor: pointColors,
          pointHoverRadius: pointRadii.map((r) => r > 0 ? 7 : 3),
          yAxisID: "y",
        },
        {
          type: "line",
          label: "Drawdown %",
          data: drawdownValues,
          borderColor: "#ef5350",
          backgroundColor: "rgba(239,83,80,0.12)",
          borderWidth: 1,
          fill: true,
          pointRadius: 0,
          pointHoverRadius: 3,
          yAxisID: "y1",
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { labels: { color: "#808080", boxWidth: 10, font: { size: 11 } } },
        tooltip: darkTooltip,
      },
      scales: {
        x: {
          grid:  { color: "rgba(22,28,48,0.8)" },
          ticks: { color: "#5a6380", maxTicksLimit: 8 },
        },
        y: {
          type: "linear",
          position: "left",
          grid:  { color: "rgba(22,28,48,0.8)" },
          ticks: { color: "#5a6380", callback: (v) => "$" + v.toLocaleString() },
        },
        y1: {
          type: "linear",
          position: "right",
          grid: { drawOnChartArea: false },
          ticks: { color: "#5a6380" },
        },
      },
    },
  });

  // Click handler — uses raw event to find nearest x point regardless of intersect
  if (onTradeClick) {
    canvas.addEventListener("click", (evt) => {
      const points = equityChartInstance.getElementsAtEventForMode(
        evt, "index", { intersect: false }, false
      );
      if (!points.length) return;
      const dataIdx = points[0].index;
      for (let d = 0; d <= 5; d++) {
        if (tradeIndexByEquityIdx[dataIdx + d] !== undefined) {
          onTradeClick(tradeIndexByEquityIdx[dataIdx + d]); return;
        }
        if (d > 0 && tradeIndexByEquityIdx[dataIdx - d] !== undefined) {
          onTradeClick(tradeIndexByEquityIdx[dataIdx - d]); return;
        }
      }
    });
  }

  // ResizeObserver: ridimensiona il chart quando il container cambia
  if (window._equityResizeObserver) window._equityResizeObserver.disconnect();
  window._equityResizeObserver = new ResizeObserver(() => {
    if (equityChartInstance) equityChartInstance.resize();
  });
  window._equityResizeObserver.observe(container);
}

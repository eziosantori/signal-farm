/**
 * Correlation matrix visualization.
 * Uses Plotly.js for heatmap.
 */

export function renderCorrelationMatrix(data) {
  const container = document.getElementById("correlation-heatmap");
  if (!data || !data.matrix) return;

  const tickers = data.tickers || [];
  const matrix = data.matrix || [];

  // Format matrix for display
  const formattedMatrix = matrix.map((row) =>
    row.map((val) => (typeof val === "number" ? val.toFixed(2) : val))
  );

  const trace = {
    z: matrix,
    x: tickers,
    y: tickers,
    type: "heatmap",
    colorscale: "RdBu",
    zmin: -1,
    zmax: 1,
    text: formattedMatrix,
    texttemplate: "%{text}",
    textfont: { size: 10 },
    colorbar: {
      title: "Correlation",
      tickcolor: "#e0e0e0",
      tickfont: { color: "#e0e0e0" },
    },
  };

  const layout = {
    title: "Equity Curve Correlation (daily returns)",
    xaxis: { side: "bottom" },
    plot_bgcolor: "rgba(26, 26, 46, 0.8)",
    paper_bgcolor: "rgba(26, 26, 46, 0.8)",
    font: {
      color: "#e0e0e0",
      family: "Arial, sans-serif",
      size: 12,
    },
    margin: { l: 150, r: 80, t: 100, b: 100 },
  };

  Plotly.newPlot(container, [trace], layout, { responsive: true });
}

export function renderRollingCorrelation(data) {
  const container = document.getElementById("rolling-correlation");
  if (!data || !data.rolling_30d) return;

  const rollingData = data.rolling_30d;
  const dates = rollingData.dates || [];
  const pairs = rollingData.pairs || {};

  if (Object.keys(pairs).length === 0) {
    container.innerHTML = "<p style='color: var(--text-secondary);'>No rolling correlation data</p>";
    return;
  }

  // Get top 5 correlations by average
  const pairAvgs = Object.entries(pairs)
    .map(([pairName, values]) => ({
      name: pairName,
      values: values,
      avg: values.reduce((a, b) => a + b, 0) / values.length,
    }))
    .sort((a, b) => Math.abs(b.avg) - Math.abs(a.avg))
    .slice(0, 5);

  const traces = pairAvgs.map((pair) => ({
    x: dates,
    y: pair.values,
    name: pair.name,
    type: "scatter",
    mode: "lines",
    line: { width: 2 },
  }));

  const layout = {
    title: "30-Day Rolling Correlation (top 5)",
    xaxis: { title: "Date" },
    yaxis: { title: "Correlation", range: [-1, 1] },
    plot_bgcolor: "rgba(26, 26, 46, 0.8)",
    paper_bgcolor: "rgba(26, 26, 46, 0.8)",
    font: {
      color: "#e0e0e0",
      family: "Arial, sans-serif",
    },
    hovermode: "x unified",
    margin: { l: 60, r: 20, t: 80, b: 60 },
    legend: {
      bgcolor: "rgba(26, 26, 46, 0.9)",
      bordercolor: "#2a2a4a",
      borderwidth: 1,
    },
  };

  Plotly.newPlot(container, traces, layout, { responsive: true });
}

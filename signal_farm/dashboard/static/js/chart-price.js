/**
 * Price chart with candlesticks, indicators, and trade markers.
 * Uses TradingView Lightweight Charts.
 */

let priceChart = null;
let rsiChart = null;
let candleSeries = null;
let rsiSeries = null;
let currentTradeLines = {};
let allTrades = [];
let onMarkerClickCallback = null;

function renderRsiChart(rsiData, priceTimeScale) {
  const container = document.getElementById("rsi-chart");
  if (!container) return;

  // Clear previous RSI chart
  if (rsiChart) {
    rsiChart.remove();
    rsiChart = null;
  }

  const { LightweightCharts } = window;
  rsiChart = LightweightCharts.createChart(container, {
    layout: {
      background: { color: "#0a0e27" },
      textColor: "#a0a0a0",
    },
    timeScale: {
      timeVisible: false,
    },
    autoSize: true,
    grid: {
      horzLines: {
        visible: false,
      },
      vertLines: {
        visible: true,
        color: "rgba(42, 42, 74, 0.3)",
      },
    },
  });

  // Hide price axis labels
  rsiChart.priceScale("right").applyOptions({
    drawBorders: false,
    drawTickMarks: false,
  });

  // Prepare data and series first
  const rsiFormattedData = rsiData.map((point) => ({ time: point.t, value: point.v }));

  rsiSeries = rsiChart.addLineSeries({ color: "#9C27B0", lineWidth: 2 });
  rsiSeries.setData(rsiFormattedData);

  // Sync timeScale (zoom/pan)
  let syncingRange = false;
  rsiChart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
    if (syncingRange || !range) return;
    syncingRange = true;
    priceTimeScale.setVisibleLogicalRange(range);
    syncingRange = false;
  });
  priceTimeScale.subscribeVisibleLogicalRangeChange((range) => {
    if (syncingRange || !range) return;
    syncingRange = true;
    rsiChart.timeScale().setVisibleLogicalRange(range);
    syncingRange = false;
  });

  // Sync crosshair
  const rsiMap = {};
  rsiFormattedData.forEach((d) => { rsiMap[d.time] = d.value; });

  priceChart.subscribeCrosshairMove((param) => {
    if (!param.time || !rsiSeries) { rsiChart.clearCrosshairPosition(); return; }
    const rsiVal = rsiMap[param.time];
    if (rsiVal !== undefined) rsiChart.setCrosshairPosition(rsiVal, param.time, rsiSeries);
  });
  rsiChart.subscribeCrosshairMove((param) => {
    if (!param.time || !candleSeries) { priceChart.clearCrosshairPosition(); return; }
    const bar = param.seriesData?.get(rsiSeries);
    if (bar !== undefined) priceChart.setCrosshairPosition(bar, param.time, candleSeries);
  });

  // Configure price scale
  rsiChart.priceScale("right").applyOptions({
    scaleMargins: {
      top: 0.1,
      bottom: 0.1,
    },
  });

  // Add subtle reference lines (30, 50, 70) without price line display
  const refLine50 = rsiChart.addLineSeries({
    color: "rgba(160, 160, 160, 0.3)",
    lineWidth: 1,
    lineStyle: 3, // dotted
    lastValueVisible: false,
  });
  const refLine30 = rsiChart.addLineSeries({
    color: "rgba(160, 160, 160, 0.2)",
    lineWidth: 1,
    lineStyle: 3,
    lastValueVisible: false,
  });
  const refLine70 = rsiChart.addLineSeries({
    color: "rgba(160, 160, 160, 0.2)",
    lineWidth: 1,
    lineStyle: 3,
    lastValueVisible: false,
  });

  // Create reference lines across all times
  const timeRange = rsiFormattedData.map((d) => d.time);
  if (timeRange.length > 0) {
    const refLineData = timeRange.map((t) => ({ time: t, value: 50 }));
    refLine50.setData(refLineData);

    const refLine30Data = timeRange.map((t) => ({ time: t, value: 30 }));
    refLine30.setData(refLine30Data);

    const refLine70Data = timeRange.map((t) => ({ time: t, value: 70 }));
    refLine70.setData(refLine70Data);
  }

  rsiChart.timeScale().fitContent();
}

function getTimeframeLabel(data) {
  if (!data.ohlc || data.ohlc.length < 2) return "unknown";
  const diff = data.ohlc[1].t - data.ohlc[0].t;
  const minutes = diff / 60;

  if (minutes === 60) return "1h";
  if (minutes === 1440) return "1d";
  if (minutes === 30) return "30m";
  if (minutes === 15) return "15m";
  if (minutes === 5) return "5m";
  return `${minutes}m`;
}

function removeTradeLines() {
  Object.values(currentTradeLines).forEach((lines) => {
    lines.forEach((line) => {
      if (line && priceChart) {
        try {
          priceChart.removeSeries(line);
        } catch (e) {
          // Already removed or invalid
        }
      }
    });
  });
  currentTradeLines = {};
}

function addTradeLines(tradeIdx) {
  // Remove previous trade's lines
  removeTradeLines();

  const trade = allTrades[tradeIdx];
  if (!trade) return;

  const stopLine = priceChart.addLineSeries({
    color: "rgba(239, 83, 80, 0.8)",
    lineWidth: 2,
  });
  stopLine.setData([
    { time: trade.entry_time, value: trade.stop },
    { time: trade.exit_time, value: trade.stop },
  ]);

  const targetLine = priceChart.addLineSeries({
    color: "rgba(38, 166, 154, 0.8)",
    lineWidth: 2,
  });
  targetLine.setData([
    { time: trade.entry_time, value: trade.target },
    { time: trade.exit_time, value: trade.target },
  ]);

  currentTradeLines[tradeIdx] = [stopLine, targetLine];
  // Don't call fitContent() - preserve user's zoom level
}

export function zoomToTrade(tradeIdx) {
  const trade = allTrades[tradeIdx];
  if (!trade || !priceChart) return;

  // Add padding bars around trade (roughly 20% of trade duration)
  const duration = trade.exit_time - trade.entry_time;
  const pad = Math.max(duration * 0.2, 3600 * 4); // at least 4h padding
  priceChart.timeScale().setVisibleRange({
    from: trade.entry_time - pad,
    to: trade.exit_time + pad,
  });
}

export function highlightTradeOnChart(tradeIdx) {
  addTradeLines(tradeIdx);
}

export function renderPriceChart(data, onMarkerClick = null) {
  onMarkerClickCallback = onMarkerClick;
  const container = document.getElementById("price-chart");
  if (!data || !data.ohlc) return;

  // Clear previous chart
  if (priceChart) {
    priceChart.remove();
    priceChart = null;
  }

  // Create chart with proper sizing and minimal grid
  const { LightweightCharts } = window;
  priceChart = LightweightCharts.createChart(container, {
    layout: {
      background: { color: "#0a0e27" },
      textColor: "#a0a0a0",
    },
    timeScale: {
      timeVisible: true,
      secondsVisible: false,
    },
    autoSize: true,
  });

  // Hide only horizontal grid lines, keep vertical lines and time labels
  priceChart.applyOptions({
    grid: {
      horzLines: {
        visible: false, // Hide horizontal grid lines
      },
      vertLines: {
        visible: true, // Keep vertical lines
        color: "rgba(42, 42, 74, 0.3)", // Subtle color
      },
    },
  });

  priceChart.priceScale("right").applyOptions({
    drawBorders: false,
    drawTickMarks: false,
  });

  priceChart.timeScale().applyOptions({
    drawBorders: false,
  });

  // Convert OHLC data
  const ohlcData = data.ohlc.map((bar) => ({
    time: bar.t,
    open: bar.o,
    high: bar.h,
    low: bar.l,
    close: bar.c,
  }));

  // Add candlestick series
  candleSeries = priceChart.addCandlestickSeries();
  candleSeries.setData(ohlcData);

  // Add SMA indicators
  const indicators = data.indicators || {};

  // SMA and Keltner use the same price scale as candlestick (they overlay price)
  if (indicators.sma_fast) {
    const smaFastSeries = priceChart.addLineSeries({
      color: "#1E88E5",
      lineWidth: 1,
      priceScaleId: "right",
      lastValueVisible: false,
      priceLineVisible: false,
    });
    const smaFastData = indicators.sma_fast.map((point) => ({
      time: point.t,
      value: point.v,
    }));
    smaFastSeries.setData(smaFastData);
  }

  if (indicators.sma_slow) {
    const smaSlowSeries = priceChart.addLineSeries({
      color: "#FF6F00",
      lineWidth: 1,
      priceScaleId: "right",
      lastValueVisible: false,
      priceLineVisible: false,
    });
    const smaSlowData = indicators.sma_slow.map((point) => ({
      time: point.t,
      value: point.v,
    }));
    smaSlowSeries.setData(smaSlowData);
  }

  // Add Keltner channels (only if available, i.e., Variant B/C)
  if (indicators.keltner_upper && indicators.keltner_lower) {
    const keltnerUpperSeries = priceChart.addLineSeries({
      color: "rgba(76, 175, 80, 0.5)",
      lineWidth: 1,
      lineStyle: 2,
      priceScaleId: "right",
      lastValueVisible: false,
      priceLineVisible: false,
    });
    const keltnerLowerSeries = priceChart.addLineSeries({
      color: "rgba(76, 175, 80, 0.5)",
      lineWidth: 1,
      lineStyle: 2,
      priceScaleId: "right",
      lastValueVisible: false,
      priceLineVisible: false,
    });

    const keltnerUpperData = indicators.keltner_upper.map((point) => ({
      time: point.t,
      value: point.v,
    }));
    const keltnerLowerData = indicators.keltner_lower.map((point) => ({
      time: point.t,
      value: point.v,
    }));

    keltnerUpperSeries.setData(keltnerUpperData);
    keltnerLowerSeries.setData(keltnerLowerData);
  }

  // Render RSI in separate chart panel if available
  if (indicators.rsi && indicators.rsi.length > 0) {
    renderRsiChart(indicators.rsi, priceChart.timeScale());
  }

  // Store trades and prepare markers
  allTrades = data.trades || [];
  const markers = [];
  const trades_by_time = {};

  allTrades.forEach((trade, idx) => {
    trades_by_time[trade.entry_time] = idx;
    trades_by_time[trade.exit_time] = idx;

    // Entry marker
    const entryShape = trade.direction === "LONG" ? "arrowUp" : "arrowDown";
    const entryColor = trade.direction === "LONG" ? "#26a69a" : "#ef5350";
    markers.push({
      time: trade.entry_time,
      position: trade.direction === "LONG" ? "belowBar" : "aboveBar",
      color: entryColor,
      shape: entryShape,
      text: "E",
      tradeIdx: idx,
      tradeType: "entry",
    });

    // Exit marker
    const exitColor = trade.pnl_r > 0 ? "#26a69a" : "#ef5350";
    const exitShape = trade.exit_reason === "target" ? "circle" : "cross";
    markers.push({
      time: trade.exit_time,
      position: "aboveBar",
      color: exitColor,
      shape: exitShape,
      text: "X",
      tradeIdx: idx,
      tradeType: "exit",
    });
  });

  if (markers.length > 0) {
    candleSeries.setMarkers(markers);

    priceChart.subscribeClick((param) => {
      if (param.point && param.time) {
        const tradeIdx = trades_by_time[param.time];
        if (tradeIdx !== undefined) {
          addTradeLines(tradeIdx);
          if (onMarkerClickCallback) onMarkerClickCallback(tradeIdx);
        }
      } else {
        removeTradeLines();
        document.querySelectorAll(".trade-row").forEach((r) => r.classList.remove("active"));
      }
    });
  }


  // Auto-scale
  priceChart.timeScale().fitContent();
}

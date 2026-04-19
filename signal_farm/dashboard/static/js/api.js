/**
 * API wrapper for dashboard endpoints.
 */

export async function listBacktests() {
  const res = await fetch("/api/list");
  return res.json();
}

export async function getBacktest(name) {
  const res = await fetch(`/api/backtest/${name}`);
  return res.json();
}

export async function getCorrelation() {
  const res = await fetch("/api/correlation");
  return res.json();
}

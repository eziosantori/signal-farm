"""
Dashboard server — serves the SPA + JSON API.

Usage:
  python main.py dashboard serve
  or: python -m dashboard.server
"""
import glob
import json
import logging
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

logger = logging.getLogger(__name__)

app = FastAPI(title="Signal Farm Dashboard")

# Resolve paths relative to this file
DASHBOARD_DIR = os.path.dirname(__file__)
DATA_DIR = os.path.join(DASHBOARD_DIR, "..", "output", "dashboard_data")
STATIC_DIR = os.path.join(DASHBOARD_DIR, "static")


@app.get("/api/list")
def list_backtests():
    """List all available backtests."""
    os.makedirs(DATA_DIR, exist_ok=True)
    files = glob.glob(os.path.join(DATA_DIR, "*.json"))
    # Filter out correlation_matrix.json
    return [
        os.path.basename(f).replace(".json", "")
        for f in files if "correlation" not in f
    ]


@app.get("/api/backtest/{name}")
def get_backtest(name: str):
    """Get single backtest data."""
    path = os.path.join(DATA_DIR, f"{name}.json")
    if not os.path.exists(path):
        return {"error": "Not found"}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@app.get("/api/correlation")
def get_correlation():
    """Get correlation matrix."""
    path = os.path.join(DATA_DIR, "correlation_matrix.json")
    if not os.path.exists(path):
        return {"error": "Correlation matrix not generated yet. Run: python main.py dashboard correlation"}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# Serve static files with no-cache headers so JS/CSS updates are always fresh
@app.get("/static/{file_path:path}")
def serve_static(file_path: str):
    full_path = os.path.join(STATIC_DIR, file_path)
    if not os.path.exists(full_path):
        return Response(status_code=404)
    response = FileResponse(full_path)
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


@app.get("/{path_name:path}")
def spa_fallback(path_name: str):
    index_path = os.path.join(STATIC_DIR, "index.html")
    response = FileResponse(index_path)
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


def start_server(port: int = 8501, open_browser: bool = False):
    """Start the dashboard server."""
    if open_browser:
        import webbrowser
        import time
        import threading

        def open_later():
            time.sleep(2)
            webbrowser.open(f"http://localhost:{port}")

        thread = threading.Thread(target=open_later, daemon=True)
        thread.start()

    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8501
    start_server(port=port)

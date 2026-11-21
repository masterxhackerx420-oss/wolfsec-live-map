"""FastAPI server: serves the map UI (docs/) and live GeoJSON layers.

    python -m livemap.server            # http://localhost:8765
    LIVEMAP_PORT=9000 python -m livemap.server
"""
from __future__ import annotations

import os
import threading
import webbrowser
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .registry import LAYERS, ROOT, Hub

hub = Hub()
app = FastAPI(title="LiveMap", version=__version__)


@app.get("/api/health")
def health():
    return {"ok": True, "mode": "server", "version": __version__}


@app.get("/api/layers")
def layers():
    out = []
    for k, (name, ttl) in LAYERS.items():
        m = (hub.data.get(k) or {}).get("metadata", {})
        out.append({"id": k, "name": name, "ttl": ttl, "count": m.get("count"), "updated": m.get("updated"),
                    "error": m.get("error"), "stale": m.get("stale")})
    return out


@app.get("/api/layer/{layer}")
def layer(layer: str, force: bool = Query(False)):
    if layer not in LAYERS:
        raise HTTPException(404, f"unknown layer {layer}")
    return JSONResponse(hub.get(layer, force=force), headers={"Cache-Control": "no-store"})


def _warm():
    with ThreadPoolExecutor(5) as ex:
        list(ex.map(hub.get, LAYERS))
    for k in LAYERS:
        m = hub.data[k]["metadata"]
        print(f"[livemap] {k:12s} {m['count']:5d} features  {'ERROR ' + m['error'] if m['error'] else 'ok'}", flush=True)


@app.on_event("startup")
def startup():
    threading.Thread(target=_warm, daemon=True).start()


app.mount("/", StaticFiles(directory=ROOT / "docs", html=True), name="ui")


def main():
    import uvicorn
    host = os.environ.get("LIVEMAP_HOST", "127.0.0.1")
    port = int(os.environ.get("LIVEMAP_PORT", "8765"))
    if os.environ.get("LIVEMAP_OPEN_BROWSER", "1") == "1" and host in ("127.0.0.1", "localhost"):
        threading.Timer(2.0, lambda: webbrowser.open(f"http://localhost:{port}")).start()
    print(f"[livemap] serving on http://localhost:{port}  (Ctrl+C to stop)", flush=True)
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()

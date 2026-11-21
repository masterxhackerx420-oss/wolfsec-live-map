"""Layer registry + per-layer TTL cache shared by the web server and the
GitHub Actions data updater."""
from __future__ import annotations

import os
import threading
import time
import traceback
from pathlib import Path

from . import feeds
from .geoip import GeoIP

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = Path(os.environ.get("LIVEMAP_CACHE_DIR", ROOT / "data_cache"))

# id -> (display name, TTL seconds)
LAYERS = {
    "earthquakes": ("Earthquakes", 60),
    "volcanoes": ("Volcanoes", 1800),
    "disasters": ("Disasters", 300),
    "weather": ("Weather (US)", 120),
    "eonet": ("Fires & storms", 600),
    "conflict": ("War / Conflict", 300),
    "news": ("World news", 300),
    "military": ("Military air", 30),
    "cyber": ("Cyber threats", 900),
    "markets": ("Markets", 120),
}


class Hub:
    def __init__(self, cache_dir: Path = CACHE_DIR):
        self.geo = GeoIP(cache_dir / "geoip.json")
        self.gdelt = feeds.Gdelt(cache_dir / "gdelt_state.json")
        self._gdelt_at = 0.0
        self._gdelt_lock = threading.Lock()
        self.data: dict[str, dict] = {}
        self.fetched_at: dict[str, float] = {}
        self.locks = {k: threading.Lock() for k in LAYERS}

    def _gdelt(self):
        with self._gdelt_lock:
            if time.time() - self._gdelt_at > 300 or not self.gdelt.files:
                self.gdelt.refresh()
                self._gdelt_at = time.time()
        return self.gdelt

    def _gdelt_layer(self, kind):
        g = self._gdelt()
        name = "War / conflict events" if kind == "conflict" else "World news events"
        return feeds.fc(kind, g.collect(kind), f"GDELT 2.0 Event Database — {name} (rolling 24h)",
                        note=g.span_note() + ". Machine-coded from news; locations/actors can be imprecise.")

    def build(self, layer: str) -> dict:
        if layer == "earthquakes":
            return feeds.fetch_earthquakes()
        if layer == "volcanoes":
            return feeds.fetch_volcanoes()
        if layer == "disasters":
            return feeds.fetch_disasters()
        if layer == "weather":
            return feeds.fetch_weather()
        if layer == "eonet":
            return feeds.fetch_eonet()
        if layer in ("conflict", "news"):
            return self._gdelt_layer(layer)
        if layer == "military":
            return feeds.fetch_military()
        if layer == "cyber":
            try:
                g = self._gdelt()
            except Exception:
                g = None
            return feeds.fetch_cyber(self.geo, g)
        if layer == "markets":
            return feeds.fetch_markets()
        raise KeyError(layer)

    def get(self, layer: str, force: bool = False) -> dict:
        name, ttl = LAYERS[layer]
        with self.locks[layer]:
            fresh = time.time() - self.fetched_at.get(layer, 0) < ttl
            if fresh and not force and layer in self.data:
                return self.data[layer]
            try:
                out = self.build(layer)
            except Exception as e:  # never fabricate: report the error, keep last real data marked stale
                traceback.print_exc()
                prev = self.data.get(layer)
                if prev:
                    out = dict(prev, metadata=dict(prev["metadata"], error=f"{type(e).__name__}: {e}", stale=True))
                else:
                    out = feeds.fc(layer, [], "", error=f"{type(e).__name__}: {e}")
            out["metadata"]["name"] = name
            out["metadata"]["ttl"] = ttl
            self.data[layer] = out
            self.fetched_at[layer] = time.time()
            return out

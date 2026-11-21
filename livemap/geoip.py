"""Free, keyless IP geolocation with a persistent on-disk cache.

Providers (in order):
  1. ip-api.com batch endpoint (100 IPs/request, ~15 requests/min, HTTP only).
  2. ipwho.is (HTTPS, one IP per request) as a fallback, capped per run.
Results (including misses) are cached so each IP is looked up at most once.
"""
import json
import threading
import time
from pathlib import Path

from .http import SESSION

_lock = threading.Lock()


class GeoIP:
    def __init__(self, cache_path: Path):
        self.path = Path(cache_path)
        self.cache: dict = {}
        if self.path.exists():
            try:
                self.cache = json.loads(self.path.read_text())
            except Exception:
                self.cache = {}

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with _lock:
            self.path.write_text(json.dumps(self.cache, separators=(",", ":")))

    def _ipapi_batch(self, ips):
        out = {}
        for i in range(0, len(ips), 100):
            chunk = ips[i:i + 100]
            r = SESSION.post("http://ip-api.com/batch?fields=status,query,lat,lon,country,city,org,as",
                             json=chunk, timeout=20)
            r.raise_for_status()
            for row in r.json():
                if row.get("status") == "success":
                    out[row["query"]] = [row["lat"], row["lon"], row.get("country"), row.get("city"),
                                         row.get("org") or row.get("as")]
                else:
                    out[row.get("query")] = None
            if int(r.headers.get("X-Rl", "10")) <= 1:
                time.sleep(int(r.headers.get("X-Ttl", "60")) + 1)
        return out

    def _ipwho(self, ips):
        out = {}
        for ip in ips:
            try:
                d = SESSION.get(f"https://ipwho.is/{ip}", timeout=15).json()
            except Exception:
                continue
            if d.get("success"):
                out[ip] = [d["latitude"], d["longitude"], d.get("country"), d.get("city"),
                           (d.get("connection") or {}).get("org") or (d.get("connection") or {}).get("isp")]
            elif "limit" in str(d.get("message", "")).lower():
                break
            else:
                out[ip] = None
        return out

    def lookup(self, ips, max_new=150):
        """Return {ip: [lat, lon, country, city, org]} for the IPs we can locate."""
        todo = [ip for ip in dict.fromkeys(ips) if ip not in self.cache][:max_new]
        if todo:
            got = {}
            try:
                got = self._ipapi_batch(todo)
            except Exception:
                got = {}
            missing = [ip for ip in todo if ip not in got]
            if missing:
                got.update(self._ipwho(missing))
            self.cache.update(got)
            self.save()
        return {ip: self.cache[ip] for ip in ips if self.cache.get(ip)}

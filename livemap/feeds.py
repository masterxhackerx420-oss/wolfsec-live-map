"""Layer fetchers: each pulls a free, keyless public feed and returns a
normalized GeoJSON FeatureCollection.

Every feature's properties follow one schema so the frontend can treat all
layers the same way:

    id, layer, kind, title, time (epoch ms or None), url, source,
    sev (0 minor .. 3 extreme, or None), mag (number or None), place,
    info (dict of extra label -> value), plus a few layer-specific keys
    (hdg for aircraft heading, chg/price/open for markets).

Fetchers never invent data. If a source fails they raise, and the cache layer
reports the error (keeping the last good copy marked stale, if any).
"""
from __future__ import annotations

import csv
import html
import io
import json
import re
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

from .http import SESSION, get, get_json

ROOT = Path(__file__).resolve().parent.parent
COUNTIES_PATH = ROOT / "docs" / "data" / "us_counties.json"
ZONES_PATH = ROOT / "docs" / "data" / "us_zones.json"

DAY_MS = 86_400_000


def now_ms() -> int:
    return int(time.time() * 1000)


def iso_ms(s: str | None) -> int | None:
    if not s:
        return None
    try:
        s = s.strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except ValueError:
        return None


def point(lon, lat, props) -> dict:
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(float(lon), 4), round(float(lat), 4)]},
            "properties": props}


def fc(layer: str, features: list, source: str, note: str = "", **extra) -> dict:
    meta = {"layer": layer, "updated": now_ms(), "count": len(features), "source": source,
            "note": note, "error": None, "stale": False}
    meta.update(extra)
    return {"type": "FeatureCollection", "metadata": meta, "features": features}


def strip_tags(s: str | None, limit: int = 400) -> str:
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", " ", html.unescape(s))
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= limit else s[: limit - 1] + "…"


def centroid(geom: dict):
    """Rough centroid (mean of vertices) for Point/LineString/Polygon/Multi*."""
    pts = []

    def walk(c):
        if isinstance(c, (list, tuple)) and c and isinstance(c[0], (int, float)):
            pts.append(c)
        elif isinstance(c, (list, tuple)):
            for x in c:
                walk(x)

    walk(geom.get("coordinates"))
    if not pts:
        return None
    return sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)


# ---------------------------------------------------------------- earthquakes
def fetch_earthquakes() -> dict:
    url = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_week.geojson"
    d = get_json(url)
    feats = []
    for f in d["features"]:
        p, c = f["properties"], f["geometry"]["coordinates"]
        mag = p.get("mag")
        sev = None if mag is None else (3 if mag >= 6 else 2 if mag >= 5 else 1 if mag >= 3 else 0)
        if p.get("alert") in ("orange", "red"):
            sev = 3
        feats.append(point(c[0], c[1], {
            "id": f["id"], "layer": "earthquakes", "kind": p.get("type", "earthquake"),
            "title": p.get("title") or f"M{mag} {p.get('place')}", "time": p.get("time"),
            "url": p.get("url"), "source": "USGS", "sev": sev, "mag": mag, "place": p.get("place"),
            "info": {"Depth": f"{c[2]:.1f} km" if len(c) > 2 and c[2] is not None else "",
                     "Tsunami": "possible" if p.get("tsunami") else "",
                     "PAGER": p.get("alert") or "", "Felt reports": p.get("felt") or ""},
        }))
    return fc("earthquakes", feats, "USGS Earthquake Hazards Program — all_week GeoJSON", url=url)


# ------------------------------------------------------------------ volcanoes
HANS_SEV = {"GREEN": 0, "YELLOW": 1, "ORANGE": 2, "RED": 3}


def fetch_volcanoes() -> dict:
    feats, errors = [], []
    # 1) Smithsonian GVP / USGS Weekly Volcanic Activity Report (global, weekly)
    try:
        xml = get("https://volcano.si.edu/news/WeeklyVolcanoRSS.xml").content
        root = ET.fromstring(xml)
        ns = {"georss": "http://www.georss.org/georss"}
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            pt = item.findtext("georss:point", namespaces=ns)
            if not pt:
                continue
            lat, lon = map(float, pt.split())
            status = title.rsplit(" - ", 1)[-1] if " - " in title else ""
            sev = 2 if "New Activity" in status else 1
            pub = item.findtext("pubDate")
            t = int(parsedate_to_datetime(pub).timestamp() * 1000) if pub else None
            feats.append(point(lon, lat, {
                "id": "gvp:" + (item.findtext("guid") or title), "layer": "volcanoes", "kind": "weekly report",
                "title": title.split(" - Report")[0] + (f" — {status}" if status else ""), "time": t,
                "url": item.findtext("guid") or item.findtext("link"), "source": "Smithsonian GVP / USGS weekly report",
                "sev": sev, "mag": None, "place": title.split(" - ")[0],
                "info": {"Report": strip_tags(item.findtext("description"), 500)},
            }))
    except Exception as e:  # keep the other source if one fails
        errors.append(f"GVP: {e}")
    # 2) USGS HANS: US volcanoes currently at elevated alert levels
    try:
        elevated = get_json("https://volcanoes.usgs.gov/hans-public/api/volcano/getElevatedVolcanoes")

        def detail(v):
            return v, get_json(f"https://volcanoes.usgs.gov/hans-public/api/volcano/getVolcano/{v['vnum']}")

        with ThreadPoolExecutor(6) as ex:
            for v, d in ex.map(detail, elevated):
                feats.append(point(d["longitude"], d["latitude"], {
                    "id": "hans:" + v["vnum"], "layer": "volcanoes", "kind": "elevated alert",
                    "title": f"{v['volcano_name']} — {v['color_code']} / {v['alert_level']}",
                    "time": int(v["sent_unixtime"]) * 1000, "url": v.get("notice_url"),
                    "source": f"USGS HANS ({v.get('obs_abbr', '').upper()})",
                    "sev": HANS_SEV.get(v.get("color_code", "").upper()), "mag": None,
                    "place": d.get("region") or v["volcano_name"],
                    "info": {"Aviation colour": v.get("color_code"), "Alert level": v.get("alert_level"),
                             "Observatory": v.get("obs_fullname")},
                }))
    except Exception as e:
        errors.append(f"HANS: {e}")
    if not feats and errors:
        raise RuntimeError("; ".join(errors))
    return fc("volcanoes", feats, "Smithsonian GVP weekly report + USGS HANS elevated volcanoes",
              note="; ".join(errors))


# ------------------------------------------------------------------ disasters
GDACS_KIND = {"EQ": "Earthquake", "TC": "Tropical cyclone", "FL": "Flood", "VO": "Volcano",
              "DR": "Drought", "WF": "Wildfire", "TS": "Tsunami"}
GDACS_SEV = {"green": 0, "orange": 2, "red": 3}
GDACS_BASE = "https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH"


def fetch_disasters() -> dict:
    urls = [GDACS_BASE + "?eventlist=EQ;TC;FL;VO;DR;WF&alertlevel=green;orange;red"] + \
           [GDACS_BASE + f"?eventlist={t}" for t in ("TC", "FL", "VO", "DR")]
    seen, feats = set(), []
    cutoff = now_ms() - 30 * DAY_MS
    ok = 0
    for u in urls:
        try:
            r = SESSION.get(u, timeout=30)
            if r.status_code == 204 or not r.content:
                ok += 1
                continue
            r.raise_for_status()
            data = r.json()
            ok += 1
        except Exception:
            continue
        for f in data.get("features", []):
            p = f["properties"]
            key = f"{p['eventtype']}{p['eventid']}"
            if key in seen:
                continue
            to_ms = iso_ms(p.get("todate"))
            if p.get("iscurrent") != "true" and (to_ms or 0) < cutoff:
                continue
            seen.add(key)
            c = f["geometry"]["coordinates"]
            sd = p.get("severitydata") or {}
            feats.append(point(c[0], c[1], {
                "id": "gdacs:" + key, "layer": "disasters", "kind": GDACS_KIND.get(p["eventtype"], p["eventtype"]),
                "title": p.get("name") or p.get("description"), "time": iso_ms(p.get("datemodified")) or to_ms,
                "url": (p.get("url") or {}).get("report"), "source": "GDACS",
                "sev": GDACS_SEV.get((p.get("alertlevel") or "").lower()), "mag": None, "place": p.get("country"),
                "info": {"Alert": p.get("alertlevel"), "Severity": sd.get("severitytext", ""),
                         "From": p.get("fromdate"), "To": p.get("todate")},
            }))
    if ok == 0:
        raise RuntimeError("GDACS API unreachable")
    return fc("disasters", feats, "GDACS — Global Disaster Alert and Coordination System")


# ------------------------------------------------------------------- weather
NWS_SEV = {"Minor": 0, "Moderate": 1, "Severe": 2, "Extreme": 3}
_refs: dict = {}


def geo_ref(path: Path) -> dict:
    if path not in _refs:
        _refs[path] = json.loads(path.read_text())
    return _refs[path]


def fetch_weather() -> dict:
    url = "https://api.weather.gov/alerts/active?status=actual&message_type=alert,update"
    d = get_json(url, headers={"Accept": "application/geo+json"})
    cty, zones = geo_ref(COUNTIES_PATH), geo_ref(ZONES_PATH)
    feats, unplaced = [], 0
    for f in d["features"]:
        p = f["properties"]
        loc = centroid(f["geometry"]) if f.get("geometry") else None
        if loc is None:
            gc = p.get("geocode") or {}
            pts = [cty[c] for c in gc.get("SAME", []) if c in cty] or \
                  [zones[c] for c in gc.get("UGC", []) if c in zones]
            if pts:
                loc = (sum(x[1] for x in pts) / len(pts), sum(x[0] for x in pts) / len(pts))
        if loc is None:
            unplaced += 1
            continue
        feats.append(point(loc[0], loc[1], {
            "id": p["id"], "layer": "weather", "kind": p.get("event"), "title": p.get("headline") or p.get("event"),
            "time": iso_ms(p.get("sent")), "url": p.get("@id") or p.get("id"), "source": "NOAA / NWS",
            "sev": NWS_SEV.get(p.get("severity")), "mag": None, "place": strip_tags(p.get("areaDesc"), 160),
            "info": {"Event": p.get("event"), "Severity": p.get("severity"), "Urgency": p.get("urgency"),
                     "Expires": p.get("expires") or "", "Details": strip_tags(p.get("description"), 400)},
        }))
    return fc("weather", feats, "NOAA National Weather Service — active US alerts",
              note=f"{unplaced} alerts without a known location not shown" if unplaced else "")


# ---------------------------------------------------------------------- EONET
EONET_SKIP = {"seaLakeIce"}  # icebergs: hundreds of slow-moving points, not "events"


def fetch_eonet() -> dict:
    url = "https://eonet.gsfc.nasa.gov/api/v3/events/geojson?status=open&days=30"
    d = get_json(url, timeout=45)
    latest = {}
    for f in d["features"]:
        p = f["properties"]
        cat = (p.get("categories") or [{}])[0]
        if cat.get("id") in EONET_SKIP:
            continue
        t = iso_ms(p.get("date"))
        if p["id"] in latest and (latest[p["id"]][0] or 0) >= (t or 0):
            continue
        latest[p["id"]] = (t, f, cat)
    feats = []
    for t, f, cat in latest.values():
        p, g = f["properties"], f["geometry"]
        loc = g["coordinates"] if g["type"] == "Point" else (g["coordinates"][-1] if g["type"] == "LineString" else centroid(g))
        if not loc:
            continue
        src = (p.get("sources") or [{}])[0]
        mv = p.get("magnitudeValue")
        feats.append(point(loc[0], loc[1], {
            "id": p["id"], "layer": "eonet", "kind": cat.get("title"), "title": p.get("title"), "time": t,
            "url": src.get("url") or p.get("link"), "source": f"NASA EONET ({src.get('id', '')})",
            "sev": None, "mag": None, "place": "",
            "info": {"Category": cat.get("title"), "Magnitude": f"{mv:g} {p.get('magnitudeUnit') or ''}" if mv else ""},
        }))
    return fc("eonet", feats, "NASA EONET — open natural events (last 30 days)", url=url)


# ---------------------------------------------------------------------- GDELT
# GDELT 2.0 Event exports: every 15 minutes, geolocated, with source article URL.
CAMEO_ROOT = {
    "01": "Public statement", "02": "Appeal", "03": "Intent to cooperate", "04": "Consult",
    "05": "Diplomatic cooperation", "06": "Material cooperation", "07": "Aid", "08": "Yield",
    "09": "Investigate", "10": "Demand", "11": "Disapprove", "12": "Reject", "13": "Threaten",
    "14": "Protest", "15": "Military posture", "16": "Reduce relations", "17": "Coerce",
    "18": "Assault", "19": "Fight", "20": "Mass violence",
}
CONFLICT_ROOTS = {"15": 0, "18": 1, "19": 2, "20": 3}
CYBER_RE = re.compile(r"cyber|hack|ransomware|malware|ddos|data-?breach|phishing|botnet|spyware|zero-?day|infosec",
                      re.I)
GDELT_RETAIN_MS = DAY_MS
GDELT_CAPS = {"conflict": 1500, "news": 1500, "cybernews": 300}


def _slug_title(url: str) -> str:
    try:
        path = urlparse(url).path.rstrip("/")
    except ValueError:
        return ""
    segs = [s for s in path.split("/") if s]
    for seg in reversed(segs):
        seg = re.sub(r"\.(s?html?|php|aspx?|cms)$", "", seg, flags=re.I)
        words = [w for w in re.split(r"[-_+.]+", seg)
                 if w and re.search(r"[a-z]", w, re.I) and not (re.search(r"\d", w) and re.fullmatch(r"[0-9a-f]+", w, re.I))]
        if sum(len(w) >= 3 and w.isalpha() for w in words) >= 3:
            t = " ".join(words)
            return (t[0].upper() + t[1:])[:140]
    return ""


class Gdelt:
    """Keeps a rolling 24h window of parsed 15-minute GDELT event files."""

    def __init__(self, state_path: Path | None = None):
        self.files: dict[str, dict] = {}  # file timestamp -> {"conflict": [...], "news": [...], "cybernews": [...]}
        self.state_path = state_path
        if state_path and state_path.exists():
            try:
                self.files = json.loads(state_path.read_text())
            except Exception:
                self.files = {}

    @staticmethod
    def _parse(ts: str) -> dict:
        url = f"https://data.gdeltproject.org/gdeltv2/{ts}.export.CSV.zip"
        r = SESSION.get(url, timeout=40)
        if r.status_code == 404:
            return {"conflict": [], "news": [], "cybernews": []}
        r.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            text = z.read(z.namelist()[0]).decode("utf-8", "replace")
        out = {"conflict": [], "news": [], "cybernews": []}
        best: dict[str, list] = {}  # one row per article; prefer its most violent coded event
        for row in csv.reader(io.StringIO(text), delimiter="\t"):
            if len(row) < 61 or not row[56] or not row[57] or not row[60]:
                continue
            old = best.get(row[60])
            if old is None or (row[28] in CONFLICT_ROOTS and row[28] > (old[28] if old[28] in CONFLICT_ROOTS else "")):
                best[row[60]] = row
        for src, row in best.items():
            root = row[28]
            added = datetime.strptime(row[59], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
            a1, a2 = row[6].title(), row[16].title()
            action = CAMEO_ROOT.get(root, "Event")
            title = _slug_title(src) or " — ".join(x for x in (a1, action, a2) if x)
            props = {
                "id": "gdelt:" + row[0], "kind": action, "title": title,
                "time": int(added.timestamp() * 1000), "url": src,
                "source": urlparse(src).netloc.replace("www.", ""),
                "sev": CONFLICT_ROOTS.get(root), "mag": None, "place": row[52],
                "info": {"Actors": " → ".join(x for x in (a1, a2) if x), "CAMEO action": f"{row[26]} ({action})",
                         "Articles": row[33], "Tone": f"{float(row[34]):.1f}" if row[34] else "",
                         "Geo precision": {"1": "country", "2": "state/region", "3": "US city", "4": "city",
                                           "5": "state/region"}.get(row[51], row[51])},
                "_n": int(row[33] or 0),
            }
            lon, lat = float(row[57]), float(row[56])
            if CYBER_RE.search(src):
                out["cybernews"].append(point(lon, lat, dict(props, layer="cyber", kind="Cyber news",
                                                              sev=None)))
            if root in CONFLICT_ROOTS:
                out["conflict"].append(point(lon, lat, dict(props, layer="conflict")))
            else:
                out["news"].append(point(lon, lat, dict(props, layer="news", sev=None)))
        # keep the most-covered stories per file so 96 files/day stay manageable
        out["news"].sort(key=lambda f: -f["properties"]["_n"])
        out["news"] = out["news"][:120]
        return out

    def refresh(self, bootstrap_files: int = 16) -> None:
        last = get("https://data.gdeltproject.org/gdeltv2/lastupdate.txt", timeout=20).text.split()[2]
        latest = datetime.strptime(re.search(r"(\d{14})", last).group(1), "%Y%m%d%H%M%S")
        wanted = [(latest - timedelta(minutes=15 * i)).strftime("%Y%m%d%H%M%S") for i in range(96)]
        n_new = bootstrap_files if not self.files else 96
        todo = [ts for ts in wanted[:n_new] if ts not in self.files][:bootstrap_files]
        with ThreadPoolExecutor(6) as ex:
            for ts, parsed in zip(todo, ex.map(self._safe_parse, todo)):
                if parsed is not None:
                    self.files[ts] = parsed
        keep = set(wanted)
        self.files = {k: v for k, v in self.files.items() if k in keep}
        if self.state_path:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(json.dumps(self.files, separators=(",", ":")))
        if not self.files:
            raise RuntimeError("no GDELT files could be downloaded")

    def _safe_parse(self, ts):
        try:
            return self._parse(ts)
        except Exception:
            return None

    def collect(self, kind: str) -> list:
        seen, out = set(), []
        for ts in sorted(self.files, reverse=True):
            for f in self.files[ts][kind]:
                u = f["properties"]["url"]
                if u in seen:
                    continue
                seen.add(u)
                p = {k: v for k, v in f["properties"].items() if k != "_n"}
                out.append({"type": "Feature", "geometry": f["geometry"], "properties": p})
        return out[: GDELT_CAPS[kind]]

    def span_note(self) -> str:
        if not self.files:
            return ""
        ks = sorted(self.files)
        a = datetime.strptime(ks[0], "%Y%m%d%H%M%S").strftime("%H:%M")
        b = datetime.strptime(ks[-1], "%Y%m%d%H%M%S").strftime("%H:%M")
        return f"{len(ks)} GDELT 15-min batches ({a}–{b} UTC)"


# -------------------------------------------------------------------- military
def fetch_military() -> dict:
    errors = []
    for name, url in (("adsb.lol", "https://api.adsb.lol/v2/mil"),
                      ("adsb.fi", "https://opendata.adsb.fi/api/v2/mil")):
        try:
            d = get_json(url, timeout=25)
        except Exception as e:
            errors.append(f"{name}: {e}")
            continue
        now = d.get("now") or now_ms()
        feats = []
        for a in d.get("ac", []):
            if a.get("lat") is None or a.get("lon") is None:
                continue
            cs = (a.get("flight") or "").strip()
            alt = a.get("alt_baro")
            seen = a.get("seen_pos", a.get("seen", 0)) or 0
            feats.append(point(a["lon"], a["lat"], {
                "id": "mil:" + a["hex"], "layer": "military", "kind": a.get("t") or "aircraft",
                "title": f"{cs or a.get('r') or a['hex'].upper()} · {a.get('t') or 'unknown type'}",
                "time": int(now - seen * 1000), "url": f"https://globe.adsb.lol/?icao={a['hex']}",
                "source": name, "sev": None, "mag": None, "place": a.get("desc") or "",
                "hdg": a.get("track", a.get("true_heading")),
                "info": {"Callsign": cs, "Registration": a.get("r") or "", "Type": a.get("t") or "",
                         "Altitude": "on ground" if alt == "ground" else (f"{alt:,} ft" if isinstance(alt, (int, float)) else ""),
                         "Speed": f"{a['gs']:.0f} kt" if a.get("gs") is not None else "",
                         "Squawk": a.get("squawk") or "", "ICAO hex": a["hex"].upper()},
            }))
        return fc("military", feats, f"{name} — ADS-B military aircraft (unfiltered community feed)",
                  note="Only aircraft broadcasting ADS-B/MLAT are visible; many military flights are not.")
    raise RuntimeError("; ".join(errors))


# ----------------------------------------------------------------------- cyber
def fetch_cyber(geo, gdelt: Gdelt | None = None) -> dict:
    """Real malicious-infrastructure feeds, geolocated by IP."""
    entries, errors = [], []
    try:  # SANS ISC / DShield: today's top attacking source IPs
        for r in get_json("https://isc.sans.edu/api/sources/attacks/100?json", timeout=25):
            if not isinstance(r, dict) or not r.get("ip"):
                continue
            ip = ".".join(str(int(x)) for x in r["ip"].split("."))
            n = int(r.get("count") or 0)
            entries.append((ip, {
                "kind": "Attacking source (DShield)", "title": f"Scanner/attacker {ip}",
                "time": iso_ms(r.get("lastseen")), "url": f"https://isc.sans.edu/ipinfo/{ip}",
                "source": "SANS ISC DShield", "sev": 3 if n > 50000 else 2 if n > 10000 else 1,
                "info": {"Reports": f"{n:,}", "Targets": f"{int(r.get('attacks') or 0):,}",
                         "First seen": r.get("firstseen"), "Last seen": r.get("lastseen")}}))
    except Exception as e:
        errors.append(f"SANS ISC: {e}")
    try:  # abuse.ch Feodo Tracker: botnet C2 servers
        for r in get_json("https://feodotracker.abuse.ch/downloads/ipblocklist.json", timeout=25):
            ip = r["ip_address"]
            entries.append((ip, {
                "kind": "Botnet C2 (Feodo Tracker)", "title": f"{r.get('malware') or 'Botnet'} C2 {ip}:{r.get('port')}",
                "time": iso_ms((r.get("last_online") or r.get("first_seen") or "").replace(" ", "T")),
                "url": f"https://feodotracker.abuse.ch/browse/host/{ip}/", "source": "abuse.ch Feodo Tracker",
                "sev": 3 if r.get("status") == "online" else 1,
                "info": {"Malware": r.get("malware"), "Status": r.get("status"), "AS": r.get("as_name"),
                         "First seen": r.get("first_seen")}}))
    except Exception as e:
        errors.append(f"Feodo: {e}")
    try:  # Emerging Threats: known compromised hosts (sample)
        txt = get("https://rules.emergingthreats.net/blockrules/compromised-ips.txt", timeout=25).text
        ips = [l.strip() for l in txt.splitlines() if re.fullmatch(r"\d+\.\d+\.\d+\.\d+", l.strip())][:120]
        for ip in ips:
            entries.append((ip, {
                "kind": "Compromised host (ET)", "title": f"Compromised host {ip}", "time": None,
                "url": f"https://www.abuseipdb.com/check/{ip}", "source": "Proofpoint Emerging Threats",
                "sev": 1, "info": {}}))
    except Exception as e:
        errors.append(f"ET: {e}")

    located = geo.lookup([ip for ip, _ in entries]) if entries else {}
    feats = []
    for ip, p in entries:
        g = located.get(ip)
        if not g:
            continue
        lat, lon, country, city, org = g
        p["info"].update({"IP": ip, "Location": ", ".join(x for x in (city, country) if x), "Network": org or ""})
        feats.append(point(lon, lat, dict(p, id=f"cyber:{p['kind'][:4]}:{ip}", layer="cyber", mag=None,
                                          place=", ".join(x for x in (city, country) if x))))
    if gdelt is not None and gdelt.files:
        feats += gdelt.collect("cybernews")
    if not feats and errors:
        raise RuntimeError("; ".join(errors))
    return fc("cyber", feats, "SANS ISC DShield + abuse.ch Feodo Tracker + ET compromised IPs (geolocated) + GDELT cyber news",
              note="Threat infrastructure & reported attackers, not live attack traffic. IP geolocation is approximate. "
                   + "; ".join(errors))


# --------------------------------------------------------------------- markets
MARKETS = [  # (symbol, name, exchange, city, lat, lon)
    ("^GSPC", "S&P 500", "NYSE", "New York", 40.7069, -74.0113),
    ("^IXIC", "Nasdaq Composite", "Nasdaq", "New York", 40.7570, -73.9860),
    ("^DJI", "Dow Jones", "NYSE", "New York", 40.6900, -74.0450),
    ("^GSPTSE", "S&P/TSX", "TSX", "Toronto", 43.6487, -79.3817),
    ("^MXX", "IPC Mexico", "BMV", "Mexico City", 19.4326, -99.1332),
    ("^BVSP", "Ibovespa", "B3", "São Paulo", -23.5475, -46.6361),
    ("^FTSE", "FTSE 100", "LSE", "London", 51.5155, -0.0992),
    ("^GDAXI", "DAX", "Xetra", "Frankfurt", 50.1155, 8.6842),
    ("^FCHI", "CAC 40", "Euronext", "Paris", 48.8686, 2.3417),
    ("^SSMI", "SMI", "SIX", "Zurich", 47.3769, 8.5417),
    ("^STOXX50E", "Euro Stoxx 50", "Euronext", "Amsterdam", 52.3702, 4.8952),
    ("^N225", "Nikkei 225", "TSE", "Tokyo", 35.6828, 139.7745),
    ("^KS11", "KOSPI", "KRX", "Seoul", 37.5665, 126.9780),
    ("000001.SS", "SSE Composite", "SSE", "Shanghai", 31.2304, 121.4737),
    ("^HSI", "Hang Seng", "HKEX", "Hong Kong", 22.2840, 114.1580),
    ("^TWII", "TAIEX", "TWSE", "Taipei", 25.0330, 121.5654),
    ("^BSESN", "BSE Sensex", "BSE", "Mumbai", 18.9298, 72.8337),
    ("^AXJO", "S&P/ASX 200", "ASX", "Sydney", -33.8688, 151.2093),
    ("^JN0U.JO", "JSE Top 40", "JSE", "Johannesburg", -26.1076, 28.0567),
    ("BTC-USD", "Bitcoin", "Crypto (24/7)", "Global", 37.7749, -122.4194),
]


def _quote(sym):
    from urllib.parse import quote
    for host in ("query1", "query2"):
        try:
            d = get_json(f"https://{host}.finance.yahoo.com/v8/finance/chart/{quote(sym)}?range=1d&interval=15m",
                         timeout=20, headers={"User-Agent": "Mozilla/5.0"})
            return d["chart"]["result"][0]["meta"]
        except Exception:
            continue
    return None


def fetch_markets() -> dict:
    with ThreadPoolExecutor(8) as ex:
        metas = list(ex.map(lambda m: _quote(m[0]), MARKETS))
    feats, now_s = [], time.time()
    for (sym, name, exch, city, lat, lon), m in zip(MARKETS, metas):
        if not m or m.get("regularMarketPrice") is None:
            continue
        price = m["regularMarketPrice"]
        prev = m.get("previousClose") or m.get("chartPreviousClose")
        chg = (price / prev - 1) * 100 if prev else None
        reg = (m.get("currentTradingPeriod") or {}).get("regular") or {}
        is_open = bool(reg) and reg.get("start", 0) <= now_s < reg.get("end", 0)
        if sym.endswith("-USD"):
            is_open = True
        feats.append(point(lon, lat, {
            "id": "mkt:" + sym, "layer": "markets", "kind": exch, "title": f"{name} ({city})",
            "time": int(m.get("regularMarketTime", now_s)) * 1000,
            "url": f"https://finance.yahoo.com/quote/{sym}", "source": "Yahoo Finance (delayed)",
            "sev": None, "mag": None, "place": f"{exch}, {city}", "price": price, "chg": chg,
            "open": is_open, "sym": sym,
            "info": {"Last": f"{price:,.2f} {m.get('currency', '')}", "Change": f"{chg:+.2f}%" if chg is not None else "",
                     "Prev close": f"{prev:,.2f}" if prev else "", "Market": "OPEN" if is_open else "closed",
                     "Day range": f"{m.get('regularMarketDayLow', 0):,.2f} – {m.get('regularMarketDayHigh', 0):,.2f}"},
        }))
    if not feats:
        raise RuntimeError("Yahoo Finance returned no quotes")
    return fc("markets", feats, "Yahoo Finance chart API (delayed quotes)",
              note=f"{len(feats)}/{len(MARKETS)} indices")

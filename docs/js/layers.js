/* Layer catalogue + browser-side fetchers for CORS-friendly feeds.
 * The browser normalizers mirror livemap/feeds.py so both paths produce the
 * same feature schema. */
(function () {
  const DAY = 86400000;
  const SVG = {
    earthquakes: '<circle cx="12" cy="12" r="5" fill="C"/><circle cx="12" cy="12" r="9" fill="none" stroke="C" stroke-width="2"/>',
    volcanoes: '<path d="M2 21 L12 4 L22 21 Z" fill="C"/><path d="M9 4 Q12 0 15 4" stroke="#fff" fill="none" stroke-width="1.5"/>',
    disasters: '<path d="M12 2 L22 12 L12 22 L2 12 Z" fill="C"/><rect x="11" y="7" width="2" height="7" fill="#000"/><rect x="11" y="15.5" width="2" height="2" fill="#000"/>',
    weather: '<path d="M6 15 a5 5 0 0 1 1-9.8 a6 6 0 0 1 11 1.8 a4 4 0 0 1 0 8 Z" fill="C"/><path d="M12 14 L10 19 L13 19 L11 23" stroke="C" stroke-width="2" fill="none"/>',
    eonet: '<path d="M12 2 C15 7 19 9 19 15 a7 7 0 0 1 -14 0 C5 11 8 9 9 6 C10 9 11 10 12 10 C12 7 12 4 12 2 Z" fill="C"/>',
    conflict: '<circle cx="12" cy="12" r="8" fill="none" stroke="C" stroke-width="2.5"/><path d="M12 1 V8 M12 16 V23 M1 12 H8 M16 12 H23" stroke="C" stroke-width="2.5"/><circle cx="12" cy="12" r="2.5" fill="C"/>',
    news: '<rect x="3" y="4" width="18" height="16" rx="2" fill="C"/><rect x="6" y="7" width="12" height="2" fill="#000"/><rect x="6" y="11" width="12" height="1.5" fill="#000"/><rect x="6" y="14" width="8" height="1.5" fill="#000"/>',
    military: '<path d="M12 1 L13.5 8 L22 13 L22 15 L13.5 12 L13 19 L16 21 L16 22.5 L12 21.5 L8 22.5 L8 21 L11 19 L10.5 12 L2 15 L2 13 L10.5 8 Z" fill="C" stroke="#000" stroke-width=".6"/>',
    cyber: '<path d="M7 2 H17 L22 7 V17 L17 22 H7 L2 17 V7 Z" fill="C"/><path d="M9 8 L5 12 L9 16 M15 8 L19 12 L15 16" stroke="#000" stroke-width="2" fill="none"/>',
    markets: '',
  };
  const LAYERS = [
    { id: "earthquakes", name: "Earthquakes", color: "#ffb300", sevLabel: "magnitude", browserLive: true },
    { id: "volcanoes", name: "Volcanoes", color: "#ff5722" },
    { id: "disasters", name: "Disasters", color: "#ff4081", browserLive: true },
    { id: "weather", name: "Weather (US)", color: "#40c4ff", browserLive: true },
    { id: "eonet", name: "Fires & storms", color: "#ff9100", browserLive: true },
    { id: "conflict", name: "War / Conflict", color: "#ff1744" },
    { id: "news", name: "World news", color: "#b0bec5", defaultOff: false },
    { id: "military", name: "Military air", color: "#76ff03", cluster: false },
    { id: "cyber", name: "Cyber threats", color: "#d500f9" },
    { id: "markets", name: "Markets", color: "#00e676", cluster: false },
  ];
  LAYERS.forEach((l) => (l.svg = SVG[l.id]));

  // ---------- helpers mirroring feeds.py
  const pt = (lon, lat, props) => ({ type: "Feature", geometry: { type: "Point", coordinates: [+lon, +lat] }, properties: props });
  const fc = (layer, features, source, note = "") => ({
    type: "FeatureCollection", features,
    metadata: { layer, updated: Date.now(), count: features.length, source, note, error: null, stale: false, via: "browser" },
  });
  const isoMs = (s) => { if (!s) return null; const t = Date.parse(/Z|[+-]\d\d:?\d\d$/.test(s) ? s : s + "Z"); return isNaN(t) ? null : t; };
  const strip = (s, n = 400) => { if (!s) return ""; const d = document.createElement("div"); d.innerHTML = s; s = (d.textContent || "").replace(/\s+/g, " ").trim(); return s.length > n ? s.slice(0, n - 1) + "…" : s; };
  function centroid(g) {
    const pts = []; (function walk(c) { if (Array.isArray(c) && typeof c[0] === "number") pts.push(c); else if (Array.isArray(c)) c.forEach(walk); })(g && g.coordinates);
    if (!pts.length) return null;
    return [pts.reduce((a, p) => a + p[0], 0) / pts.length, pts.reduce((a, p) => a + p[1], 0) / pts.length];
  }
  async function getJSON(url, opts) { const r = await fetch(url, opts); if (!r.ok) throw new Error(`${r.status} ${url}`); if (r.status === 204) return null; return r.json(); }

  const LIVE = {};
  LIVE.earthquakes = async (ctx) => {
    const h = ctx.windowHours;
    const feed = h && h <= 1 ? "all_hour" : h && h <= 24 ? "all_day" : "all_week";
    const url = `https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/${feed}.geojson`;
    const d = await getJSON(url);
    const feats = d.features.map((f) => {
      const p = f.properties, c = f.geometry.coordinates, mag = p.mag;
      let sev = mag == null ? null : mag >= 6 ? 3 : mag >= 5 ? 2 : mag >= 3 ? 1 : 0;
      if (p.alert === "orange" || p.alert === "red") sev = 3;
      return pt(c[0], c[1], { id: f.id, layer: "earthquakes", kind: p.type, title: p.title, time: p.time, url: p.url, source: "USGS",
        sev, mag, place: p.place, info: { Depth: c[2] != null ? `${c[2].toFixed(1)} km` : "", Tsunami: p.tsunami ? "possible" : "", PAGER: p.alert || "", "Felt reports": p.felt || "" } });
    });
    return fc("earthquakes", feats, `USGS Earthquake Hazards Program — ${feed} GeoJSON`);
  };

  const GDACS_KIND = { EQ: "Earthquake", TC: "Tropical cyclone", FL: "Flood", VO: "Volcano", DR: "Drought", WF: "Wildfire", TS: "Tsunami" };
  const GDACS_SEV = { green: 0, orange: 2, red: 3 };
  LIVE.disasters = async () => {
    const base = "https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH";
    const urls = [base + "?eventlist=EQ;TC;FL;VO;DR;WF&alertlevel=green;orange;red", ...["TC", "FL", "VO", "DR"].map((t) => `${base}?eventlist=${t}`)];
    const res = await Promise.allSettled(urls.map((u) => getJSON(u)));
    if (!res.some((r) => r.status === "fulfilled")) throw new Error("GDACS unreachable from browser");
    const seen = new Set(), feats = [], cutoff = Date.now() - 30 * DAY;
    res.forEach((r) => {
      if (r.status !== "fulfilled" || !r.value) return;
      r.value.features.forEach((f) => {
        const p = f.properties, key = `${p.eventtype}${p.eventid}`;
        if (seen.has(key)) return;
        const to = isoMs(p.todate);
        if (p.iscurrent !== "true" && (to || 0) < cutoff) return;
        seen.add(key);
        const c = f.geometry.coordinates, sd = p.severitydata || {};
        feats.push(pt(c[0], c[1], { id: "gdacs:" + key, layer: "disasters", kind: GDACS_KIND[p.eventtype] || p.eventtype,
          title: p.name || p.description, time: isoMs(p.datemodified) || to, url: (p.url || {}).report, source: "GDACS",
          sev: GDACS_SEV[(p.alertlevel || "").toLowerCase()] ?? null, mag: null, place: p.country,
          info: { Alert: p.alertlevel, Severity: sd.severitytext || "", From: p.fromdate, To: p.todate } }));
      });
    });
    return fc("disasters", feats, "GDACS — Global Disaster Alert and Coordination System");
  };

  let refs = null;
  const NWS_SEV = { Minor: 0, Moderate: 1, Severe: 2, Extreme: 3 };
  LIVE.weather = async () => {
    if (!refs) {
      const [c, z] = await Promise.all([getJSON("data/us_counties.json"), getJSON("data/us_zones.json")]);
      refs = { c, z };
    }
    const d = await getJSON("https://api.weather.gov/alerts/active?status=actual&message_type=alert,update", { headers: { Accept: "application/geo+json" } });
    const feats = []; let unplaced = 0;
    d.features.forEach((f) => {
      const p = f.properties; let loc = f.geometry ? centroid(f.geometry) : null;
      if (!loc) {
        const gc = p.geocode || {};
        let pts = (gc.SAME || []).map((s) => refs.c[s]).filter(Boolean);
        if (!pts.length) pts = (gc.UGC || []).map((s) => refs.z[s]).filter(Boolean);
        if (pts.length) loc = [pts.reduce((a, x) => a + x[1], 0) / pts.length, pts.reduce((a, x) => a + x[0], 0) / pts.length];
      }
      if (!loc) { unplaced++; return; }
      feats.push(pt(loc[0], loc[1], { id: p.id, layer: "weather", kind: p.event, title: p.headline || p.event, time: isoMs(p.sent),
        url: p["@id"] || p.id, source: "NOAA / NWS", sev: NWS_SEV[p.severity] ?? null, mag: null, place: strip(p.areaDesc, 160),
        info: { Event: p.event, Severity: p.severity, Urgency: p.urgency, Expires: p.expires || "", Details: strip(p.description, 400) } }));
    });
    return fc("weather", feats, "NOAA National Weather Service — active US alerts", unplaced ? `${unplaced} alerts without a known location not shown` : "");
  };

  LIVE.eonet = async () => {
    const d = await getJSON("https://eonet.gsfc.nasa.gov/api/v3/events/geojson?status=open&days=30");
    const latest = new Map();
    d.features.forEach((f) => {
      const p = f.properties, cat = (p.categories || [{}])[0];
      if (cat.id === "seaLakeIce") return;
      const t = isoMs(p.date), prev = latest.get(p.id);
      if (prev && (prev.t || 0) >= (t || 0)) return;
      latest.set(p.id, { t, f, cat });
    });
    const feats = [];
    latest.forEach(({ t, f, cat }) => {
      const p = f.properties, g = f.geometry;
      const loc = g.type === "Point" ? g.coordinates : g.type === "LineString" ? g.coordinates[g.coordinates.length - 1] : centroid(g);
      if (!loc) return;
      const src = (p.sources || [{}])[0];
      feats.push(pt(loc[0], loc[1], { id: p.id, layer: "eonet", kind: cat.title, title: p.title, time: t, url: src.url || p.link,
        source: `NASA EONET (${src.id || ""})`, sev: null, mag: null, place: "",
        info: { Category: cat.title, Magnitude: p.magnitudeValue ? `${p.magnitudeValue} ${p.magnitudeUnit || ""}` : "" } }));
    });
    return fc("eonet", feats, "NASA EONET — open natural events (last 30 days)");
  };

  window.LIVEMAP_LAYERS = LAYERS;
  window.LIVEMAP_BROWSER_LIVE = LIVE;
})();

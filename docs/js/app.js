/* WolfSec LiveMap frontend. Works in two modes:
 *   server : python -m livemap.server  -> /api/layer/<id>
 *   static : GitHub Pages              -> browser-live fetchers + data/<id>.json */
(function () {
  const CFG = window.LIVEMAP_CONFIG || {};
  const LAYERS = window.LIVEMAP_LAYERS, LIVE = window.LIVEMAP_BROWSER_LIVE;
  const BY_ID = Object.fromEntries(LAYERS.map((l) => [l.id, l]));
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const safeUrl = (u) => (/^https?:\/\//i.test(u || "") ? u : null);
  const SEV_NAME = ["minor", "moderate", "severe", "extreme"];

  // ---------------- state (persisted)
  const saved = JSON.parse(localStorage.getItem("livemap") || "{}");
  const state = Object.assign({
    on: Object.fromEntries(LAYERS.map((l) => [l.id, true])),
    window: 24, sev: -1, mag: 2.5, q: "", cluster: true, refresh: CFG.defaultRefreshSec || 60,
  }, saved);
  const params = new URLSearchParams(location.search); // ?layers=earthquakes,conflict&window=6&sev=2&mag=4
  if (params.get("layers")) { const on = params.get("layers").split(","); LAYERS.forEach((l) => (state.on[l.id] = on.includes(l.id))); }
  ["window", "sev", "mag", "refresh"].forEach((k) => params.has(k) && (state[k] = +params.get(k)));
  if (params.has("q")) state.q = params.get("q");
  if (params.has("cluster")) state.cluster = params.get("cluster") !== "0";
  const save = () => localStorage.setItem("livemap", JSON.stringify(state));

  const data = {};      // id -> FeatureCollection
  const status = {};    // id -> {loading, error, via}
  let mode = "static";

  // ---------------- map
  const map = L.map("map", { worldCopyJump: true, zoomControl: false, minZoom: 2, maxZoom: 11, preferCanvas: false }).setView([28, 15], 2);
  L.control.zoom({ position: "bottomright" }).addTo(map);
  // Esri Dark Gray Canvas (keyless; CARTO basemaps now require an API key)
  const ESRI = "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/";
  L.tileLayer(ESRI + "World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}", {
    maxZoom: 16, attribution: 'Tiles &copy; <a href="https://www.esri.com">Esri</a> — Esri, HERE, Garmin, &copy; OpenStreetMap contributors',
  }).addTo(map);
  L.tileLayer(ESRI + "World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}", { maxZoom: 16, pane: "shadowPane", opacity: 0.8 }).addTo(map);

  const groups = {};
  function makeGroup(l) {
    const clustered = state.cluster && l.cluster !== false;
    const g = clustered
      ? L.markerClusterGroup({
          maxClusterRadius: 45, showCoverageOnHover: false, chunkedLoading: true, spiderfyOnMaxZoom: true,
          iconCreateFunction: (c) => {
            const n = c.getChildCount();
            const s = n < 10 ? 30 : n < 100 ? 36 : 44;
            return L.divIcon({ html: `<div style="--c:${l.color}"><span>${n}</span></div>`, className: "lm-cluster", iconSize: [s, s] });
          },
        })
      : L.layerGroup();
    g._lm = l.id;
    return g;
  }
  LAYERS.forEach((l) => (groups[l.id] = makeGroup(l)));

  const MKT_ANCHOR = { "^IXIC": [0, 22], "^DJI": [0, -22], "^STOXX50E": [0, 22], "^FCHI": [60, 0], "^SSMI": [0, -22], "^HSI": [0, -22], "^TWII": [0, 22] };
  function iconFor(f) {
    const p = f.properties, l = BY_ID[p.layer] || BY_ID.news;
    if (p.layer === "markets") {
      const up = (p.chg ?? 0) >= 0, chg = p.chg == null ? "n/a" : `${up ? "▲" : "▼"} ${Math.abs(p.chg).toFixed(2)}%`;
      const short = p.title.split(" (")[0];
      return L.divIcon({ className: "lm-mkt " + (up ? "up" : "down") + (p.open ? " open" : ""),
        html: `<b>${esc(short)}</b><span>${chg}</span>`, iconSize: null, iconAnchor: MKT_ANCHOR[p.sym] || [0, 0] });
    }
    let size = 20;
    if (p.layer === "earthquakes" && p.mag != null) size = Math.max(12, Math.min(40, 8 + p.mag * 4.5));
    if (p.layer === "news") size = 14;
    if (p.layer === "military") size = 22;
    const rot = p.layer === "military" && p.hdg != null ? `transform:rotate(${p.hdg}deg)` : "";
    const sevCls = p.sev === 3 ? " pulse" : p.sev === 2 ? " hot" : "";
    return L.divIcon({
      className: "lm-icon" + sevCls, iconSize: [size, size], iconAnchor: [size / 2, size / 2],
      html: `<svg viewBox="0 0 24 24" width="${size}" height="${size}" style="${rot};--c:${l.color}">${l.svg.replaceAll(`="C"`, `="${l.color}"`).replaceAll("#000", "#05080d")}</svg>`,
    });
  }

  function popupHtml(p) {
    const l = BY_ID[p.layer] || {};
    const t = p.time ? new Date(p.time) : null;
    const rows = Object.entries(p.info || {}).filter(([, v]) => v !== "" && v != null)
      .map(([k, v]) => `<tr><th>${esc(k)}</th><td>${esc(v)}</td></tr>`).join("");
    const url = safeUrl(p.url);
    return `<div class="pp" style="--c:${l.color}">
      <div class="pp-tag">${esc(l.name)}${p.kind ? " · " + esc(p.kind) : ""}${p.sev != null ? ` · <span class="sev s${p.sev}">${SEV_NAME[p.sev]}</span>` : ""}</div>
      <div class="pp-title">${esc(p.title)}</div>
      ${p.place ? `<div class="pp-place">${esc(p.place)}</div>` : ""}
      ${t ? `<div class="pp-time">${esc(t.toLocaleString())} <span class="dim">(${ago(p.time)})</span></div>` : ""}
      ${rows ? `<table>${rows}</table>` : ""}
      <div class="pp-src">${esc(p.source || "")}${url ? ` · <a href="${esc(url)}" target="_blank" rel="noopener noreferrer">open source ↗</a>` : ""}</div></div>`;
  }

  function ago(ms) {
    const s = Math.round((Date.now() - ms) / 1000);
    if (s < 0) return "just now";
    if (s < 60) return s + "s ago";
    if (s < 3600) return Math.round(s / 60) + "m ago";
    if (s < 86400) return Math.round(s / 3600) + "h ago";
    return Math.round(s / 86400) + "d ago";
  }

  // ---------------- filtering
  function passes(f) {
    const p = f.properties;
    if (state.window > 0 && p.time && p.layer !== "markets" && Date.now() - p.time > state.window * 3600e3) return false;
    if (state.sev >= 0 && p.layer !== "markets" && p.layer !== "military" && p.layer !== "news") {
      if (p.sev == null || p.sev < state.sev) return false;
    }
    if (p.layer === "earthquakes" && p.mag != null && p.mag < state.mag) return false;
    if (state.q) {
      const hay = `${p.title} ${p.place} ${p.kind} ${Object.values(p.info || {}).join(" ")}`.toLowerCase();
      if (!hay.includes(state.q.toLowerCase())) return false;
    }
    return true;
  }

  const markerById = {};
  function render(id) {
    const g = groups[id];
    g.clearLayers();
    const fcol = data[id];
    let shown = 0;
    if (fcol && state.on[id]) {
      const ms = [];
      fcol.features.forEach((f) => {
        if (!passes(f)) return;
        const [lon, lat] = f.geometry.coordinates;
        const m = L.marker([lat, lon], { icon: iconFor(f), riseOnHover: true, keyboard: false });
        m.bindPopup(() => popupHtml(f.properties), { maxWidth: 360, className: "lm-popup" });
        m._lmf = f;
        markerById[f.properties.id] = m;
        ms.push(m);
        shown++;
      });
      if (g.addLayers) g.addLayers(ms); else ms.forEach((m) => g.addLayer(m));
    }
    if (state.on[id] && !map.hasLayer(g)) map.addLayer(g);
    if (!state.on[id] && map.hasLayer(g)) map.removeLayer(g);
    status[id] = Object.assign(status[id] || {}, { shown });
  }

  function renderAll() { LAYERS.forEach((l) => render(l.id)); renderSidebar(); renderFeed(); renderMarkets(); }

  // ---------------- data loading
  async function fetchLayer(id) {
    const l = BY_ID[id];
    status[id] = Object.assign(status[id] || {}, { loading: true });
    renderSidebar();
    try {
      let d;
      if (mode === "server") {
        const r = await fetch(`api/layer/${id}`, { cache: "no-store" });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        d = await r.json();
        d.metadata.via = "server";
      } else {
        if (CFG.browserLive !== false && l.browserLive && LIVE[id]) {
          try { d = await LIVE[id]({ windowHours: state.window }); }
          catch (e) { console.warn(`[${id}] browser fetch failed, using Actions snapshot`, e); }
        }
        if (!d) {
          const r = await fetch(`data/${id}.json?t=${Math.floor(Date.now() / 60000)}`);
          if (!r.ok) throw new Error(`no snapshot (HTTP ${r.status})`);
          d = await r.json();
          d.metadata.via = "snapshot";
        }
      }
      data[id] = d;
      status[id] = { loading: false, error: d.metadata.error, stale: d.metadata.stale, via: d.metadata.via };
    } catch (e) {
      status[id] = { loading: false, error: String(e.message || e), via: status[id] && status[id].via };
      if (!data[id]) data[id] = null;
    }
    render(id); renderSidebar(); renderFeed(); if (id === "markets") renderMarkets();
  }

  const lastFetch = {};
  function refresh(force) {
    LAYERS.forEach((l) => {
      if (!state.on[l.id]) return;
      if (force || !lastFetch[l.id] || Date.now() - lastFetch[l.id] >= (state.refresh || 60) * 1000 - 500) {
        lastFetch[l.id] = Date.now();
        fetchLayer(l.id);
      }
    });
  }

  // ---------------- sidebar
  function renderSidebar() {
    const ul = $("#layer-list");
    ul.innerHTML = LAYERS.map((l) => {
      const st = status[l.id] || {}, d = data[l.id], m = d && d.metadata;
      const total = d ? d.features.length : 0;
      const badge = st.loading ? '<span class="st load">…</span>'
        : st.error ? `<span class="st err" title="${esc(st.error)}">${st.stale ? "STALE" : "ERR"}</span>`
        : d ? '<span class="st ok">●</span>' : '<span class="st">–</span>';
      const upd = m && m.updated ? ago(m.updated) : "";
      const via = st.via === "browser" ? "live" : st.via === "snapshot" ? "snapshot" : st.via === "server" ? "" : "";
      const tip = esc([m && m.source, m && m.note, st.error].filter(Boolean).join("\n"));
      return `<li class="${state.on[l.id] ? "on" : "off"}" style="--c:${l.color}" title="${tip}">
        <input type="checkbox" data-id="${l.id}" ${state.on[l.id] ? "checked" : ""}>
        <span class="sw">${l.svg ? `<svg viewBox="0 0 24 24" width="16" height="16">${l.svg.replaceAll(`="C"`, `="${l.color}"`).replaceAll("#000", "#05080d")}</svg>` : "<b>%</b>"}</span>
        <span class="nm" data-solo="${l.id}">${esc(l.name)}</span>
        <span class="ct">${state.on[l.id] ? (st.shown ?? 0) + "/" : ""}${total}</span>
        ${badge}
        <span class="up">${upd}${via ? " · " + via : ""}</span></li>`;
    }).join("");
  }
  $("#layer-list").addEventListener("change", (e) => {
    const id = e.target.dataset.id; if (!id) return;
    state.on[id] = e.target.checked; save();
    if (state.on[id] && !data[id]) { lastFetch[id] = Date.now(); fetchLayer(id); }
    render(id); renderSidebar(); renderFeed();
  });
  $("#layer-list").addEventListener("click", (e) => {
    const id = e.target.dataset.solo; if (!id) return;
    LAYERS.forEach((l) => (state.on[l.id] = l.id === id)); save();
    if (!data[id]) fetchLayer(id);
    renderAll();
  });
  $("#all-on").onclick = (e) => { e.preventDefault(); LAYERS.forEach((l) => (state.on[l.id] = true)); save(); refresh(false); renderAll(); };
  $("#all-off").onclick = (e) => { e.preventDefault(); LAYERS.forEach((l) => (state.on[l.id] = false)); save(); renderAll(); };

  // ---------------- feed / ticker
  function renderFeed() {
    const items = [];
    LAYERS.forEach((l) => {
      if (!state.on[l.id] || !data[l.id] || l.id === "markets") return;
      data[l.id].features.forEach((f) => { if (f.properties.time && passes(f)) items.push(f); });
    });
    items.sort((a, b) => b.properties.time - a.properties.time);
    const top = items.slice(0, 250);
    $("#feed-count").textContent = `${items.length.toLocaleString()} events`;
    $("#feed-list").innerHTML = top.map((f) => {
      const p = f.properties, l = BY_ID[p.layer];
      return `<li data-id="${esc(p.id)}" style="--c:${l.color}">
        <span class="fl">${esc(l.name)}</span><span class="ft">${new Date(p.time).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>
        <div class="fx">${p.sev != null ? `<i class="sev s${p.sev}"></i>` : ""}${esc(p.title)}</div>
        ${p.place ? `<div class="fp">${esc(p.place)}</div>` : ""}</li>`;
    }).join("");
  }
  $("#feed-list").addEventListener("click", (e) => {
    const li = e.target.closest("li"); if (!li) return;
    const m = markerById[li.dataset.id]; if (!m) return;
    const g = groups[m._lmf.properties.layer];
    const go = () => { map.flyTo(m.getLatLng(), Math.max(map.getZoom(), 6), { duration: 1.2 }); map.once("moveend", () => (g.zoomToShowLayer ? g.zoomToShowLayer(m, () => m.openPopup()) : m.openPopup())); };
    go();
  });

  function renderMarkets() {
    const d = data.markets;
    $("#markets-strip").innerHTML = !d ? "" : d.features.map((f) => {
      const p = f.properties, up = (p.chg ?? 0) >= 0;
      return `<span class="mk ${up ? "up" : "down"}" data-id="${esc(p.id)}" title="${esc(p.title)} — ${p.open ? "market open" : "market closed"}">${esc(p.title.split(" (")[0])} <b>${p.chg == null ? "" : (up ? "+" : "") + p.chg.toFixed(2) + "%"}</b>${p.open ? '<i class="dot"></i>' : ""}</span>`;
    }).join("");
  }
  $("#markets-strip").addEventListener("click", (e) => {
    const s = e.target.closest(".mk"); if (!s) return;
    const m = markerById[s.dataset.id]; if (m) { map.flyTo(m.getLatLng(), 5); m.openPopup(); }
  });

  // ---------------- controls
  const bind = (sel, key, conv, after) => {
    const el = $(sel); el.value = state[key];
    el.addEventListener("input", () => { state[key] = conv(el.value); save(); after && after(); renderAll(); });
  };
  bind("#f-window", "window", Number, () => { if (mode === "static" && state.on.earthquakes) fetchLayer("earthquakes"); });
  bind("#f-sev", "sev", Number);
  bind("#f-mag", "mag", Number, () => ($("#f-mag-val").textContent = state.mag));
  $("#f-mag-val").textContent = state.mag;
  bind("#f-q", "q", String);
  bind("#f-refresh", "refresh", Number);
  const cl = $("#f-cluster"); cl.checked = state.cluster;
  cl.addEventListener("change", () => {
    state.cluster = cl.checked; save();
    LAYERS.forEach((l) => { map.removeLayer(groups[l.id]); groups[l.id] = makeGroup(l); });
    renderAll();
  });
  $("#refresh-now").onclick = () => refresh(true);
  $("#collapse").onclick = () => { document.body.classList.toggle("collapsed"); setTimeout(() => map.invalidateSize(), 300); };

  function tick() {
    const n = new Date();
    $("#clock-local").textContent = n.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }) + " local";
    $("#clock-utc").textContent = n.toISOString().slice(11, 19) + " UTC";
  }
  setInterval(tick, 1000); tick();
  setInterval(() => { if (state.refresh > 0) refresh(false); }, 5000);
  setInterval(renderSidebar, 15000);

  // ---------------- boot
  (async function boot() {
    try {
      if (/\.github\.io$/.test(location.hostname)) throw 0; // Pages is always static
      const r = await fetch("api/health", { cache: "no-store" });
      if (r.ok && (await r.json()).mode === "server") mode = "server";
    } catch (_) { /* static hosting */ }
    const b = $("#mode-badge");
    b.textContent = mode === "server" ? "● LIVE · local server" : "● LIVE · web";
    b.title = mode === "server" ? "Data fetched by your local Python server" :
      "Static site: CORS-friendly feeds load live in your browser; others come from snapshots refreshed by GitHub Actions";
    renderSidebar();
    refresh(true);
  })();
})();

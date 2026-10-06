// The map of the published feed: feed.geojson and status.json, next to this file.
"use strict";

const KINDS = {
  fixed: { color: "#cf222e", label: "Radar fijo" },
  section: { color: "#8250df", label: "Radar de tramo (extremo)" },
  mobile_announced: { color: "#e36209", label: "Radar móvil anunciado" },
  mobile_recurring: { color: "#bf8700", label: "Radar móvil frecuente (según multas)" },
  stretch: { color: "#0969da", label: "Tramo vigilado" },
  reported: { color: "#bf3989", label: "Aviso sin confirmar: nota de OpenStreetMap, sin zona" },
};
const OTHER = { color: "#57606a", label: "Otro" };
const SPAIN = { center: [40.2, -3.7], zoom: 6 };
// The feed is rebuilt every 6 hours. Older than this, the schedule has stopped (GitHub turns
// it off after 60 days without activity in the repository) or keeps failing.
const STALE_AFTER_MS = 24 * 3600 * 1000;

const map = L.map("map", { preferCanvas: true }).setView(SPAIN.center, SPAIN.zoom);
L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
}).addTo(map);

function el(tag, text, cls) {
  const node = document.createElement(tag);
  if (text !== undefined && text !== null) node.textContent = String(text);
  if (cls) node.className = cls;
  return node;
}

function safeLink(url, text) {
  if (typeof url !== "string" || !/^https?:\/\//.test(url)) return el("span", "sin enlace");
  const a = el("a", text || url);
  a.href = url;
  a.rel = "noopener noreferrer";
  a.target = "_blank";
  return a;
}

function popup(p, licences) {
  const dl = el("dl", null, "popup");
  const row = (term, value) => {
    dl.append(el("dt", term));
    const dd = el("dd");
    dd.append(value instanceof Node ? value : document.createTextNode(value));
    dl.append(dd);
  };
  if (p.kind === "reported") {
    // A person's note on OpenStreetMap: nobody published or checked it.
    row("Aviso", p.name || "sin texto");
    row("Estado", "sin confirmar: lo reporta una persona y ninguna fuente lo publica; no da alerta");
    row("Reportado el", p.reported || "sin fecha");
    row("Fuente", p.attribution ? `${p.source}: ${p.attribution}` : p.source || "desconocida");
    row("Licencia", licences[p.source] || p.attribution || "ver LICENSE-DATA.md");
    row("Nota", safeLink(p.url));
    return dl;
  }
  row("Nombre", p.name || "Radar");
  row("Límite", p.maxspeed ? `${p.maxspeed} km/h` : "sin dato");
  row("Estado", p.active === false ? "inactivo: su periodo acabó" : "activo");
  if (p.valid_from || p.valid_to) row("Vigencia", `${p.valid_from || "…"} a ${p.valid_to || "…"}`);
  row("Fuente", p.attribution ? `${p.source}: ${p.attribution}` : p.source || "desconocida");
  row("Licencia", licences[p.source] || p.attribution || "ver LICENSE-DATA.md");
  row("Publicado en", safeLink(p.url));
  return dl;
}

function style(p) {
  const kind = KINDS[p.kind] || OTHER;
  const dormant = p.active === false;
  const reported = p.kind === "reported";
  return {
    color: dormant ? "#8c959f" : kind.color,
    weight: p.kind === "stretch" ? 4 : reported ? 2 : 1.5,
    dashArray: dormant ? "4 4" : reported ? "2 2" : null,
    fillColor: kind.color,
    fillOpacity: dormant ? 0 : reported ? 0.3 : 0.7,
    opacity: 0.9,
    radius: 5,
  };
}

function legend() {
  const ul = document.getElementById("legend");
  for (const [kind, { color, label }] of Object.entries(KINDS)) {
    const li = el("li");
    const sw = el("span", null, kind === "stretch" ? "swatch line" : `swatch ${kind}`);
    sw.style.borderColor = color;
    if (kind !== "stretch") sw.style.background = color;
    li.append(sw, el("span", label));
    ul.append(li);
  }
  const li = el("li");
  const sw = el("span", null, "swatch dormant");
  sw.style.borderColor = "#8c959f";
  li.append(sw, el("span", "Inactivo: calle anunciada cuyo periodo acabó"));
  ul.append(li);
}

function sourcesTable(status) {
  const body = document.querySelector("#sources tbody");
  const credits = document.getElementById("attributions");
  for (const s of status.sources || []) {
    const tr = el("tr");
    tr.dataset.source = s.source;
    tr.append(
      el("td", s.source),
      el("td", s.status, s.status),
      el("td", s.in_feed),
      el("td", s.data_time ? s.data_time.replace("T", " ").replace("+00:00", " UTC") : "nunca"),
    );
    if (s.error) tr.title = s.error;
    body.append(tr);
    const terms = s.attribution.includes(s.licence) ? "" : ` (${s.licence})`;
    const updated = s.updated ? `, actualizado ${s.updated}` : "";
    credits.append(el("li", `${s.source}: ${s.attribution}${updated}${terms}`));
  }
}

async function getJSON(url) {
  const res = await fetch(url, { cache: "no-cache" });
  if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`);
  return res.json();
}

async function main() {
  legend();
  const summary = document.getElementById("summary");
  let status = { sources: [] };
  try {
    status = await getJSON("status.json");
    sourcesTable(status);
  } catch (err) {
    console.warn("status.json not loaded", err);
  }
  const generated = Date.parse(status.generated || "");
  if (Number.isFinite(generated) && Date.now() - generated > STALE_AFTER_MS) {
    const days = Math.floor((Date.now() - generated) / (24 * 3600 * 1000));
    const warn = el(
      "p",
      `Aviso: este feed no se actualiza desde hace ${days} día${days === 1 ? "" : "s"}. ` +
        "Los estados de las fuentes y qué radares están activos son de esa fecha.",
      "stale-warning",
    );
    warn.id = "stale-warning";
    document.querySelector("header").append(warn);
    document.body.dataset.stale = "true";
  }
  const licences = Object.fromEntries((status.sources || []).map((s) => [s.source, s.licence]));
  let data;
  try {
    data = await getJSON("feed.geojson");
  } catch (err) {
    summary.textContent = "No se pudo cargar el feed.";
    document.body.dataset.state = "error";
    throw err;
  }
  const features = data.features || [];
  const layer = L.geoJSON(data, {
    style: (f) => style(f.properties || {}),
    pointToLayer: (f, latlng) => L.circleMarker(latlng, style(f.properties || {})),
    onEachFeature: (f, lyr) => lyr.bindPopup(() => popup(f.properties || {}, licences)),
  }).addTo(map);
  const isReported = (f) => (f.properties || {}).kind === "reported";
  const reported = features.filter(isReported).length;
  const active = features.filter((f) => !isReported(f) && (f.properties || {}).active !== false).length;
  const when = status.generated
    ? ` Generado el ${status.generated.replace("T", " ").replace("+00:00", " UTC")}.`
    : "";
  if (features.length) {
    map.fitBounds(layer.getBounds(), { maxZoom: 12, padding: [20, 20] });
    const dormant = features.length - active - reported;
    summary.textContent =
      `${features.length} elementos: ${active} activos, ${dormant} inactivos, ` +
      `${reported} avisos sin confirmar.${when}`;
  } else {
    summary.textContent = `El feed no tiene radares ahora mismo.${when}`;
  }
  document.body.dataset.features = String(features.length);
  document.body.dataset.state = "ready";
}

main();

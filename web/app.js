import { loadVenues, buildBrandList, venuesByBrand, venuesByServing } from "./datasource.js";

const SERVING_LABEL = { tank: "Tankbier", fass: "Fassbier", unknown: "" };
const SOURCE_LABEL = { manual: "✓ verifiziert", community: "✓ geprüft", osm: "OSM" };

// Venue names/addresses/brands originate from OpenStreetMap (publicly editable),
// so every interpolated value MUST be HTML-escaped before going into markup.
const esc = (s) => String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;")
  .replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");

const OSM_STYLE = {
  version: 8,
  sources: { osm: { type: "raster", tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
                    tileSize: 256, attribution: "© OpenStreetMap contributors" } },
  layers: [{ id: "osm", type: "raster", source: "osm" }],
};

const map = new maplibregl.Map({
  container: "map", style: OSM_STYLE, center: [9.9937, 53.5511], zoom: 11.5,
  minZoom: 9, maxZoom: 18, attributionControl: false,
});

// ---- State ----
let allVenues = [];       // venues that actually carry beer data (brands.length > 0)
let brand = null;         // selected brand filter, or null
let serving = "all";      // all | draught | fass | tank
let search = "";
let brandFreq = [];       // [ [brand, venueCount], ... ] desc

const servingBar = document.getElementById("serving-bar");
const brandBar = document.getElementById("brand-bar");
const countEl = document.getElementById("count");
const searchEl = document.getElementById("search");
const topbar = document.getElementById("topbar");
const zoomCtrl = document.getElementById("zoom-ctrl");

const SERVING_DEFS = [
  { value: "all", label: "Alle Orte" },
  { value: "draught", label: "Nur Ausschank" },
  { value: "fass", label: "Nur Fassbier" },
  { value: "tank", label: "Nur Tankbier" },
];

// ---- Filtering ----
// Only venues that carry beer data are ever plotted (the raw dataset has ~4000
// restaurants, ~39 with known beer data). "draught" = fass OR tank.
function currentVenues() {
  let r = allVenues;
  const servingArg = serving === "all" ? null : serving;
  if (brand) r = venuesByBrand(r, brand, servingArg);
  else if (servingArg) r = venuesByServing(r, servingArg);
  if (search) {
    const q = search.toLowerCase();
    r = r.filter((v) =>
      (v.name || "").toLowerCase().includes(q) ||
      (v.address || "").toLowerCase().includes(q) ||
      v.brands.some((b) => b.brand.toLowerCase().includes(q)));
  }
  return r;
}

function toFC(venues) {
  return { type: "FeatureCollection", features: venues.map((v) => ({
    type: "Feature", geometry: { type: "Point", coordinates: [v.lon, v.lat] },
    properties: {
      osm_id: v.osm_id || "", name: v.name || "", address: v.address || "",
      brands: JSON.stringify(v.brands || []),
    } })) };
}

function applyFilters() {
  const r = currentVenues();
  if (map.getSource("venues")) map.getSource("venues").setData(toFC(r));
  countEl.textContent = `${r.length} Orte`;
}

// ---- Chip bars ----
function renderServingChips() {
  servingBar.querySelectorAll(".chip").forEach((el) => el.remove());
  const frag = document.createDocumentFragment();
  for (const d of SERVING_DEFS) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "chip" + (!brand && serving === d.value ? " active" : "");
    b.textContent = d.label;
    b.addEventListener("click", () => { serving = d.value; refreshChips(); applyFilters(); });
    frag.appendChild(b);
  }
  servingBar.insertBefore(frag, servingBar.firstChild);
}

function renderBrandChips() {
  brandBar.textContent = "";
  const top = brandFreq.slice(0, 9);
  if (!top.length) { brandBar.hidden = true; return; }
  brandBar.hidden = false;
  for (const [name, cnt] of top) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "chip" + (brand === name ? " active" : "");
    b.innerHTML = `${esc(name)} <span class="cnt">${cnt}</span>`;
    b.addEventListener("click", () => {
      brand = brand === name ? null : name;
      refreshChips(); applyFilters();
    });
    brandBar.appendChild(b);
  }
}

// Re-mark active states without rebuilding the whole bar.
function refreshChips() {
  const servingButtons = servingBar.querySelectorAll(".chip");
  SERVING_DEFS.forEach((d, i) =>
    servingButtons[i]?.classList.toggle("active", !brand && serving === d.value));
  const brandButtons = brandBar.querySelectorAll(".chip");
  brandFreq.slice(0, 9).forEach(([name], i) =>
    brandButtons[i]?.classList.toggle("active", brand === name));
}

// ---- HTML cluster markers (synced from the clustered source) ----
// The minimal raster style ships no glyph server, so we render markers as DOM
// elements via maplibregl.Marker instead of symbol layers.
const markers = new Map();       // id -> { marker, el, isCluster }
let markersOnScreen = new Map();

function clusterSize(count) {
  return count < 10 ? 32 : count < 30 ? 40 : count < 100 ? 48 : 56;
}

function makeClusterEl(count) {
  const el = document.createElement("div");
  el.className = "map-cluster";
  el.style.cursor = "pointer";
  updateClusterEl(el, count);
  return el;
}
function updateClusterEl(el, count) {
  const size = clusterSize(count);
  el.style.width = el.style.height = size + "px";
  el.textContent = String(count);
}

function makeVenueEl() {
  const el = document.createElement("div");
  el.className = "map-marker";
  el.style.cursor = "pointer";
  return el;
}

function updateMarkers() {
  const newMarkers = new Map();
  const features = map.querySourceFeatures("venues");
  for (const f of features) {
    const coords = f.geometry.coordinates;
    const p = f.properties;
    let id, entry;
    if (p.cluster) {
      id = "c" + p.cluster_id;
      entry = markers.get(id);
      if (!entry) {
        const el = makeClusterEl(p.point_count);
        const marker = new maplibregl.Marker({ element: el }).setLngLat(coords);
        entry = { marker, el, isCluster: true, clusterId: p.cluster_id, coords };
        el.onclick = () => {
          map.getSource("venues").getClusterExpansionZoom(entry.clusterId)
            .then((z) => map.easeTo({ center: entry.coords, zoom: Math.min(z, 18) }));
        };
        markers.set(id, entry);
      }
      // A cluster's centroid/count can shift across zoom — keep both fresh.
      entry.clusterId = p.cluster_id;
      entry.coords = coords;
      entry.marker.setLngLat(coords);
      updateClusterEl(entry.el, p.point_count);
    } else {
      id = "v" + (p.osm_id || coords.join(","));
      entry = markers.get(id);
      if (!entry) {
        const el = makeVenueEl();
        const marker = new maplibregl.Marker({ element: el }).setLngLat(coords);
        entry = { marker, el, isCluster: false };
        markers.set(id, entry);
      }
      entry.props = p;   // keep latest properties for the click handler
      entry.el.onclick = () => openVenueModal(entry.props);
    }
    newMarkers.set(id, entry);
    if (!markersOnScreen.has(id)) entry.marker.addTo(map);
  }
  for (const [id, entry] of markersOnScreen)
    if (!newMarkers.has(id)) entry.marker.remove();
  markersOnScreen = newMarkers;
}

// ---- Zoom controls ----
document.getElementById("zoom-in").addEventListener("click", () => map.zoomIn());
document.getElementById("zoom-out").addEventListener("click", () => map.zoomOut());

function positionZoomCtrl() {
  zoomCtrl.style.top = topbar.offsetHeight + 10 + "px";
}

// ---- Modal ----
const modalRoot = document.getElementById("modal-root");
const modalTitle = document.getElementById("modal-title");
const modalBody = document.getElementById("modal-body");

function openModal(title, html) {
  modalTitle.textContent = title;
  modalBody.innerHTML = html;
  modalRoot.hidden = false;
}
function closeModal() { modalRoot.hidden = true; modalBody.innerHTML = ""; }

document.getElementById("modal-close").addEventListener("click", closeModal);
document.getElementById("modal-overlay").addEventListener("click", closeModal);
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });

// --- Venue detail modal (with add/correct/close forms) ---
function beerRow(osm, b) {
  const parts = [SERVING_LABEL[b.serving], SOURCE_LABEL[b.source] || b.source, b.last_seen]
    .filter(Boolean).map(esc);
  const cls = (b.source === "manual" || b.source === "community") ? "badge manual" : "badge";
  const tank = b.serving === "tank";
  const product = (b.beer && b.beer !== b.brand)
    ? ` <span class="beer-product">${esc(b.beer)}</span>` : "";
  return `<li class="beer">
    <span class="beer-name">${esc(b.brand)}${product}<span class="${cls}">${parts.join(" · ")}</span></span>
    <form class="beerform" data-osm="${esc(osm)}" data-brand="${esc(b.brand)}" data-beer="${esc(b.beer || "")}">
      <select name="serving" aria-label="Ausschank">
        <option value="fass"${tank ? "" : " selected"}>Fass</option>
        <option value="tank"${tank ? " selected" : ""}>Tank</option>
      </select>
      <button name="act" value="edit" title="Ausschank korrigieren">✓</button>
      <button name="act" value="remove" class="danger" title="Bier entfernen">✕</button>
      <span class="msg"></span>
    </form>
  </li>`;
}

function openVenueModal(p) {
  const osm = p.osm_id || "";
  const brands = JSON.parse(p.brands || "[]");
  const beers = brands.length
    ? `<ul class="beers">${brands.map((b) => beerRow(osm, b)).join("")}</ul>`
    : `<p class="nobeers">Noch keine Biere erfasst.</p>`;
  const html =
    (p.address ? `<div class="venue-addr">${esc(p.address)}</div>` : "") +
    beers +
    `<form class="addbeer" data-osm="${esc(osm)}">
       <input name="brand" list="brandlist" placeholder="Marke hinzufügen" required>
       <input name="beer" placeholder="Sorte (optional)">
       <label><input type="radio" name="serving" value="fass" checked>Fass</label>
       <label><input type="radio" name="serving" value="tank">Tank</label>
       <input class="hp" name="hp" tabindex="-1" autocomplete="off">
       <button>+ Bier melden</button><span class="msg"></span>
     </form>
     <details class="venue-actions">
       <summary>Ort korrigieren</summary>
       <form class="venueform" data-osm="${esc(osm)}">
         <input name="address" placeholder="Adresse" value="${esc(p.address || "")}">
         <button name="act" value="edit_venue">Adresse speichern</button>
         <button name="act" value="close_venue" class="danger">Als geschlossen melden</button>
         <span class="msg"></span>
       </form>
     </details>`;
  openModal(p.name || "Kneipe", html);
}

// --- Statistik / Über / Kontakt / Bier melden ---
function openStats() {
  const rows = brandFreq.slice(0, 10);
  const max = rows.length ? rows[0][1] : 1;
  const html = rows.map(([name, cnt]) => `
    <div class="stat-row">
      <span class="stat-name">${esc(name)}</span>
      <div class="stat-track"><div class="stat-fill" style="width:${Math.round((cnt / max) * 100)}%"></div></div>
      <span class="stat-count">${cnt}</span>
    </div>`).join("") || `<p class="nobeers">Noch keine Daten.</p>`;
  openModal("Statistik", html);
}

function openAbout() {
  openModal("Über das Projekt", `<div class="modal-text"><p>Zapfkompass zeigt, wo es in Hamburg Bier vom Fass oder Tank gibt — Marke für Marke. Die Basis bilden von Hand geprüfte Einträge, ergänzt um OpenStreetMap-Daten und die „Wo gibt&#39;s das?“-Seiten der Brauereien. Jede Verknüpfung trägt eine Quelle und ein Prüfdatum.</p></div>`);
}

function openContact() {
  openModal("Kontakt", `<div class="modal-text">
    <p>Fehler entdeckt oder eine Kneipe fehlt? Schreib uns: <a href="mailto:beermap@jakubwaller.eu">beermap@jakubwaller.eu</a></p>
    <p>Rechtliches: <a href="impressum.html">Impressum</a> · <a href="datenschutz.html">Datenschutz</a></p>
  </div>`);
}

function openAddInfo() {
  openModal("Bier melden", `<div class="modal-text"><p>Klick auf eine Kneipe direkt auf der Karte — dort kannst du eine Marke und Ausschankart (Fass/Tank) melden.</p></div>`);
}

document.querySelectorAll("[data-modal]").forEach((el) => {
  const kind = el.getAttribute("data-modal");
  el.addEventListener("click", () => ({ stats: openStats, about: openAbout, contact: openContact }[kind])());
});
document.getElementById("cta-add").addEventListener("click", openAddInfo);
searchEl.addEventListener("input", () => { search = searchEl.value; applyFilters(); });

// ---- Submission forms (delegated on the modal) ----
function submissionBody(form, action) {
  const osm = form.dataset.osm;
  if (form.classList.contains("addbeer"))
    return { venue_osm_id: osm, brand: form.brand.value.trim(),
             beer: form.beer.value.trim() || null,
             serving: form.serving.value, kind: "add", hp: form.hp.value };
  if (form.classList.contains("beerform"))
    return action === "remove"
      ? { venue_osm_id: osm, brand: form.dataset.brand, beer: form.dataset.beer || null, kind: "remove" }
      : { venue_osm_id: osm, brand: form.dataset.brand, beer: form.dataset.beer || null,
          serving: form.serving.value, kind: "add" };
  if (form.classList.contains("venueform"))
    return action === "close_venue"
      ? { venue_osm_id: osm, kind: "close_venue" }
      : { venue_osm_id: osm, kind: "edit_venue", address: form.address.value.trim() };
  return null;
}

modalBody.addEventListener("submit", async (ev) => {
  const f = ev.target;
  if (!f.matches(".addbeer, .beerform, .venueform")) return;
  ev.preventDefault();
  const action = ev.submitter && ev.submitter.value;
  if (action === "close_venue" &&
      !confirm("Diesen Ort wirklich als dauerhaft geschlossen melden?")) return;
  const body = submissionBody(f, action);
  if (!body) return;
  const msg = f.querySelector(".msg");
  const r = await fetch("/api/submit", { method: "POST",
    headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (msg) msg.textContent = r.ok ? " Danke, wird geprüft!" : " Fehler";
  if (r.ok) f.querySelectorAll("button, input, select").forEach((el) => (el.disabled = true));
});

// ---- Boot ----
// The filter chrome (chips, search, count) is deliberately decoupled from the
// map's WebGL "load" event: the UI stays usable even while the map is still
// warming up. The map source/markers are attached once BOTH the venue data and
// the map style are ready (whichever finishes last), via addMapData().
let dataReady = false, styleReady = false;

function syncMarkers() {
  if (map.getSource("venues") && map.isSourceLoaded("venues")) updateMarkers();
}

function addMapData() {
  if (!dataReady || !styleReady || map.getSource("venues")) return;
  map.addSource("venues", {
    type: "geojson", data: toFC(currentVenues()),
    cluster: true, clusterRadius: 46, clusterMaxZoom: 16,
  });
  // maplibregl.Marker repositions itself on pan/zoom, so we only re-sync the
  // marker SET when clustering can change: after a move, and whenever the
  // (async) clustered source finishes (re)loading its features.
  map.on("moveend", syncMarkers);
  map.on("sourcedata", (e) => { if (e.sourceId === "venues" && e.isSourceLoaded) syncMarkers(); });
  syncMarkers();
}

map.on("load", () => { styleReady = true; addMapData(); });

async function boot() {
  const fc = await (await fetch("data/venues.json")).json();
  allVenues = loadVenues(fc).filter((v) => v.brands.length > 0);

  // Brand frequency = number of venues serving each brand, desc.
  const freq = new Map();
  for (const v of allVenues) {
    const seen = new Set();
    for (const b of v.brands) {
      if (seen.has(b.brand)) continue;
      seen.add(b.brand);
      freq.set(b.brand, (freq.get(b.brand) || 0) + 1);
    }
  }
  brandFreq = [...freq.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0], "de"));

  // Datalist for the "Marke hinzufügen" autocomplete.
  const dl = document.createElement("datalist"); dl.id = "brandlist";
  for (const b of buildBrandList(allVenues)) {
    const o = document.createElement("option"); o.value = b; dl.appendChild(o);
  }
  document.body.appendChild(dl);

  renderServingChips();
  renderBrandChips();
  positionZoomCtrl();
  applyFilters();          // updates the count now; setData is a no-op until the source exists
  dataReady = true;
  addMapData();
}

boot();
window.addEventListener("resize", positionZoomCtrl);

import { loadVenues, buildBrandList, venuesByBrand, venuesByServing } from "./datasource.js";

const SERVING_LABEL = { tank: "Tankbier", fass: "Fassbier", unknown: "" };
const SOURCE_LABEL = { manual: "✓ verifiziert", community: "✓ geprüft", osm: "OSM" };

// Venue names/addresses/brands originate from OpenStreetMap (publicly editable),
// so every interpolated value MUST be HTML-escaped before going into a popup.
const esc = (s) => String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;")
  .replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");

const OSM_STYLE = {
  version: 8,
  sources: { osm: { type: "raster", tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
                    tileSize: 256, attribution: "© OpenStreetMap contributors" } },
  layers: [{ id: "osm", type: "raster", source: "osm" }],
};

const map = new maplibregl.Map({ container: "map", style: OSM_STYLE, center: [9.9937, 53.5511], zoom: 12 });
const brandSelect = document.getElementById("brand");
const servingSelect = document.getElementById("serving");
const countEl = document.getElementById("count");
let allVenues = [];

// Collapsible filter panel — starts collapsed on small screens so it doesn't cover the map.
const panel = document.getElementById("panel");
const panelToggle = document.getElementById("panel-toggle");
function setPanelOpen(open) {
  panel.classList.toggle("is-open", open);
  panelToggle.setAttribute("aria-expanded", String(open));
}
panelToggle.addEventListener("click", () => setPanelOpen(!panel.classList.contains("is-open")));
if (window.matchMedia("(max-width: 600px)").matches) setPanelOpen(false);

// Keep popups inside the viewport on phones.
const popupMaxWidth = () => Math.min(340, window.innerWidth - 28) + "px";

// Keep feature properties small: store the raw data and build the (much larger)
// interactive popup markup lazily on click in buildPopupHtml().
function toFC(venues) {
  return { type: "FeatureCollection", features: venues.map((v) => ({
    type: "Feature", geometry: { type: "Point", coordinates: [v.lon, v.lat] },
    properties: {
      osm_id: v.osm_id || "", name: v.name || "", address: v.address || "",
      brands: JSON.stringify(v.brands || []),
    } })) };
}

// One editable row per existing beer: correct the serving or remove it.
function beerRow(osm, b) {
  const parts = [SERVING_LABEL[b.serving], SOURCE_LABEL[b.source] || b.source, b.last_seen]
    .filter(Boolean).map(esc);
  const cls = (b.source === "manual" || b.source === "community") ? "badge manual" : "badge";
  const tank = b.serving === "tank";
  return `<li class="beer">
    <span class="beer-name">${esc(b.brand)}<span class="${cls}">${parts.join(" · ")}</span></span>
    <form class="beerform" data-osm="${esc(osm)}" data-brand="${esc(b.brand)}">
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

function buildPopupHtml(p) {
  const osm = p.osm_id || "";
  const brands = JSON.parse(p.brands || "[]");
  const beers = brands.length
    ? `<ul class="beers">${brands.map((b) => beerRow(osm, b)).join("")}</ul>`
    : `<p class="nobeers">Noch keine Biere erfasst.</p>`;
  return `<strong>${esc(p.name)}</strong>` +
    (p.address ? `<div class="addr">${esc(p.address)}</div>` : "") +
    beers +
    `<form class="addbeer" data-osm="${esc(osm)}">
       <input name="brand" list="brandlist" placeholder="Marke hinzufügen" required>
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
}

// Translate a submitted popup form into an /api/submit body. Returns null if the
// form isn't one we handle.
function submissionBody(form, action) {
  const osm = form.dataset.osm;
  if (form.classList.contains("addbeer"))
    return { venue_osm_id: osm, brand: form.brand.value.trim(),
             serving: form.serving.value, kind: "add", hp: form.hp.value };
  if (form.classList.contains("beerform"))
    return action === "remove"
      ? { venue_osm_id: osm, brand: form.dataset.brand, kind: "remove" }
      : { venue_osm_id: osm, brand: form.dataset.brand, serving: form.serving.value, kind: "add" };
  if (form.classList.contains("venueform"))
    return action === "close_venue"
      ? { venue_osm_id: osm, kind: "close_venue" }
      : { venue_osm_id: osm, kind: "edit_venue", address: form.address.value.trim() };
  return null;
}

function render(venues) {
  map.getSource("venues").setData(toFC(venues));
  countEl.textContent = `${venues.length} Orte`;
}

function applyFilters() {
  const b = brandSelect.value, s = servingSelect.value;
  let r = allVenues;
  if (b) r = venuesByBrand(r, b, s || null);
  else if (s) r = venuesByServing(r, s);
  render(r);
}

map.on("load", async () => {
  allVenues = loadVenues(await (await fetch("data/venues.json")).json());
  for (const brand of buildBrandList(allVenues)) {
    const o = document.createElement("option");
    o.value = o.textContent = brand;
    brandSelect.appendChild(o);
  }
  const dl = document.createElement("datalist"); dl.id = "brandlist";
  for (const b of buildBrandList(allVenues)) {
    const o = document.createElement("option"); o.value = b; dl.appendChild(o);
  }
  document.body.appendChild(dl);
  map.addSource("venues", { type: "geojson", data: toFC(allVenues) });
  map.addLayer({ id: "dots", type: "circle", source: "venues",
    paint: { "circle-radius": 6, "circle-color": "#c8102e", "circle-stroke-width": 1, "circle-stroke-color": "#fff" } });
  render(allVenues);
  map.on("click", "dots", (e) => {
    const coords = e.features[0].geometry.coordinates.slice();
    new maplibregl.Popup({ maxWidth: popupMaxWidth(), focusAfterOpen: false })
      .setLngLat(coords).setHTML(buildPopupHtml(e.features[0].properties)).addTo(map);
    // Pan so the popup (which opens above the dot) isn't hidden under the panel/edge.
    map.easeTo({ center: coords, offset: [0, 90], duration: 400 });
  });
  map.on("mouseenter", "dots", () => (map.getCanvas().style.cursor = "pointer"));
  map.on("mouseleave", "dots", () => (map.getCanvas().style.cursor = ""));
  brandSelect.addEventListener("change", applyFilters);
  servingSelect.addEventListener("change", applyFilters);

  document.getElementById("map").addEventListener("submit", async (ev) => {
    const f = ev.target;
    if (!f.matches(".addbeer, .beerform, .venueform")) return;
    ev.preventDefault();
    const action = ev.submitter && ev.submitter.value;  // distinguishes multi-button forms
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
});
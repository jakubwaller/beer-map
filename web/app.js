import { loadVenues, buildBrandList, venuesByBrand, venuesByServing, searchVenues, fold,
         brandChips, searchBrands, tilesForBounds }
  from "./datasource.js?v=__ASSET_VERSION__";
import { openState, statusText, formatWeek, venueSchedule, venuesOpenNow }
  from "./hours.js?v=__ASSET_VERSION__";
import { initLang, getLang, setLang, t, tn }
  from "./i18n.js?v=__ASSET_VERSION__";

initLang();

const servingLabel = (s) =>
  s === "tank" ? t("badge.tank") : s === "fass" ? t("badge.fass") : "";
const sourceLabel = (s) =>
  s === "manual" ? t("source.manual") : s === "community" ? t("source.community")
  : s === "osm" ? "OSM" : s;

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

// Whole-map view (Germany + Czechia + Austria); `bounds` at init adapts the
// zoom to the viewport (a phone gets a wider zoom than a desktop for the same
// box). The topbar overlays the map, so the fit needs extra top padding or
// Hamburg hides under the chips.
const MAP_BOUNDS = [[5.5, 46.2], [19.2, 55.1]];
const CITY_VIEWS = {
  berlin: { center: [13.4050, 52.5200], zoom: 11 },
  bremen: { center: [8.8017, 53.0793], zoom: 11.5 },
  dresden: { center: [13.7373, 51.0504], zoom: 11.5 },
  duesseldorf: { center: [6.7735, 51.2277], zoom: 11.5 },
  frankfurt: { center: [8.6821, 50.1109], zoom: 11.5 },
  hamburg: { center: [9.9937, 53.5511], zoom: 11.5 },
  hannover: { center: [9.7320, 52.3759], zoom: 11.5 },
  koeln: { center: [6.9603, 50.9375], zoom: 11.5 },
  leipzig: { center: [12.3731, 51.3397], zoom: 11.5 },
  muenchen: { center: [11.5820, 48.1351], zoom: 11.5 },
  nuernberg: { center: [11.0767, 49.4521], zoom: 11.5 },
  stuttgart: { center: [9.1829, 48.7758], zoom: 11.5 },
  praha: { center: [14.4378, 50.0755], zoom: 11 },
  brno: { center: [16.6068, 49.1951], zoom: 11.5 },
  plzen: { center: [13.3776, 49.7475], zoom: 11.5 },
  ostrava: { center: [18.2820, 49.8209], zoom: 11.5 },
  budejovice: { center: [14.4747, 48.9745], zoom: 12 },
  boleslav: { center: [14.9058, 50.4114], zoom: 12.5 },
  wien: { center: [16.3721, 48.2082], zoom: 11 },
  graz: { center: [15.4395, 47.0707], zoom: 11.5 },
  linz: { center: [14.2858, 48.3059], zoom: 12 },
  salzburg: { center: [13.0430, 47.8022], zoom: 12 },
  innsbruck: { center: [11.3928, 47.2654], zoom: 12 },
  klagenfurt: { center: [14.3053, 46.6247], zoom: 12 },
};
const dePadding = () => ({
  top: document.getElementById("topbar").offsetHeight + 16,
  left: 24, right: 24, bottom: 24,
});

const map = new maplibregl.Map({
  container: "map", style: OSM_STYLE, bounds: MAP_BOUNDS,
  fitBoundsOptions: { padding: dePadding() },
  minZoom: 4.5, maxZoom: 18, attributionControl: false,
  // Flat 2D map: without this an off-axis pinch on a phone starts rotating or
  // tilting the view, which reads as the map "not responding" to the zoom.
  dragRotate: false, pitchWithRotate: false, touchPitch: false,
});
map.touchZoomRotate.disableRotation();

// ---- State ----
let allVenues = [];       // venues that carry beer data (brands.length > 0)
let grayVenues = [];      // the rest of the dataset (no beer data yet) — gray dots
let brand = null;         // selected brand filter, or null
// Default "all" so the gray no-data venues show too; "draught" narrows to
// venues with at least one known brand (fass/tank verified or not).
let serving = "all";      // all | draught | fass | tank
let openNow = false;      // "Jetzt geöffnet" toggle — orthogonal to the above
let search = "";
let brandFreq = [];       // [ [brand, venueCount], ... ] desc

const servingBar = document.getElementById("serving-bar");
const brandBar = document.getElementById("brand-bar");
const countEl = document.getElementById("count");
const searchEl = document.getElementById("search");
const resultsEl = document.getElementById("search-results");
const clearEl = document.getElementById("search-clear");
const topbar = document.getElementById("topbar");
const zoomCtrl = document.getElementById("zoom-ctrl");

const SERVING_DEFS = [
  { value: "all", key: "serving.all" },
  { value: "draught", key: "serving.draught" },
  { value: "fass", key: "serving.fass" },
  { value: "tank", key: "serving.tank" },
];

// Brand chips that fit the bar before the "all brands" picker takes over.
const TOP_BRAND_CHIPS = 9;

// ---- Filtering ----
// Venues with beer data get the amber DOM markers; the rest of the dataset
// (~30k OSM pubs/bars without beer data, from the fully swept cities) shows
// as small gray dots, but only on "Alle Orte" with no brand selected.
// "draught" = any known brand; fass/tank require a verified serving.
function currentVenues() {
  let r = allVenues;
  const servingArg = serving === "all" ? null : serving;
  if (brand) r = venuesByBrand(r, brand, servingArg);
  else if (servingArg) r = venuesByServing(r, servingArg);
  if (openNow) r = venuesOpenNow(r);
  return searchVenues(r, search);
}

const grayVisible = () => serving === "all" && !brand;
const currentGrayVenues = () =>
  (grayVisible() ? searchVenues(openNow ? venuesOpenNow(grayVenues) : grayVenues, search) : []);

function applyFilters() {
  const n = currentVenues().length + currentGrayVenues().length;
  countEl.textContent = search.trim() ? tn("count.hits", n) : tn("count.places", n);
  refreshMarkers();
  refreshGrayLayer();
}

// "Open now" is the one filter that goes stale on its own: a map left open on
// the counter must not still show a pub that shut at 23:00. Re-check every
// minute while the toggle is on, and redraw only when the answer moved —
// setData on tens of thousands of gray dots is not something to do for nothing.
const OPEN_NOW_TICK_MS = 60000;
let openNowTimer = null;
let openNowSig = "";

const openNowSignature = () => `${currentVenues().length}/${currentGrayVenues().length}`;

function watchOpenNow() {
  clearInterval(openNowTimer);
  openNowTimer = null;
  if (!openNow) return;
  openNowSig = openNowSignature();
  openNowTimer = setInterval(() => {
    const sig = openNowSignature();
    if (sig === openNowSig) return;
    openNowSig = sig;
    applyFilters();
  }, OPEN_NOW_TICK_MS);
}

// ---- Chip bars ----
function renderServingChips() {
  servingBar.querySelectorAll(".chip").forEach((el) => el.remove());
  const frag = document.createDocumentFragment();
  // The open-now toggle rides in the same bar but is not part of the group: it
  // combines with whichever serving (and brand) is selected, so it is a
  // pressed/unpressed switch rather than a fifth mutually exclusive option.
  // It leads the bar because on a phone that bar scrolls — anything after the
  // four serving chips starts off-screen, which for a new filter means
  // undiscovered.
  const now = document.createElement("button");
  now.type = "button";
  now.id = "chip-open-now";
  now.className = "chip toggle" + (openNow ? " active" : "");
  now.setAttribute("aria-pressed", String(openNow));
  now.title = t("serving.openNowHint");
  now.innerHTML = `<span aria-hidden="true">🕒</span> ${esc(t("serving.openNow"))}`;
  now.addEventListener("click", () => {
    openNow = !openNow;
    refreshChips();
    applyFilters();
    watchOpenNow();
  });
  frag.appendChild(now);
  for (const d of SERVING_DEFS) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "chip" + (serving === d.value ? " active" : "");
    b.dataset.serving = d.value;
    b.textContent = t(d.key);
    b.addEventListener("click", () => { serving = d.value; refreshChips(); applyFilters(); });
    frag.appendChild(b);
  }
  servingBar.insertBefore(frag, servingBar.firstChild);
}

function renderBrandChips() {
  brandBar.textContent = "";
  const chips = brandChips(brandFreq, TOP_BRAND_CHIPS, brand);
  if (!chips.length) { brandBar.hidden = true; return; }
  brandBar.hidden = false;
  for (const [name, cnt] of chips) {
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
  // Nine chips out of ~1500 brands: everything else is one tap away here.
  const more = document.createElement("button");
  more.type = "button";
  more.className = "chip more";
  more.innerHTML = `${esc(t("brands.all"))} <span class="cnt">${brandFreq.length}</span>`;
  more.addEventListener("click", openBrandPicker);
  brandBar.appendChild(more);
  scrollChipIntoView(brandBar.querySelector(".chip.active"));
}

// The bar scrolls horizontally, and the picker that selects a brand sits at its
// far right end — so the chip for the brand just chosen lands off-screen to the
// left unless the bar is nudged back to it.
function scrollChipIntoView(chip) {
  if (!chip) return;
  const bar = brandBar.getBoundingClientRect();
  const box = chip.getBoundingClientRect();
  if (!box.width) return;   // not laid out yet (boot)
  if (box.left < bar.left) brandBar.scrollLeft -= bar.left - box.left + 12;
  else if (box.right > bar.right) brandBar.scrollLeft += box.right - bar.right + 12;
}

// Re-mark active states. The serving chips only change class; the brand bar is
// rebuilt because its contents depend on the selection (a brand picked from the
// full list joins the bar, and leaves it again when switched off).
function refreshChips() {
  const servingButtons = servingBar.querySelectorAll(".chip[data-serving]");
  SERVING_DEFS.forEach((d, i) =>
    servingButtons[i]?.classList.toggle("active", serving === d.value));
  const nowChip = document.getElementById("chip-open-now");
  if (nowChip) {
    nowChip.classList.toggle("active", openNow);
    nowChip.setAttribute("aria-pressed", String(openNow));
  }
  renderBrandChips();
}

// ---- Brand picker (every brand, searchable) ----
// Rendering ~1500 buttons at once is what would make the list stutter on a
// phone, so the view is capped and the rest is reachable by typing.
const BRAND_PICKER_MAX = 200;

function brandListHTML(query) {
  const matches = searchBrands(brandFreq, query);
  if (!matches.length)
    return `<div class="suggest-empty">${esc(t("brands.none", { q: query.trim() }))}</div>`;
  const rows = matches.slice(0, BRAND_PICKER_MAX).map(([name, cnt]) =>
    `<button type="button" class="brand-pick${brand === name ? " active" : ""}" data-value="${esc(name)}">
       <span class="brand-pick-name">${esc(name)}</span><span class="cnt">${cnt}</span>
     </button>`).join("");
  const rest = matches.length - BRAND_PICKER_MAX;
  return rows + (rest > 0 ? `<div class="brand-more">${esc(tn("brands.more", rest))}</div>` : "");
}

function pickBrand(name) {
  brand = brand === name ? null : name;
  closeModal();
  refreshChips();
  applyFilters();
}

function openBrandPicker() {
  openModal(t("brands.title"), `
    <div class="brand-picker">
      <input id="brand-filter" type="search" placeholder="${esc(t("brands.search"))}"
             autocomplete="off" autocorrect="off" autocapitalize="off" spellcheck="false"
             aria-label="${esc(t("brands.search"))}">
      <div id="brand-list" class="brand-list" role="listbox" aria-label="${esc(t("brands.title"))}"></div>
      ${brand ? `<button type="button" id="brand-reset" class="brand-reset">${esc(t("brands.reset"))}</button>` : ""}
    </div>`);
  const input = document.getElementById("brand-filter");
  const list = document.getElementById("brand-list");
  const render = () => { list.innerHTML = brandListHTML(input.value); };
  render();
  input.addEventListener("input", render);
  input.addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    e.preventDefault();          // pick the top match instead of submitting
    const first = list.querySelector(".brand-pick");
    if (first) pickBrand(first.dataset.value);
  });
  list.addEventListener("click", (e) => {
    const row = e.target.closest(".brand-pick");
    if (row) pickBrand(row.dataset.value);
  });
  document.getElementById("brand-reset")?.addEventListener("click", () => {
    brand = null;
    closeModal();
    refreshChips();
    applyFilters();
  });
  // Only on a pointer device: autofocusing on a phone raises the keyboard over
  // the very list the visitor came to browse.
  if (!matchMedia("(pointer: coarse)").matches) input.focus();
}

// ---- Markers with grid-bucket clustering ----
// Clustering is done here in JS (project each venue to screen pixels, bucket by
// a 46px grid) rather than via MapLibre's source clustering: the minimal raster
// style ships no glyph server for cluster-count symbol layers, and this keeps
// the whole marker pipeline independent of the source/tile machinery. Markers
// are plain maplibregl.Marker elements, rebuilt whenever the filtered set or
// the view changes (only venues with beer data get DOM markers, and only the
// ones inside the viewport — the ~30k no-data venues render as a circle
// layer, see below).
const CELL_PX = 46;
// Off-screen margin (px) that still gets markers: keeps clusters at the edge
// honest and covers small pans until the next moveend rebuild.
const VIEW_PAD = 80;
let liveMarkers = [];

function clusterSize(count) {
  return count < 10 ? 32 : count < 30 ? 40 : count < 100 ? 48 : 56;
}

function makeClusterEl(count) {
  const el = document.createElement("div");
  el.className = "map-cluster";
  el.style.cursor = "pointer";
  const size = clusterSize(count);
  el.style.width = el.style.height = size + "px";
  el.textContent = String(count);
  return el;
}

function makeVenueEl() {
  const el = document.createElement("div");
  el.className = "map-marker";
  el.style.cursor = "pointer";
  return el;
}

function refreshMarkers() {
  if (!dataReady || !styleReady) return;
  for (const m of liveMarkers) m.remove();
  liveMarkers = [];

  // Cull to the viewport before creating any DOM. The dataset is Germany-wide
  // (~3k branded venues), so at city zoom nearly all of it projects far
  // off-screen — without this cull each of those venues still became a live
  // DOM marker whose transform MapLibre updates every frame, which is what
  // made panning/zooming janky on phones.
  const vw = map.getContainer().clientWidth;
  const vh = map.getContainer().clientHeight;
  const buckets = new Map();
  for (const v of currentVenues()) {
    const pt = map.project([v.lon, v.lat]);
    if (pt.x < -VIEW_PAD || pt.x > vw + VIEW_PAD ||
        pt.y < -VIEW_PAD || pt.y > vh + VIEW_PAD) continue;
    const key = Math.round(pt.x / CELL_PX) + "," + Math.round(pt.y / CELL_PX);
    let b = buckets.get(key);
    if (!b) { b = []; buckets.set(key, b); }
    b.push(v);
  }

  const dotBoxes = []; // screen-space [x1,y1,x2,y2] of every dot/cluster
  const singles = [];  // label candidates: un-clustered venues

  for (const bucket of buckets.values()) {
    const lon = bucket.reduce((s, v) => s + v.lon, 0) / bucket.length;
    const lat = bucket.reduce((s, v) => s + v.lat, 0) / bucket.length;
    const pt = map.project([lon, lat]);
    let el;
    if (bucket.length > 1) {
      el = makeClusterEl(bucket.length);
      const half = clusterSize(bucket.length) / 2;
      dotBoxes.push([pt.x - half, pt.y - half, pt.x + half, pt.y + half]);
      el.onclick = (e) => {
        e.stopPropagation();   // ...so close the dropdown by hand: at maxZoom
        closeSuggestions();    // easeTo only pans and emits no zoomstart either
        map.easeTo({ center: [lon, lat], zoom: Math.min(map.getZoom() + 2.2, 18) });
      };
    } else {
      const v = bucket[0];
      el = makeVenueEl();
      dotBoxes.push([pt.x - 10, pt.y - 10, pt.x + 10, pt.y + 10]);
      // stopPropagation: don't let the click fall through to the gray-dot layer.
      el.onclick = (e) => { e.stopPropagation(); openVenueModal(v); };
      singles.push({ v, pt, el });
    }
    liveMarkers.push(new maplibregl.Marker({ element: el }).setLngLat([lon, lat]).addTo(map));
  }

  placeLabels(singles, dotBoxes);
}

// ---- Venue name labels ----
// Names render as DOM children of un-clustered markers, placed greedily:
// a label appears only if its measured box overlaps no dot/cluster and no
// already-placed label. Runs with every marker rebuild (moveend), so the
// visible set adapts to zoom and pan; sorted by name so the winners are
// stable while panning instead of flickering between candidates.
const LABEL_MIN_ZOOM = 12;
const LABEL_FONT = "600 11px 'Work Sans', system-ui, sans-serif";
const LABEL_MAX_CHARS = 28;
const labelCtx = document.createElement("canvas").getContext("2d");

function placeLabels(singles, dotBoxes) {
  if (map.getZoom() < LABEL_MIN_ZOOM) return;
  const w = map.getContainer().clientWidth;
  const h = map.getContainer().clientHeight;
  labelCtx.font = LABEL_FONT;
  const boxes = dotBoxes;
  singles.sort((a, b) => (a.v.name || "").localeCompare(b.v.name || ""));
  for (const { v, pt, el } of singles) {
    if (!v.name) continue;
    if (pt.x < -60 || pt.x > w + 60 || pt.y < -30 || pt.y > h + 30) continue;
    const text = v.name.length > LABEL_MAX_CHARS
      ? v.name.slice(0, LABEL_MAX_CHARS - 1).trimEnd() + "…" : v.name;
    const halfW = labelCtx.measureText(text).width / 2 + 3;
    // Below the 16px dot: top edge ~11px under the marker center, ~15px tall.
    const box = [pt.x - halfW, pt.y + 11, pt.x + halfW, pt.y + 26];
    if (boxes.some((b) => box[0] < b[2] && box[2] > b[0] && box[1] < b[3] && box[3] > b[1]))
      continue;
    boxes.push(box);
    const label = document.createElement("div");
    label.className = "marker-label";
    label.textContent = text;
    el.appendChild(label);
  }
}

// ---- Gray dots: venues without beer data ----
// Unlike the (few) beer venues above, these are the brandless rest of the
// dataset — since the nationwide sweep ~250k venues across DE+CZ+AT — so they
// render as a WebGL circle layer instead of DOM markers, and they are not
// shipped as a file at all: the viewport's slippy tiles load on demand from
// /api/gray. Clicking a dot opens the normal venue modal, whose "Marke
// hinzufügen" form turns the gray dot into data — anywhere, not just in the
// sweep cities.
const GRAY_SOURCE = "gray-venues";
const GRAY_LAYER = "gray-venues";
// Below this zoom the gray layer neither renders nor loads: a country-wide
// gray blanket would be noise, and the tile fan-out unbounded.
const GRAY_MIN_ZOOM = 10;
const GRAY_TILE_Z = 10;
const grayTilesLoaded = new Set();  // fetched or in-flight tile keys
let graySourceStale = true;         // venue set / filters changed since last setData

function grayFC(venues) {
  return { type: "FeatureCollection", features: venues.map((v) => ({
    type: "Feature",
    geometry: { type: "Point", coordinates: [v.lon, v.lat] },
    properties: { idx: v.grayIdx },
  })) };
}

function addGrayLayer() {
  map.addSource(GRAY_SOURCE, { type: "geojson", data: grayFC([]) });
  map.addLayer({
    id: GRAY_LAYER, type: "circle", source: GRAY_SOURCE,
    layout: { visibility: "none" },
    paint: {
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 10, 2, 13, 3.5, 16, 6],
      "circle-color": "#a89f93",
      "circle-opacity": 0.6,
      "circle-stroke-width": 1,
      "circle-stroke-color": "#fff",
      "circle-stroke-opacity": 0.5,
    },
  });
  map.on("click", GRAY_LAYER, (e) => {
    const v = grayVenues[e.features[0].properties.idx];
    if (v) openVenueModal(v);
  });
  map.on("mouseenter", GRAY_LAYER, () => { map.getCanvas().style.cursor = "pointer"; });
  map.on("mouseleave", GRAY_LAYER, () => { map.getCanvas().style.cursor = ""; });
}

const grayLayerVisible = () => grayVisible() && map.getZoom() >= GRAY_MIN_ZOOM;

// Called when the venue set or the filters changed — marks the source dirty.
function refreshGrayLayer() {
  if (!dataReady || !styleReady) return;
  graySourceStale = true;
  updateGrayLayer();
}

// Called on every moveend too; cheap unless the source is actually stale
// (setData on tens of thousands of points is what would jank a pan).
function updateGrayLayer() {
  if (!dataReady || !styleReady) return;
  const visible = grayLayerVisible();
  map.setLayoutProperty(GRAY_LAYER, "visibility", visible ? "visible" : "none");
  if (!visible) return;
  if (graySourceStale) {
    map.getSource(GRAY_SOURCE).setData(grayFC(currentGrayVenues()));
    graySourceStale = false;
  }
  loadGrayTiles();
}

// Fetch the viewport's not-yet-loaded gray tiles and merge them in. A failed
// tile is forgotten so a later pan retries it; a failure only ever means
// "no gray dots there yet" — the branded map is untouched.
async function loadGrayTiles() {
  const b = map.getBounds();
  const tiles = tilesForBounds(
    { west: b.getWest(), south: b.getSouth(), east: b.getEast(), north: b.getNorth() },
    GRAY_TILE_Z).filter((t) => !grayTilesLoaded.has(t.key));
  if (!tiles.length) return;
  for (const t of tiles) grayTilesLoaded.add(t.key);
  const batches = await Promise.all(tiles.map(async (t) => {
    try {
      const r = await fetch(`/api/gray/${t.key}`);
      if (!r.ok) throw new Error(String(r.status));
      return loadVenues(await r.json());
    } catch {
      grayTilesLoaded.delete(t.key);
      return [];
    }
  }));
  const fresh = batches.flat().filter((v) => v.osm_id && !knownIds.has(v.osm_id));
  if (!fresh.length) return;
  for (const v of fresh) { knownIds.add(v.osm_id); remoteHits.delete(v.osm_id); }
  setGrayVenues(grayVenues.concat(fresh));
  rebuildSearchPool();
  graySourceStale = true;
  applyFilters();          // count + gray layer now include the new arrivals
}

// ---- Zoom controls ----
document.getElementById("zoom-in").addEventListener("click", () => map.zoomIn());
document.getElementById("zoom-out").addEventListener("click", () => map.zoomOut());

function positionZoomCtrl() {
  zoomCtrl.style.top = topbar.offsetHeight + 10 + "px";
}

// ---- Toast (transient status messages) ----
const toastEl = document.getElementById("toast");
let toastTimer = null;
function showToast(text) {
  toastEl.textContent = text;
  toastEl.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { toastEl.hidden = true; }, 4000);
}

// ---- "Mein Standort" ----
const locateBtn = document.getElementById("locate");
let userMarker = null;

function showUserLocation(lngLat) {
  if (!userMarker) {
    const el = document.createElement("div");
    el.className = "user-location";
    userMarker = new maplibregl.Marker({ element: el }).setLngLat(lngLat).addTo(map);
  } else {
    userMarker.setLngLat(lngLat);
  }
  map.flyTo({ center: lngLat, zoom: Math.max(map.getZoom(), 14) });
}

locateBtn.addEventListener("click", () => {
  if (!navigator.geolocation) {
    showToast(t("toast.noGeo"));
    return;
  }
  locateBtn.classList.add("locating");
  navigator.geolocation.getCurrentPosition(
    (pos) => {
      locateBtn.classList.remove("locating");
      showUserLocation([pos.coords.longitude, pos.coords.latitude]);
    },
    (err) => {
      locateBtn.classList.remove("locating");
      showToast(err.code === 1 ? t("toast.denied") : t("toast.failed"));
    },
    { enableHighAccuracy: true, timeout: 10000, maximumAge: 30000 }
  );
});

// ---- Modal ----
const modalRoot = document.getElementById("modal-root");
const modalTitle = document.getElementById("modal-title");
const modalBody = document.getElementById("modal-body");

function openModal(title, html) {
  // Marker and cluster handlers stopPropagation, so their clicks never reach
  // the document-level close below; without this the search list stays open
  // behind the modal (z-index 12 vs 20) and is still there when it closes.
  closeSuggestions();
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
  const parts = [servingLabel(b.serving), sourceLabel(b.source), b.last_seen]
    .filter(Boolean).map(esc);
  const cls = (b.source === "manual" || b.source === "community") ? "badge manual" : "badge";
  const tank = b.serving === "tank";
  const product = (b.beer && b.beer !== b.brand)
    ? ` <span class="beer-product">${esc(b.beer)}</span>` : "";
  return `<li class="beer">
    <span class="beer-name">${esc(b.brand)}${product}<span class="${cls}">${parts.join(" · ")}</span></span>
    <form class="beerform" data-osm="${esc(osm)}" data-brand="${esc(b.brand)}" data-beer="${esc(b.beer || "")}">
      <select name="serving" aria-label="${esc(t("a11y.serving"))}">
        <option value="fass"${tank ? "" : " selected"}>${esc(t("beer.fass"))}</option>
        <option value="tank"${tank ? " selected" : ""}>${esc(t("beer.tank"))}</option>
      </select>
      <button name="act" value="edit" title="${esc(t("beer.correct"))}">✓</button>
      <button name="act" value="remove" class="danger" title="${esc(t("beer.remove"))}">✕</button>
      <span class="msg"></span>
    </form>
  </li>`;
}

// Opening hours come from the OSM `opening_hours` tag. Anything the parser
// can't read is printed verbatim rather than guessed at — see web/hours.js.
function hoursBlock(v) {
  if (!v.opening_hours) return "";
  const schedule = venueSchedule(v);
  if (!schedule)
    return `<div class="venue-hours"><span class="hours-raw">🕒 ${esc(v.opening_hours)}</span></div>`;
  const state = openState(schedule);
  const week = formatWeek(schedule, getLang()).map((g) =>
    `<div class="hours-row"><span>${esc(g.label)}</span><span>${esc(g.text)}</span></div>`).join("");
  return `<div class="venue-hours">
      <span class="hours-badge ${state.open ? "open" : "closed"}">${esc(statusText(state, getLang()))}</span>
      <details class="hours-week">
        <summary>${esc(t("hours.title"))}</summary>
        ${week}
        <div class="hours-note">${esc(t("hours.note"))}</div>
      </details>
    </div>`;
}

function openVenueModal(v) {
  const osm = v.osm_id || "";
  const brands = v.brands || [];
  const beers = brands.length
    ? `<ul class="beers">${brands.map((b) => beerRow(osm, b)).join("")}</ul>`
    : `<p class="nobeers">${esc(t("venue.noBeers"))}</p>`;
  const html =
    (v.address ? `<div class="venue-addr">${esc(v.address)}</div>` : "") +
    hoursBlock(v) +
    beers +
    `<form class="addbeer" data-osm="${esc(osm)}">
       <span class="combo">
         <input name="brand" data-combo placeholder="${esc(t("venue.addBrand"))}" autocomplete="off" required>
         <div class="combo-list" hidden></div>
       </span>
       <input name="beer" placeholder="${esc(t("venue.beerOptional"))}">
       <label><input type="radio" name="serving" value="fass" checked>${esc(t("beer.fass"))}</label>
       <label><input type="radio" name="serving" value="tank">${esc(t("beer.tank"))}</label>
       <input class="hp" name="hp" tabindex="-1" autocomplete="off">
       <button>${esc(t("venue.submitBeer"))}</button><span class="msg"></span>
     </form>
     <details class="venue-actions">
       <summary>${esc(t("venue.fix"))}</summary>
       <form class="venueform" data-osm="${esc(osm)}">
         <input name="address" placeholder="${esc(t("venue.address"))}" value="${esc(v.address || "")}">
         <button name="act" value="edit_venue">${esc(t("venue.saveAddress"))}</button>
         <button name="act" value="close_venue" class="danger">${esc(t("venue.reportClosed"))}</button>
         <span class="msg"></span>
       </form>
     </details>`;
  openModal(v.name || t("venue.fallbackName"), html);
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
    </div>`).join("") || `<p class="nobeers">${esc(t("stats.empty"))}</p>`;
  openModal(t("modal.stats"), html);
}

function openAbout() {
  openModal(t("modal.about"), `<div class="modal-text"><p>${esc(t("about.body"))}</p></div>`);
}

function openContact() {
  openModal(t("modal.contact"), `<div class="modal-text">
    <p>${esc(t("contact.intro"))} <a href="mailto:beermap@jakubwaller.eu">beermap@jakubwaller.eu</a></p>
    <p>${esc(t("contact.legal"))}: <a href="impressum.html">${esc(t("contact.imprint"))}</a> · <a href="datenschutz.html">${esc(t("contact.privacy"))}</a></p>
    <p>${esc(t("kofi.free"))} <a href="https://ko-fi.com/jakubwaller" target="_blank" rel="noopener">${esc(t("kofi.cta"))}</a></p>
  </div>`);
}

function openAddInfo() {
  openModal(t("modal.add"), `<div class="modal-text">
      <p>${esc(t("add.p1"))}</p>
      <p><strong>${esc(t("add.p2strong"))}</strong> ${esc(t("add.p2rest"))}</p>
    </div>
    <form class="venueadd">
      <input name="venue" placeholder="${esc(t("add.venueName"))}" maxlength="120" required>
      <input name="address" placeholder="${esc(t("add.addressPh"))}" maxlength="200" required>
      <span class="combo">
        <input name="brand" data-combo placeholder="${esc(t("add.brandOptional"))}" maxlength="80" autocomplete="off">
        <div class="combo-list" hidden></div>
      </span>
      <label><input type="radio" name="serving" value="fass" checked>${esc(t("beer.fass"))}</label>
      <label><input type="radio" name="serving" value="tank">${esc(t("beer.tank"))}</label>
      <input class="hp" name="hp" tabindex="-1" autocomplete="off">
      <button>${esc(t("add.submitVenue"))}</button><span class="msg"></span>
    </form>`);
}

document.querySelectorAll("[data-modal]").forEach((el) => {
  const kind = el.getAttribute("data-modal");
  el.addEventListener("click", () => ({ stats: openStats, about: openAbout, contact: openContact }[kind])());
});
document.getElementById("cta-add").addEventListener("click", openAddInfo);

// ---- Search ----
// Filtering the markers was never enough on its own: off-screen markers are
// culled, so a hit in Leipzig while you are looking at Hamburg counted towards
// "12 Treffer" and then appeared nowhere. The dropdown makes every match
// reachable — pick one to fly to it, or put the whole result set on the map.
const MAX_SUGGESTIONS = 8;
let searchPool = [];   // every loaded venue, gray dots and remote hits included
let matches = [];      // current ranked matches
let suggestIdx = -1;   // keyboard-highlighted row, -1 = none
const knownIds = new Set();   // osm_ids of everything in allVenues + grayVenues
const remoteHits = new Map(); // osm_id -> venue known only via /api/search

function rebuildSearchPool() {
  searchPool = allVenues.concat(grayVenues, [...remoteHits.values()]);
}

// ---- Remote search (nationwide) ----
// The local pool only holds the branded venues plus whatever gray tiles the
// visitor has panned over; /api/search covers the entire database. Hits merge
// into the pool, so the dropdown, Enter and "show all" treat them like any
// other venue — and flying to one loads its area's gray tiles around it.
const SEARCH_DEBOUNCE_MS = 250;
let remoteTimer = null;
let remoteSeq = 0;

function scheduleRemoteSearch() {
  clearTimeout(remoteTimer);
  const q = search.trim();
  if (fold(q).length < 2) return;
  remoteTimer = setTimeout(() => remoteSearch(q), SEARCH_DEBOUNCE_MS);
}

async function remoteSearch(q) {
  const seq = ++remoteSeq;
  let fc;
  try {
    const r = await fetch("/api/search?q=" + encodeURIComponent(q));
    if (!r.ok) return;
    fc = await r.json();
  } catch { return; }   // offline/error: local results stand
  if (seq !== remoteSeq || search.trim() !== q) return;  // stale answer
  let added = false;
  for (const v of loadVenues(fc)) {
    if (!v.osm_id || knownIds.has(v.osm_id) || remoteHits.has(v.osm_id)) continue;
    remoteHits.set(v.osm_id, v);
    added = true;
  }
  if (!added) return;
  rebuildSearchPool();
  // Only refresh a list that is still open. renderSuggestions() opens it
  // unconditionally, so a late answer used to re-open one the user had already
  // dismissed: picking a venue inside the debounce+fetch window left the
  // dropdown sitting behind the modal (z-index 12 vs 20), still there when the
  // modal closed. The pool is rebuilt either way, so the hits are not lost.
  renderSuggestions({ open: !resultsEl.hidden });
}

function suggestRow(v, i) {
  const state = openState(venueSchedule(v));
  const bits = [];
  if (state)
    bits.push(`<span class="${state.open ? "is-open" : "is-closed"}">`
      + esc(state.open ? t("suggest.open") : t("suggest.closed")) + `</span>`);
  const brands = [...new Set((v.brands || []).map((b) => b.brand))];
  if (brands.length)
    bits.push(esc(brands.slice(0, 2).join(", ")
      + (brands.length > 2 ? ` +${brands.length - 2}` : "")));
  if (v.address) bits.push(esc(v.address));
  return `<button type="button" class="suggest-row" role="option" data-idx="${i}">
      <span class="suggest-name">${esc(v.name || t("suggest.unnamed"))}</span>
      <span class="suggest-meta">${bits.join(" · ")}</span>
    </button>`;
}

function openSuggestions() {
  resultsEl.hidden = false;
  searchEl.setAttribute("aria-expanded", "true");
}

function closeSuggestions() {
  resultsEl.hidden = true;
  searchEl.setAttribute("aria-expanded", "false");
  suggestIdx = -1;
}

// `open: false` refreshes `matches` without showing or re-showing the list —
// Enter still fits bounds over the full set even though the dropdown is closed.
function renderSuggestions({ open = true } = {}) {
  const q = search.trim();
  clearEl.hidden = !q;
  if (!q) { matches = []; closeSuggestions(); return; }
  matches = searchVenues(searchPool, q);
  suggestIdx = -1;
  if (!open) return;
  resultsEl.innerHTML = matches.length
    ? matches.slice(0, MAX_SUGGESTIONS).map(suggestRow).join("")
      + `<button type="button" class="suggest-all">`
      + esc(tn("suggest.showAll", matches.length)) + `</button>`
    : `<div class="suggest-empty">${esc(t("suggest.none", { q }))}</div>`;
  openSuggestions();
}

function pickVenue(v) {
  if (!v) return;
  closeSuggestions();
  searchEl.blur();
  map.flyTo({ center: [v.lon, v.lat], zoom: Math.max(map.getZoom(), 16) });
  openVenueModal(v);
}

function showAllMatches() {
  if (!matches.length) return;
  if (matches.length === 1) { pickVenue(matches[0]); return; }
  closeSuggestions();
  searchEl.blur();
  const bounds = new maplibregl.LngLatBounds();
  for (const v of matches) bounds.extend([v.lon, v.lat]);
  map.fitBounds(bounds, { padding: dePadding(), maxZoom: 15 });
}

function highlightSuggestion(delta) {
  const rows = resultsEl.querySelectorAll(".suggest-row");
  if (!rows.length) return;
  suggestIdx += delta;
  if (suggestIdx < 0) suggestIdx = rows.length - 1;
  if (suggestIdx >= rows.length) suggestIdx = 0;
  rows.forEach((r, i) => r.classList.toggle("active", i === suggestIdx));
  rows[suggestIdx].scrollIntoView({ block: "nearest" });
}

function clearSearch() {
  searchEl.value = "";
  search = "";
  matches = [];
  clearEl.hidden = true;
  closeSuggestions();
  applyFilters();
}

searchEl.addEventListener("input", () => {
  search = searchEl.value;
  renderSuggestions();
  scheduleRemoteSearch();
  applyFilters();
});
searchEl.addEventListener("focus", () => { if (search.trim()) renderSuggestions(); });
searchEl.addEventListener("keydown", (e) => {
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    if (resultsEl.hidden) renderSuggestions();
    else highlightSuggestion(e.key === "ArrowDown" ? 1 : -1);
  } else if (e.key === "Enter") {
    e.preventDefault();
    if (suggestIdx >= 0) pickVenue(matches[suggestIdx]);
    else showAllMatches();
  } else if (e.key === "Escape") {
    if (resultsEl.hidden) clearSearch(); else closeSuggestions();
  } else if (e.key === "Tab") {
    closeSuggestions();   // else it stays open and aria-expanded="true"
  }
});
// Closed on an outside click rather than on `blur`, for the same reason as the
// brand combo further down this file: the old guard against blur-closing was a
// `preventDefault()` on pointerdown, and that cancels touch panning, so this
// list (up to 8 two-line rows in 58vh) could not be scrolled on a phone.
document.addEventListener("click", (e) => {
  if (!e.target.closest(".search-wrap")) closeSuggestions();
});
// Panning by touch produces no click anywhere, so the document listener above
// never fires for it and the list would sit over the map being dragged.
// `dragstart`/`zoomstart` rather than `movestart`: MapLibre also fires
// `movestart` from resize(), which a ResizeObserver drives, so a phone rotation
// or the soft keyboard opening would close the list mid-typing.
map.on("dragstart", closeSuggestions);
map.on("zoomstart", closeSuggestions);
resultsEl.addEventListener("click", (e) => {
  if (e.target.closest(".suggest-all")) { showAllMatches(); return; }
  const row = e.target.closest(".suggest-row");
  if (row) pickVenue(matches[+row.dataset.idx]);
});
clearEl.addEventListener("click", () => { clearSearch(); searchEl.focus(); });

// ---- Brand autocomplete (modal forms) ----
// Hand-rolled instead of <input list> + <datalist>: mobile Safari renders the
// native datalist dropdown erratically — on a phone the brand field offered no
// completions at all — and a tappable list beats a keyboard-only one anyway.
const COMBO_MAX = 6;
let brandNames = [];

function comboMatches(query) {
  const q = fold(query);
  if (!q) return brandNames.slice(0, COMBO_MAX);
  const starts = [], contains = [];
  for (const name of brandNames) {
    const f = fold(name);
    if (f.startsWith(q)) starts.push(name);
    else if (f.includes(q)) contains.push(name);
  }
  return starts.concat(contains).slice(0, COMBO_MAX);
}

const comboList = (input) => input.parentElement.querySelector(".combo-list");

function closeCombo(input) {
  const list = comboList(input);
  if (list) { list.hidden = true; list.textContent = ""; }
}

function renderCombo(input) {
  const list = comboList(input);
  if (!list) return;
  const items = comboMatches(input.value);
  // Nothing to add once the field already holds the one remaining match.
  if (!items.length || (items.length === 1 && fold(items[0]) === fold(input.value))) {
    closeCombo(input);
    return;
  }
  list.innerHTML = items.map((n) =>
    `<button type="button" class="combo-opt" data-value="${esc(n)}">${esc(n)}</button>`).join("");
  list.hidden = false;
}

modalBody.addEventListener("input", (e) => {
  if (e.target.matches("input[data-combo]")) renderCombo(e.target);
});
modalBody.addEventListener("focusin", (e) => {
  if (e.target.matches("input[data-combo]")) renderCombo(e.target);
});
// Both of these are deliberately bound to `click` rather than to `focusout` or
// `pointerdown`, and that is load-bearing on a phone:
//
//   * the list sits in the flow (see style.css), so closing it on `focusout`
//     reflowed the form mid-tap and pulled "Bier melden" up from under the
//     finger — pointerdown and pointerup landed on different elements and the
//     click was never delivered, which is why the button needed two taps;
//   * `preventDefault()` on pointerdown (which used to hold the focus so the
//     blur-close wouldn't beat the pick) also cancels the browser's pan
//     gesture, so the list could not be scrolled by touch at all.
//
// Closing on `click` happens after the tap it interrupted has been delivered,
// which costs nothing and makes both gestures work.
modalBody.addEventListener("click", (e) => {
  const opt = e.target.closest(".combo-opt");
  if (!opt) return;
  const input = opt.closest(".combo").querySelector("input");
  input.value = opt.dataset.value;
  // Focus first, close second. Blink and Gecko move focus to a <button> on
  // mousedown, so by click time the field has blurred and this focus() re-fires
  // `focusin` -> renderCombo, which re-opens the list for every brand that is a
  // strict prefix of another ("Augustiner" pulls back its five variants). The
  // old pointerdown preventDefault used to mask this by never letting the field
  // blur; closing after the focus does it without blocking touch scroll.
  input.focus();
  closeCombo(input);
});
document.addEventListener("click", (e) => {
  if (e.target.closest(".combo")) return;
  modalBody.querySelectorAll("input[data-combo]").forEach((i) => closeCombo(i));
});
modalBody.addEventListener("keydown", (e) => {
  const input = e.target;
  if (!input.matches || !input.matches("input[data-combo]")) return;
  const list = comboList(input);
  if (!list || list.hidden) return;
  const opts = [...list.querySelectorAll(".combo-opt")];
  const cur = opts.findIndex((o) => o.classList.contains("active"));
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    const next = e.key === "ArrowDown"
      ? (cur + 1) % opts.length
      : (cur <= 0 ? opts.length - 1 : cur - 1);
    opts.forEach((o, i) => o.classList.toggle("active", i === next));
  } else if (e.key === "Enter" && cur >= 0) {
    e.preventDefault();   // pick the highlighted brand instead of submitting
    input.value = opts[cur].dataset.value;
    closeCombo(input);
  } else if (e.key === "Escape") {
    e.stopPropagation();   // else the document-level Escape closes the modal too
    closeCombo(input);
  } else if (e.key === "Tab") {
    closeCombo(input);
  }
});

// ---- Language ----
// Static chrome is translated in place via data-i18n attributes; everything
// rendered by JS goes through t()/tn() at render time, so a language switch
// just re-renders the visible pieces. Open popups (modal, suggestions) are
// closed rather than re-rendered — they rebuild translated on next open.
function applyStaticI18n() {
  document.documentElement.lang = getLang();
  document.title = t("title");
  document.querySelectorAll("[data-i18n]").forEach((el) => {
    el.textContent = t(el.dataset.i18n);
  });
  document.querySelectorAll("[data-i18n-aria]").forEach((el) => {
    el.setAttribute("aria-label", t(el.dataset.i18nAria));
  });
  document.querySelectorAll("[data-i18n-title]").forEach((el) => {
    el.title = t(el.dataset.i18nTitle);
  });
  document.querySelectorAll("[data-i18n-placeholder]").forEach((el) => {
    el.placeholder = t(el.dataset.i18nPlaceholder);
  });
  document.querySelectorAll("[data-i18n-label]").forEach((el) => {
    el.label = t(el.dataset.i18nLabel);
  });
}

const langSelect = document.getElementById("lang-select");
langSelect.value = getLang();
langSelect.addEventListener("change", () => {
  setLang(langSelect.value);
  // Keep the address bar shareable: copying the URL reproduces the language
  // the visitor is looking at.
  try {
    const u = new URL(location.href);
    u.searchParams.set("lang", langSelect.value);
    history.replaceState(null, "", u);
  } catch { /* the switcher itself still works */ }
  applyStaticI18n();
  closeModal();
  closeSuggestions();
  renderServingChips();
  renderBrandChips();
  applyFilters();
});
applyStaticI18n();

const citySelect = document.getElementById("city-select");
citySelect.addEventListener("change", () => {
  const view = CITY_VIEWS[citySelect.value];
  if (view) map.flyTo(view);
  else map.fitBounds(MAP_BOUNDS, { padding: dePadding() });
});

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
  if (form.classList.contains("venueadd"))
    return { kind: "add_venue", venue_osm_id: "", name: form.venue.value.trim(),
             address: form.address.value.trim(), brand: form.brand.value.trim(),
             serving: form.serving.value, hp: form.hp.value };
  return null;
}

modalBody.addEventListener("submit", async (ev) => {
  const f = ev.target;
  if (!f.matches(".addbeer, .beerform, .venueform, .venueadd")) return;
  ev.preventDefault();
  const action = ev.submitter && ev.submitter.value;
  if (action === "close_venue" && !confirm(t("confirm.close"))) return;
  const body = submissionBody(f, action);
  if (!body) return;
  const msg = f.querySelector(".msg");
  const r = await fetch("/api/submit", { method: "POST",
    headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (msg) msg.textContent = r.ok ? t("form.thanks") : t("form.error");
  if (r.ok) f.querySelectorAll("button, input, select").forEach((el) => (el.disabled = true));
});

// ---- Boot ----
// The filter chrome (chips, search, count) is deliberately decoupled from the
// map's WebGL "load" event: the UI stays usable even while the map is still
// warming up. Markers are (re)built once BOTH the venue data and the map style
// are ready — refreshMarkers() no-ops until then.
let dataReady = false, styleReady = false;

// Re-cluster after every pan/zoom (grid buckets are in screen space); the
// gray layer re-gates on zoom and fetches any tiles the view newly covers.
map.on("moveend", () => { refreshMarkers(); updateGrayLayer(); });
map.on("load", () => {
  styleReady = true;
  addGrayLayer();
  refreshMarkers();
  refreshGrayLayer();
});

async function boot() {
  const fc = await (await fetch("data/venues.json")).json();
  const venues = loadVenues(fc);
  allVenues = venues.filter((v) => v.brands.length > 0);
  // Normally empty — the export is branded-only, gray venues come per
  // viewport from /api/gray. Kept as a filter so an old pre-split
  // venues.json (stale data volume right after a code deploy) still renders
  // its gray dots instead of dropping them.
  setGrayVenues(venues.filter((v) => v.brands.length === 0));
  for (const v of venues) if (v.osm_id) knownIds.add(v.osm_id);
  // The search dropdown reaches every loaded venue, chips or no chips:
  // someone typing a pub name wants that pub, not "no results, because
  // Tankbier" — and /api/search extends the reach to the whole country.
  rebuildSearchPool();

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

  // "Marke hinzufügen" autocomplete, commonest brand first — alphabetical order
  // opens the list on "60" and "7up", which reads like a broken suggestion box.
  brandNames = buildBrandList(allVenues)
    .sort((a, b) => (freq.get(b) || 0) - (freq.get(a) || 0) || a.localeCompare(b, "de"));

  renderServingChips();
  renderBrandChips();
  positionZoomCtrl();
  dataReady = true;
  applyFilters();          // updates the count and plots markers (if the map is ready)
}

function setGrayVenues(venues) {
  grayVenues = venues;
  // The gray map layer round-trips venues through feature properties by index.
  grayVenues.forEach((v, i) => { v.grayIdx = i; });
}

boot();
window.addEventListener("resize", positionZoomCtrl);

import { loadVenues, buildBrandList, venuesByBrand, venuesByServing } from "./datasource.js";

const SERVING_LABEL = { tank: "Tankbier", fass: "Fassbier", unknown: "" };
const SOURCE_LABEL = { manual: "✓ verifiziert", osm: "OSM" };

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

function toFC(venues) {
  return { type: "FeatureCollection", features: venues.map((v) => ({
    type: "Feature", geometry: { type: "Point", coordinates: [v.lon, v.lat] },
    properties: { html: `<strong>${esc(v.name)}</strong><br>${esc(v.address || "")}<br>` + v.brands.map((b) => {
      const parts = [SERVING_LABEL[b.serving], SOURCE_LABEL[b.source] || b.source, b.last_seen]
        .filter(Boolean).map(esc);
      const cls = b.source === "manual" ? "badge manual" : "badge";
      return `${esc(b.brand)}<span class="${cls}">${parts.join(" · ")}</span>`;
    }).join("<br>") } })) };
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
  map.addSource("venues", { type: "geojson", data: toFC(allVenues) });
  map.addLayer({ id: "dots", type: "circle", source: "venues",
    paint: { "circle-radius": 6, "circle-color": "#c8102e", "circle-stroke-width": 1, "circle-stroke-color": "#fff" } });
  render(allVenues);
  map.on("click", "dots", (e) => new maplibregl.Popup().setLngLat(e.lngLat).setHTML(e.features[0].properties.html).addTo(map));
  map.on("mouseenter", "dots", () => (map.getCanvas().style.cursor = "pointer"));
  map.on("mouseleave", "dots", () => (map.getCanvas().style.cursor = ""));
  brandSelect.addEventListener("change", applyFilters);
  servingSelect.addEventListener("change", applyFilters);
});

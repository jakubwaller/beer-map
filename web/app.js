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

function toFC(venues) {
  return { type: "FeatureCollection", features: venues.map((v) => ({
    type: "Feature", geometry: { type: "Point", coordinates: [v.lon, v.lat] },
    properties: { osm_id: v.osm_id || "", html:
      `<strong>${esc(v.name)}</strong><br>${esc(v.address || "")}<br>` +
      v.brands.map((b) => {
        const parts = [SERVING_LABEL[b.serving], SOURCE_LABEL[b.source] || b.source, b.last_seen]
          .filter(Boolean).map(esc);
        const cls = (b.source === "manual" || b.source === "community") ? "badge manual" : "badge";
        return `${esc(b.brand)}<span class="${cls}">${parts.join(" · ")}</span>`;
      }).join("<br>") +
      `<form class="addbeer" data-osm="${esc(v.osm_id || "")}">
         <input name="brand" list="brandlist" placeholder="Marke" required>
         <label><input type="radio" name="serving" value="fass" checked>Fass</label>
         <label><input type="radio" name="serving" value="tank">Tank</label>
         <input class="hp" name="hp" tabindex="-1" autocomplete="off">
         <button>+ Bier melden</button><span class="msg"></span>
       </form>` } })) };
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
  map.on("click", "dots", (e) => new maplibregl.Popup().setLngLat(e.lngLat).setHTML(e.features[0].properties.html).addTo(map));
  map.on("mouseenter", "dots", () => (map.getCanvas().style.cursor = "pointer"));
  map.on("mouseleave", "dots", () => (map.getCanvas().style.cursor = ""));
  brandSelect.addEventListener("change", applyFilters);
  servingSelect.addEventListener("change", applyFilters);

  document.getElementById("map").addEventListener("submit", async (ev) => {
    if (!ev.target.classList.contains("addbeer")) return;
    ev.preventDefault();
    const f = ev.target, msg = f.querySelector(".msg");
    const body = { venue_osm_id: f.dataset.osm, brand: f.brand.value.trim(),
                   serving: f.serving.value, kind: "add", hp: f.hp.value };
    const r = await fetch("/api/submit", { method: "POST",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    msg.textContent = r.ok ? " Danke, wird geprüft!" : " Fehler";
    if (r.ok) f.querySelector("button").disabled = true;
  });
});

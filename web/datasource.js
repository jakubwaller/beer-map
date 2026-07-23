function rank(source) {
  if (source === "manual") return 0;
  if (source === "community") return 1;
  if (source.startsWith("finder:")) return 2;
  return 3;
}

// A venue can list several beers of one brand (e.g. Augustiner Edelstoff + Hell),
// and each beer can have edges from several sources. Show each distinct
// brand+beer once, keeping the highest-trust edge; merge a known serving from a
// dropped duplicate. A brand-only entry (no specific beer) is dropped when that
// brand also has specific beers, since those already imply the brand. Assumes
// `brands` is sorted best-trust-first.
function dedupeBrands(brands) {
  const byBrand = new Map();  // brand -> { specific: Map(beer -> entry), generic: entry|null }
  for (const b of brands) {
    let g = byBrand.get(b.brand);
    if (!g) { g = { specific: new Map(), generic: null }; byBrand.set(b.brand, g); }
    const merge = (kept) => {
      if (kept.serving === "unknown" && b.serving && b.serving !== "unknown")
        kept.serving = b.serving;
    };
    if (b.beer) {
      const kept = g.specific.get(b.beer);
      if (kept) merge(kept); else g.specific.set(b.beer, { ...b });
    } else if (g.generic) merge(g.generic);
    else g.generic = { ...b };
  }
  const out = [];
  for (const g of byBrand.values()) {
    if (g.specific.size) out.push(...g.specific.values());  // specific beers win
    else if (g.generic) out.push(g.generic);
  }
  return out;
}

// "draught" means served on tap — Fass or Tank (vs the specific fass/tank/unknown).
const DRAUGHT = new Set(["fass", "tank"]);
const servingMatch = (b, serving) =>
  serving === "draught" ? DRAUGHT.has(b.serving) : b.serving === serving;

export function loadVenues(fc) {
  return (fc.features || []).map((f) => ({
    name: f.properties.name,
    lat: f.geometry.coordinates[1],
    lon: f.geometry.coordinates[0],
    address: f.properties.address,
    website: f.properties.website,
    osm_id: f.properties.osm_id ?? "",
    brands: dedupeBrands([...(f.properties.brands || [])].sort(
      (a, b) => rank(a.source) - rank(b.source) || a.brand.localeCompare(b.brand, "de"))),
  }));
}

export function buildBrandList(venues) {
  const set = new Set();
  for (const v of venues) for (const b of v.brands) set.add(b.brand);
  return [...set].sort((a, b) => a.localeCompare(b, "de"));
}

export function venuesByBrand(venues, brand, serving = null) {
  return venues.filter((v) =>
    v.brands.some((b) => b.brand === brand && (!serving || servingMatch(b, serving))));
}

export function venuesByServing(venues, serving) {
  return venues.filter((v) => v.brands.some((b) => servingMatch(b, serving)));
}

export function searchVenues(venues, query) {
  if (!query) return venues;
  const q = query.toLowerCase();
  return venues.filter((v) =>
    (v.name || "").toLowerCase().includes(q) ||
    (v.address || "").toLowerCase().includes(q) ||
    v.brands.some((b) => b.brand.toLowerCase().includes(q)));
}

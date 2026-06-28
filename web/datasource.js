function rank(source) {
  if (source === "manual") return 0;
  if (source === "community") return 1;
  if (source.startsWith("finder:")) return 2;
  return 3;
}

// The same brand can have edges from several sources (e.g. an OSM listing plus a
// community serving correction). Show each brand once, keeping the highest-trust
// entry — but let it adopt a known serving from a dropped duplicate so the serving
// filter still matches. Assumes `brands` is already sorted best-trust-first.
function dedupeBrands(brands) {
  const byName = new Map();
  for (const b of brands) {
    const kept = byName.get(b.brand);
    if (!kept) { byName.set(b.brand, { ...b }); continue; }
    if (kept.serving === "unknown" && b.serving && b.serving !== "unknown")
      kept.serving = b.serving;
    if (!kept.beer && b.beer) kept.beer = b.beer;  // adopt a specific beer from a dup
  }
  return [...byName.values()];
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

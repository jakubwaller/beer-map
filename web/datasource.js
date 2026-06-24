function rank(source) {
  if (source === "manual") return 0;
  if (source.startsWith("finder:")) return 1;
  return 2;
}

export function loadVenues(fc) {
  return (fc.features || []).map((f) => ({
    name: f.properties.name,
    lat: f.geometry.coordinates[1],
    lon: f.geometry.coordinates[0],
    address: f.properties.address,
    website: f.properties.website,
    brands: [...(f.properties.brands || [])].sort(
      (a, b) => rank(a.source) - rank(b.source) || a.brand.localeCompare(b.brand, "de")),
  }));
}

export function buildBrandList(venues) {
  const set = new Set();
  for (const v of venues) for (const b of v.brands) set.add(b.brand);
  return [...set].sort((a, b) => a.localeCompare(b, "de"));
}

export function venuesByBrand(venues, brand, serving = null) {
  return venues.filter((v) =>
    v.brands.some((b) => b.brand === brand && (!serving || b.serving === serving)));
}

export function venuesByServing(venues, serving) {
  return venues.filter((v) => v.brands.some((b) => b.serving === serving));
}

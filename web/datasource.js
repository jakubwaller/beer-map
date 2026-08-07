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

// "draught" means served on tap. Any brand edge counts: an OSM `brewery=` tag
// or a curated link means the venue pours that brand even when fass-vs-tank is
// unverified (serving=unknown) — only the explicit fass/tank filters are strict.
const servingMatch = (b, serving) =>
  serving === "draught" ? true : b.serving === serving;

export function loadVenues(fc) {
  return (fc.features || []).map((f) => ({
    name: f.properties.name,
    lat: f.geometry.coordinates[1],
    lon: f.geometry.coordinates[0],
    address: f.properties.address,
    website: f.properties.website,
    opening_hours: f.properties.opening_hours ?? null,
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

// Brands that always get a filter chip when they exist in the data, even when
// bigger brands crowd them out of the top slots by venue count — the map was
// built to answer "where pours Pilsner Urquell" (see docs/specs), yet it sits
// around rank 36 by raw frequency.
export const PINNED_BRANDS = ["Pilsner Urquell"];

/** Top `n` entries of `freq` ([brand, count] pairs, sorted desc), with pinned
 *  brands replacing the tail entries when they would otherwise miss the cut. */
export function topBrands(freq, n, pinned = PINNED_BRANDS) {
  const top = freq.slice(0, n);
  const missing = freq.filter(([name]) =>
    pinned.includes(name) && !top.some(([t]) => t === name));
  if (missing.length)
    top.splice(n - missing.length, missing.length, ...missing);
  return top;
}

export function venuesByBrand(venues, brand, serving = null) {
  return venues.filter((v) =>
    v.brands.some((b) => b.brand === brand && (!serving || servingMatch(b, serving))));
}

export function venuesByServing(venues, serving) {
  return venues.filter((v) => v.brands.some((b) => servingMatch(b, serving)));
}

// ---- Search ----
// Everything is compared in a folded form: lowercased, ß -> ss, diacritics
// stripped (so "Kuche" finds "Küche" and "Cafe" finds "Café"), and every run of
// punctuation collapsed to one space, which makes "St.Pauli", "St. Pauli" and
// "st pauli" the same string.
export function fold(s) {
  return String(s ?? "")
    .toLowerCase()
    .replace(/ß/g, "ss")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")   // combining marks left by NFD
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

// Folded fields are memoized per venue object — the venue list is built once at
// boot but re-searched on every keystroke, and folding ~5000 venues each time
// is what would make typing feel heavy on a phone.
const foldCache = new WeakMap();

function folded(v) {
  let f = foldCache.get(v);
  if (!f) {
    const brands = (v.brands || []).map((b) => fold(b.brand));
    const beers = (v.brands || []).map((b) => fold(b.beer)).filter(Boolean);
    const name = fold(v.name);
    const address = fold(v.address);
    f = { name, address, brands, beers,
          all: [name, address, ...brands, ...beers].join(" "),
          // Everything a query may match mid-word (see tokenHit) — the address
          // deliberately stays out.
          loose: [name, ...brands, ...beers].join(" ") };
    foldCache.set(v, f);
  }
  return f;
}

const startsWord = (hay, needle) => (" " + hay).includes(" " + needle);

// How well one field answers the query. A whole-string hit beats a prefix hit
// beats a word-start hit beats a substring; the token rules below catch
// "goldener handwerk" for "Zum Goldenen Handwerk", which plain substring
// matching (the old behaviour) missed entirely. `wordOnly` fields (addresses)
// skip the mid-word rules — see tokenHit.
function fieldScore(hay, tokens, full, wordOnly = false) {
  if (!hay) return 0;
  if (hay === full) return 100;
  if (hay.startsWith(full)) return 80;
  if (startsWord(hay, full)) return 65;
  if (!wordOnly && hay.includes(full)) return 50;
  if (tokens.length > 1 && tokens.every((t) => startsWord(hay, t))) return 45;
  if (!wordOnly && tokens.length > 1 && tokens.every((t) => hay.includes(t))) return 35;
  return 0;
}

// A token counts when it starts a word anywhere, or sits mid-word in the name,
// a brand or a beer. Addresses are word-start only on purpose: German street
// names swallow half the brand list ("Astra" lives inside "Koreastraße",
// "Amandastraße", …), which buried the real hits under dozens of gray dots.
const tokenHit = (f, t) => startsWord(f.all, t) || f.loose.includes(t);

/** Relevance of one venue for `query`; 0 means "no match". */
export function scoreVenue(v, query) {
  const full = fold(query);
  if (!full) return 0;
  const tokens = full.split(" ");
  const f = folded(v);
  // Gate first: every token has to hit, so "astra altona" needs both, even when
  // they land in different fields.
  if (!tokens.every((t) => tokenHit(f, t))) return 0;
  const best = (fields) => fields.reduce((m, h) => Math.max(m, fieldScore(h, tokens, full)), 0);
  const score = Math.max(
    fieldScore(f.name, tokens, full),
    best(f.brands) * 0.7,
    best(f.beers) * 0.6,
    fieldScore(f.address, tokens, full, true) * 0.45,
    1,  // matched across fields only — still a hit, just the weakest kind
  );
  // Tiny nudge so a venue with actual beer data outranks a bare gray dot.
  return score + ((v.brands && v.brands.length) ? 1 : 0);
}

/** Venues matching `query`, best match first (input order when there is no
 *  query). The map filter only needs the membership; the search dropdown needs
 *  the order. */
export function searchVenues(venues, query) {
  if (!fold(query)) return venues;
  return venues
    .map((v) => ({ v, s: scoreVenue(v, query) }))
    .filter((h) => h.s > 0)
    .sort((a, b) => b.s - a.s || (a.v.name || "").localeCompare(b.v.name || "", "de"))
    .map((h) => h.v);
}

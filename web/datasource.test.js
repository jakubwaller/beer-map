import { test } from "node:test";
import assert from "node:assert/strict";
import { loadVenues, buildBrandList, venuesByBrand, venuesByServing, searchVenues, scoreVenue, fold, topBrands } from "./datasource.js";

const FC = {
  type: "FeatureCollection",
  features: [
    { type: "Feature", geometry: { type: "Point", coordinates: [9.99, 53.55] },
      properties: { name: "Bar A", address: "HH", website: null, brands: [
        { brand: "Astra", source: "osm", serving: "unknown", last_seen: "2026-06-24" },
        { brand: "Ratsherrn", source: "manual", serving: "fass", last_seen: "2026-06-24" },
        { brand: "Ratsherrn", source: "community", serving: "fass", last_seen: "2026-06-24" } ] } },
    { type: "Feature", geometry: { type: "Point", coordinates: [10.05, 53.60] },
      properties: { name: "WALD", address: "HH", website: null, brands: [
        { brand: "Budweiser Budvar", source: "manual", serving: "tank", last_seen: "2026-06-24" } ] } },
  ],
};

test("loadVenues flattens and sorts brands by trust (manual first)", () => {
  const v = loadVenues(FC);
  assert.equal(v[0].lat, 53.55);
  assert.equal(v[0].brands[0].source, "manual");  // Ratsherrn before Astra
});

test("loadVenues defaults a missing brands property (gray export omits it)", () => {
  const v = loadVenues({ type: "FeatureCollection", features: [
    { type: "Feature", geometry: { type: "Point", coordinates: [9.99, 53.55] },
      properties: { name: "Gray Pub", address: "HH", website: null } } ] });
  assert.deepEqual(v[0].brands, []);
});

test("buildBrandList is unique + sorted", () => {
  assert.deepEqual(buildBrandList(loadVenues(FC)), ["Astra", "Budweiser Budvar", "Ratsherrn"]);
});

test("venuesByBrand optionally filters by serving", () => {
  const v = loadVenues(FC);
  assert.deepEqual(venuesByBrand(v, "Ratsherrn").map((x) => x.name), ["Bar A"]);
  assert.deepEqual(venuesByBrand(v, "Ratsherrn", "tank").map((x) => x.name), []);
});

test("venuesByServing filters tank vs fass", () => {
  assert.deepEqual(venuesByServing(loadVenues(FC), "tank").map((x) => x.name), ["WALD"]);
});

test("venuesByServing 'draught' matches any brand edge; fass/tank stay strict", () => {
  const at = (lon, lat) => ({ type: "Point", coordinates: [lon, lat] });
  const fc = { type: "FeatureCollection", features: [
    { type: "Feature", geometry: at(9.9, 53.5),
      properties: { name: "UnknownOnly", brands: [{ brand: "X", source: "osm", serving: "unknown" }] } },
    { type: "Feature", geometry: at(9.9, 53.5),
      properties: { name: "FassPub", brands: [{ brand: "Y", source: "osm", serving: "fass" }] } },
    { type: "Feature", geometry: at(9.9, 53.5),
      properties: { name: "NoData", brands: [] } } ] };
  const v = loadVenues(fc);
  assert.deepEqual(venuesByServing(v, "draught").map((x) => x.name).sort(), ["FassPub", "UnknownOnly"]);
  assert.deepEqual(venuesByServing(v, "fass").map((x) => x.name), ["FassPub"]);
});

test("loadVenues carries the specific beer through and adopts it on dedupe", () => {
  const fc = { type: "FeatureCollection", features: [
    { type: "Feature", geometry: { type: "Point", coordinates: [9.9, 53.5] },
      properties: { name: "Bar", brands: [
        { brand: "Ratsherrn", source: "osm", serving: "unknown" },
        { brand: "Ratsherrn", source: "manual", serving: "fass", beer: "Matrosenschluck" } ] } } ] };
  const [b] = loadVenues(fc)[0].brands;
  assert.equal(b.source, "manual");
  assert.equal(b.beer, "Matrosenschluck");
});

test("loadVenues dedupes a brand to its highest-trust entry", () => {
  const v = loadVenues(FC);
  // Bar A lists Ratsherrn from both manual and community — show it once, as manual.
  const ratsherrn = v[0].brands.filter((b) => b.brand === "Ratsherrn");
  assert.equal(ratsherrn.length, 1);
  assert.equal(ratsherrn[0].source, "manual");
  assert.deepEqual(v[0].brands.map((b) => b.brand), ["Ratsherrn", "Astra"]);
});

test("dedupe keeps top provenance but adopts a known serving from a duplicate", () => {
  const fc = { type: "FeatureCollection", features: [
    { type: "Feature", geometry: { type: "Point", coordinates: [9.9, 53.5] },
      properties: { name: "Bar C", brands: [
        { brand: "Astra", source: "manual", serving: "unknown", last_seen: "2026-06-24" },
        { brand: "Astra", source: "osm", serving: "fass", last_seen: "2026-06-24" } ] } } ] };
  const [b] = loadVenues(fc)[0].brands;
  assert.equal(b.source, "manual");   // higher trust wins
  assert.equal(b.serving, "fass");    // serving filled in from the OSM duplicate
});

test("searchVenues matches name, address, or brand, case-insensitively", () => {
  const v = loadVenues(FC);
  assert.deepEqual(searchVenues(v, "bar a").map((x) => x.name), ["Bar A"]);
  assert.deepEqual(searchVenues(v, "budvar").map((x) => x.name), ["WALD"]);
  assert.deepEqual(searchVenues(v, "hh").map((x) => x.name), ["Bar A", "WALD"]);
  assert.deepEqual(searchVenues(v, "xyz"), []);
});

test("searchVenues with an empty query returns everything, brandless venues included", () => {
  const v = loadVenues(FC).concat([{ name: "NoData", address: "", brands: [] }]);
  assert.equal(searchVenues(v, "").length, 3);
  assert.deepEqual(searchVenues(v, "nodata").map((x) => x.name), ["NoData"]);
});

test("fold strips diacritics, ß and punctuation", () => {
  assert.equal(fold("Café Größe – St.Pauli!"), "cafe grosse st pauli");
  assert.equal(fold("  "), "");
});

test("searchVenues ignores umlauts, case and punctuation", () => {
  const v = loadVenues({ type: "FeatureCollection", features: [
    { type: "Feature", geometry: { type: "Point", coordinates: [9.9, 53.5] },
      properties: { name: "Zur Schönen Aussicht", address: "St. Pauli", brands: [] } } ] });
  assert.equal(searchVenues(v, "schonen").length, 1);
  assert.equal(searchVenues(v, "SCHÖNEN").length, 1);
  assert.equal(searchVenues(v, "st.pauli").length, 1);
});

test("searchVenues matches all tokens, in any order and across fields", () => {
  const v = loadVenues({ type: "FeatureCollection", features: [
    { type: "Feature", geometry: { type: "Point", coordinates: [9.9, 53.5] },
      properties: { name: "Zum Goldenen Handwerk", address: "Altona", brands: [
        { brand: "Astra", source: "osm", serving: "fass" } ] } } ] });
  assert.equal(searchVenues(v, "goldenen handwerk").length, 1);  // word tokens, gap skipped
  assert.equal(searchVenues(v, "handwerk zum").length, 1);        // order irrelevant
  assert.equal(searchVenues(v, "astra altona").length, 1);        // brand + address
  assert.equal(searchVenues(v, "astra billstedt").length, 0);     // one token missing
});

test("searchVenues ranks name hits over brand hits over address hits", () => {
  const at = (lon, lat) => ({ type: "Point", coordinates: [lon, lat] });
  const v = loadVenues({ type: "FeatureCollection", features: [
    { type: "Feature", geometry: at(9.9, 53.5),
      properties: { name: "Weinstube", address: "Astraweg 4", brands: [] } },
    { type: "Feature", geometry: at(9.9, 53.5),
      properties: { name: "Eckkneipe", address: "Altona", brands: [
        { brand: "Astra", source: "osm", serving: "fass" } ] } },
    { type: "Feature", geometry: at(9.9, 53.5),
      properties: { name: "Astra Stube", address: "Altona", brands: [] } } ] });
  assert.deepEqual(searchVenues(v, "astra").map((x) => x.name),
                   ["Astra Stube", "Eckkneipe", "Weinstube"]);
});

test("a query does not match mid-word inside an address", () => {
  const v = loadVenues({ type: "FeatureCollection", features: [
    // "astra" hides inside "Koreastraße" — a substring match on addresses buried
    // the real Astra pubs under gray dots.
    { type: "Feature", geometry: { type: "Point", coordinates: [9.9, 53.5] },
      properties: { name: "Alte Liebe", address: "Koreastraße 1, 20457 Hamburg", brands: [] } },
    { type: "Feature", geometry: { type: "Point", coordinates: [9.9, 53.5] },
      properties: { name: "Eckkneipe", address: "Astraweg 4", brands: [] } } ] });
  assert.deepEqual(searchVenues(v, "astra").map((x) => x.name), ["Eckkneipe"]);
  // Mid-word still works where it is useful: names, brands and beers.
  const named = loadVenues({ type: "FeatureCollection", features: [
    { type: "Feature", geometry: { type: "Point", coordinates: [9.9, 53.5] },
      properties: { name: "Straßenbräu", address: "", brands: [] } } ] });
  assert.equal(searchVenues(named, "brau").length, 1);
});

test("scoreVenue prefers a venue with beer data over a bare dot", () => {
  const mk = (brands) => ({ name: "Kneipe", address: "", brands });
  assert.ok(scoreVenue(mk([{ brand: "Astra" }]), "kneipe") >
            scoreVenue(mk([]), "kneipe"));
});

test("dedupe keeps multiple beers of one brand and drops the brand-only entry", () => {
  const fc = { type: "FeatureCollection", features: [
    { type: "Feature", geometry: { type: "Point", coordinates: [9.9, 53.5] },
      properties: { name: "Bar", brands: [
        { brand: "Augustiner", source: "osm", serving: "unknown" },               // generic -> dropped
        { brand: "Augustiner", source: "manual", serving: "fass", beer: "Edelstoff" },
        { brand: "Augustiner", source: "manual", serving: "fass", beer: "Hell" } ] } } ] };
  const bs = loadVenues(fc)[0].brands;
  assert.equal(bs.length, 2);
  assert.deepEqual(bs.map((b) => b.beer).sort(), ["Edelstoff", "Hell"]);
});

test("topBrands puts Pilsner Urquell first even when it misses the cut", () => {
  const freq = [["Augustiner", 208], ["Bitburger", 152], ["Krombacher", 138],
                ["Paulaner", 129], ["Pilsner Urquell", 12]];
  assert.deepEqual(topBrands(freq, 3),
    [["Pilsner Urquell", 12], ["Augustiner", 208], ["Bitburger", 152]]);
});

test("topBrands moves a pinned brand from mid-list to the front", () => {
  const freq = [["Astra", 50], ["Pilsner Urquell", 40], ["Jever", 10]];
  assert.deepEqual(topBrands(freq, 2), [["Pilsner Urquell", 40], ["Astra", 50]]);
});

test("topBrands does not invent a chip for a pinned brand absent from the data", () => {
  const freq = [["Astra", 50], ["Jever", 10]];
  assert.deepEqual(topBrands(freq, 1), [["Astra", 50]]);
  // Fewer brands than slots: everything shows, nothing is duplicated.
  assert.deepEqual(topBrands([["Pilsner Urquell", 3]], 9), [["Pilsner Urquell", 3]]);
});

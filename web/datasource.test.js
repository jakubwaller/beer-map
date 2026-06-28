import { test } from "node:test";
import assert from "node:assert/strict";
import { loadVenues, buildBrandList, venuesByBrand, venuesByServing } from "./datasource.js";

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

test("venuesByServing 'draught' matches fass or tank, not unknown", () => {
  const at = (lon, lat) => ({ type: "Point", coordinates: [lon, lat] });
  const fc = { type: "FeatureCollection", features: [
    { type: "Feature", geometry: at(9.9, 53.5),
      properties: { name: "UnknownOnly", brands: [{ brand: "X", source: "osm", serving: "unknown" }] } },
    { type: "Feature", geometry: at(9.9, 53.5),
      properties: { name: "FassPub", brands: [{ brand: "Y", source: "osm", serving: "fass" }] } },
    { type: "Feature", geometry: at(9.9, 53.5),
      properties: { name: "TankPub", brands: [{ brand: "Z", source: "osm", serving: "tank" }] } } ] };
  const v = loadVenues(fc);
  assert.deepEqual(venuesByServing(v, "draught").map((x) => x.name).sort(), ["FassPub", "TankPub"]);
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

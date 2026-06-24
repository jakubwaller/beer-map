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

test("community ranks below manual and above osm/finder", () => {
  const sources = loadVenues(FC)[0].brands.map((b) => b.source);
  assert.ok(sources.indexOf("manual") < sources.indexOf("community"));
  assert.ok(sources.indexOf("community") < sources.indexOf("osm"));
});

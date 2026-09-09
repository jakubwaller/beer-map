import { test } from "node:test";
import assert from "node:assert/strict";
import { nearestTarget, edgeGap, lerpStops } from "./hittest.js";

const target = (x, y, r, id) => ({ x, y, r, id });

test("nearestTarget: a tap inside a dot hits it", () => {
  const t = [target(100, 100, 8, "a")];
  assert.equal(nearestTarget(t, { x: 103, y: 97 }, 22).id, "a");
});

test("nearestTarget: a tap beside a dot still hits it, up to the slop", () => {
  const t = [target(100, 100, 8, "a")];
  assert.equal(nearestTarget(t, { x: 128, y: 100 }, 22).id, "a");  // 20px gap
  assert.equal(nearestTarget(t, { x: 131, y: 100 }, 22), null);    // 23px gap
});

test("nearestTarget: nothing near, nothing opened", () => {
  assert.equal(nearestTarget([], { x: 0, y: 0 }, 22), null);
  assert.equal(nearestTarget([target(0, 0, 8)], { x: 500, y: 500 }, 22), null);
});

test("nearestTarget: the nearest dot wins, not the first one listed", () => {
  const t = [target(100, 100, 8, "far"), target(112, 100, 8, "near")];
  assert.equal(nearestTarget(t, { x: 118, y: 100 }, 22).id, "near");
});

test("nearestTarget: a tap inside a big cluster beats a nearer small dot", () => {
  // Finger 24px from the centre of a 28px-radius cluster — inside it, gap 0 —
  // and 11px from a small dot's centre, 3px outside its edge. By centre
  // distance the dot wins by more than twice; by edge distance it does not.
  // The dot is listed first, so the tie-break by order cannot explain a pass.
  const t = [target(135, 100, 8, "dot"), target(100, 100, 28, "cluster")];
  assert.equal(nearestTarget(t, { x: 124, y: 100 }, 22).id, "cluster");
});

test("nearestTarget: a missing radius counts as a point", () => {
  assert.equal(nearestTarget([{ x: 0, y: 0 }], { x: 10, y: 0 }, 22).x, 0);
});

test("edgeGap: zero inside the glyph, the distance to its edge outside", () => {
  const t = target(100, 100, 8);
  assert.equal(edgeGap(t, { x: 100, y: 100 }), 0);
  assert.equal(edgeGap(t, { x: 108, y: 100 }), 0);   // on the edge
  assert.equal(edgeGap(t, { x: 120, y: 100 }), 12);
  assert.equal(edgeGap({ x: 0, y: 0 }, { x: 3, y: 4 }), 5);  // no radius
});

test("lerpStops: interpolates between stops and clamps outside them", () => {
  const stops = [[10, 2], [13, 3.5], [16, 6]];
  assert.equal(lerpStops(stops, 10), 2);
  assert.equal(lerpStops(stops, 13), 3.5);
  assert.equal(lerpStops(stops, 16), 6);
  assert.equal(lerpStops(stops, 11.5), 2.75);
  assert.equal(lerpStops(stops, 14.5), 4.75);
  assert.equal(lerpStops(stops, 4.5), 2);
  assert.equal(lerpStops(stops, 18), 6);
});

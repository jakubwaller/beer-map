// Screen-space hit testing for map taps.
//
// A fingertip covers far more map than a mouse pointer does, and the things it
// aims at are small: a venue dot is 16px across, a gray one as little as 4px at
// zoom 10. Testing the glyph itself therefore turns "open this pub" into a game
// of skill on a phone. Every drawn dot is registered as a target with its
// radius instead, and a tap resolves to the nearest one within a slop radius.
//
// Pure and in screen pixels (y down), so it unit-tests without a map.

/**
 * How far `point` lies outside a target's glyph — 0 anywhere inside it.
 * Callers use the zero to tell "landed on the dot" from "landed near it".
 */
export function edgeGap(t, point) {
  return Math.max(0, Math.hypot(t.x - point.x, t.y - point.y) - (t.r || 0));
}

/**
 * The target nearest `point`, or null when none is within `slop` pixels of it.
 *
 * Distance is measured to the glyph's edge rather than its centre: a 56px
 * cluster would otherwise lose the tap to a 16px dot a few pixels nearer the
 * finger, even when the finger came down well inside the cluster. Ties go to
 * the earlier target, so a stable target order gives a stable answer.
 *
 * Targets are `{x, y, r}`; any other property rides along on the returned one.
 */
export function nearestTarget(targets, point, slop) {
  let best = null;
  let bestDist = Infinity;
  for (const t of targets) {
    const gap = edgeGap(t, point);
    if (gap <= slop && gap < bestDist) { best = t; bestDist = gap; }
  }
  return best;
}

/**
 * Linear interpolation over `[[x, y], ...]` stops, clamped outside the range:
 * the JS twin of MapLibre's `["interpolate", ["linear"], ...]`, so a layer's
 * drawn size can be reused for hit testing instead of being restated.
 */
export function lerpStops(stops, x) {
  for (let i = 1; i < stops.length; i++) {
    const [x0, y0] = stops[i - 1];
    const [x1, y1] = stops[i];
    if (x <= x0) return y0;
    if (x <= x1) return y0 + ((y1 - y0) * (x - x0)) / (x1 - x0);
  }
  return stops[stops.length - 1][1];
}

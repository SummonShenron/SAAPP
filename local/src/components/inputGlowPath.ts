// How fast the message box's comet (InputGlow.tsx) travels at each point of its lap: quick along the long straight edges, slow through the rounded ends.
// Pure, so it is tested by itself (node --test src/components/inputGlowPath.test.mjs from local/).
//
// The outline is a rounded rectangle drawn clockwise from the start of its top edge: top straight, top-right corner, right side, bottom-right corner,
// bottom straight, bottom-left corner, left side, top-left corner. The two "ends" (a corner, the short side, the next corner) are the slow part; the two
// straights are the fast part. Speed does not jump at the joins: it eases in over `ramp` pixels, so the comet slows into a corner and eases out of it.

export interface GlowSpeeds {
  /** Pixels per second along the middle of a straight edge. */
  fast: number;
  /** Pixels per second through the rounded ends. */
  slow: number;
  /** Pixels over which the speed eases between the two. */
  ramp: number;
}

export const DEFAULT_SPEEDS: GlowSpeeds = { fast: 999, slow: 200, ramp: 400 };
const SAMPLES = 160;

export interface GlowKeyframe {
  /** Share of the lap's time, 0 to 1. */
  t: number;
  /** Share of the way round, 0 to 100 (the units of the outline's pathLength). */
  p: number;
}

export interface GlowTimeline {
  durationMs: number;
  keyframes: GlowKeyframe[];
}

const smoothstep = (x: number) => {
  const c = Math.min(Math.max(x, 0), 1);
  return c * c * (3 - 2 * c);
};

/** The time the comet takes for one lap of a box `width` by `height` (the outline's own size, radius `radius`), and where it is along the way. */
export function glowTimeline(width: number, height: number, radius = 25, speeds: GlowSpeeds = DEFAULT_SPEEDS): GlowTimeline {
  const r = Math.max(Math.min(radius, width / 2, height / 2), 0);
  const straight = Math.max(width - 2 * r, 0);        // each of the top and bottom edges
  const side = Math.max(height - 2 * r, 0);           // each of the left and right edges
  const corner = (Math.PI * r) / 2;
  const perimeter = 2 * straight + 2 * side + 4 * corner;
  if (perimeter <= 0) return { durationMs: 1000, keyframes: [{ t: 0, p: 0 }, { t: 1, p: 100 }] };

  // [start, end) of the two straights along the way round; everything else is an end.
  const topEnd = straight;
  const bottomStart = straight + 2 * corner + side;
  const bottomEnd = bottomStart + straight;
  const speedAt = (s: number): number => {
    let distance = 0; // how far into a straight, from whichever of its ends is nearer; 0 in the slow part
    if (s >= 0 && s <= topEnd) distance = Math.min(s, topEnd - s);
    else if (s >= bottomStart && s <= bottomEnd) distance = Math.min(s - bottomStart, bottomEnd - s);
    const ramp = Math.max(Math.min(speeds.ramp, Math.max(straight / 2, 1)), 1);
    return speeds.slow + (speeds.fast - speeds.slow) * smoothstep(distance / ramp);
  };

  const step = perimeter / SAMPLES;
  const times: number[] = [0];
  for (let i = 0; i < SAMPLES; i++) {
    const mid = (i + 0.5) * step; // the speed over this little stretch, taken at its middle
    times.push(times[i] + step / speedAt(mid));
  }
  const total = times[SAMPLES];
  return {
    durationMs: Math.round(total * 1000),
    keyframes: times.map((time, i) => ({ t: time / total, p: (100 * i) / SAMPLES })),
  };
}

/** The speed, in pixels per second, between two keyframes of a timeline for a box of this size. */
export function speedBetween(a: GlowKeyframe, b: GlowKeyframe, perimeter: number, durationMs: number): number {
  return ((b.p - a.p) / 100 * perimeter) / (((b.t - a.t) * durationMs) / 1000);
}

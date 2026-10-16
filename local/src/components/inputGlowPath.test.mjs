// Run with: node --test src/components/inputGlowPath.test.mjs   (from local/; Node 22.18+ runs the TypeScript module directly)
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { DEFAULT_SPEEDS, glowTimeline, speedBetween } from './inputGlowPath.ts';

const W = 800, H = 66, R = 25;
const perimeter = (w, h, r = R) => 2 * (w - 2 * r) + 2 * (h - 2 * r) + 2 * Math.PI * r;

function speedAtShare(timeline, share, w = W, h = H) {
  const p = share * 100;
  const i = timeline.keyframes.findIndex((k, n) => k.p <= p && (timeline.keyframes[n + 1]?.p ?? 101) > p);
  return speedBetween(timeline.keyframes[i], timeline.keyframes[i + 1], perimeter(w, h), timeline.durationMs);
}

test('the lap starts at zero, ends at the whole way round, and only ever moves forward', () => {
  const { keyframes } = glowTimeline(W, H);
  assert.equal(keyframes[0].t, 0); assert.equal(keyframes[0].p, 0);
  assert.ok(Math.abs(keyframes.at(-1).t - 1) < 1e-9); assert.ok(Math.abs(keyframes.at(-1).p - 100) < 1e-9);
  for (let i = 1; i < keyframes.length; i++) {
    assert.ok(keyframes[i].t > keyframes[i - 1].t, `time at ${i}`);
    assert.ok(keyframes[i].p > keyframes[i - 1].p, `place at ${i}`);
  }
});

test('it zips along the middle of the long edges and slows to a crawl through the rounded ends', () => {
  const timeline = glowTimeline(W, H);
  const P = perimeter(W, H);
  const topMiddle = speedAtShare(timeline, (0.5 * (W - 2 * R)) / P);
  const rightEnd = speedAtShare(timeline, ((W - 2 * R) + (Math.PI * R) / 2 + (H - 2 * R) / 2) / P);   // the middle of the right-hand end
  const bottomMiddle = speedAtShare(timeline, ((W - 2 * R) + Math.PI * R + (H - 2 * R) + 0.5 * (W - 2 * R)) / P);
  assert.ok(topMiddle > 3 * rightEnd, `${topMiddle} vs ${rightEnd}`);
  // the middle of a straight reaches the fast speed only if the straight is long enough to ease up to it; the ends sit at the slow speed
  assert.ok(topMiddle > 0.85 * DEFAULT_SPEEDS.fast && bottomMiddle > 0.85 * DEFAULT_SPEEDS.fast, `${topMiddle}, ${bottomMiddle}`);
  assert.ok(Math.abs(rightEnd - DEFAULT_SPEEDS.slow) < 0.1 * DEFAULT_SPEEDS.slow);
});

test('the speed changes smoothly: no jump between neighbouring stretches, only an ease', () => {
  const { keyframes, durationMs } = glowTimeline(W, H);
  const P = perimeter(W, H);
  const speeds = keyframes.slice(1).map((k, i) => speedBetween(keyframes[i], k, P, durationMs));
  for (let i = 1; i < speeds.length; i++) assert.ok(Math.abs(speeds[i] - speeds[i - 1]) < 0.15 * DEFAULT_SPEEDS.fast, `step ${i}: ${speeds[i - 1]} to ${speeds[i]}`);
});

test('a lap takes a second or a few, and a narrower box takes less time round than a wide one', () => {
  const wide = glowTimeline(900, H).durationMs, narrow = glowTimeline(320, H).durationMs;
  assert.ok(wide > 1000 && wide < 12000, String(wide));
  assert.ok(narrow < wide);
});

test('a box too small to have straight edges still gets a valid, steady lap', () => {
  for (const [w, h] of [[40, 40], [50, 66], [10, 10], [0, 0]]) {
    const { keyframes, durationMs } = glowTimeline(w, h);
    assert.ok(durationMs > 0 && keyframes.length >= 2, `${w}x${h}`);
    for (let i = 1; i < keyframes.length; i++) assert.ok(keyframes[i].t >= keyframes[i - 1].t && keyframes[i].p >= keyframes[i - 1].p, `${w}x${h} at ${i}`);
  }
});

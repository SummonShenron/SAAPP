// Run with: node --test src/components/inputGlowPath.test.mjs   (from local/; Node 22.18+ runs the TypeScript module directly)
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { DEFAULT_SPEEDS, glowTimeline, headPosition, speedBetween, sweepTimeline } from './inputGlowPath.ts';

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

test('the head position at a moment of the lap is read back from the lap, and wraps past the end', () => {
  const { keyframes } = glowTimeline(W, H);
  assert.equal(headPosition(keyframes, 0), 0);
  assert.ok(Math.abs(headPosition(keyframes, 1) - 100) < 1e-6 || Math.abs(headPosition(keyframes, 1)) < 1e-6);
  assert.ok(Math.abs(headPosition(keyframes, 1.25) - headPosition(keyframes, 0.25)) < 1e-9);       // a quarter of the way into the next lap is where a quarter is
  for (const k of [keyframes[10], keyframes[80], keyframes[150]]) assert.ok(Math.abs(headPosition(keyframes, k.t) - k.p) < 1e-9);
  let last = -1;
  for (let t = 0; t <= 1; t += 0.01) { const p = headPosition(keyframes, t); assert.ok(p >= last); last = p; }
});

test('a sweep from the top is the sweep the box always had: the first 90 percent of the lap, at the comet\'s own pace', () => {
  const timeline = glowTimeline(W, H);
  const sweep = sweepTimeline(timeline, 0, 90);
  assert.equal(sweep.keyframes[0].t, 0); assert.equal(sweep.keyframes[0].p, 0); assert.equal(sweep.keyframes[0].progress, 0);
  assert.equal(sweep.keyframes.at(-1).t, 1); assert.equal(sweep.keyframes.at(-1).p, 90); assert.equal(sweep.keyframes.at(-1).progress, 90);
  const lapShare = timeline.keyframes.findIndex(k => k.p >= 90);
  assert.ok(Math.abs(sweep.durationMs / timeline.durationMs - timeline.keyframes[lapShare].t) < 0.02);       // 90 percent of the way round takes about that share of the lap's time
});

test('a sweep that starts partway round carries on from there, past the start of the outline, and ends 90 percent of a lap later', () => {
  const timeline = glowTimeline(W, H);
  for (const start of [10, 37.5, 62, 95]) {
    const sweep = sweepTimeline(timeline, start, 90);
    const first = sweep.keyframes[0], last = sweep.keyframes.at(-1);
    assert.ok(Math.abs(first.p - start) < 1e-9 && first.t === 0 && first.progress === 0, `start ${start}`);
    assert.ok(Math.abs(last.p - (start + 90)) < 1e-9 && last.t === 1 && Math.abs(last.progress - 90) < 1e-9, `end ${start}`);
    for (let i = 1; i < sweep.keyframes.length; i++) {
      assert.ok(sweep.keyframes[i].p > sweep.keyframes[i - 1].p && sweep.keyframes[i].t > sweep.keyframes[i - 1].t, `forward at ${i} from ${start}`);
    }
    assert.ok(sweep.durationMs > 0 && sweep.durationMs < timeline.durationMs, String(sweep.durationMs));
    if (start > 10) assert.ok(last.p > 100, "it runs on through the start of the outline");
  }
});

test('where the sweep starts is where the comet was: the head position it is given is the one the comet reports', () => {
  const timeline = glowTimeline(W, H);
  for (const t of [0.05, 0.3, 0.55, 0.9]) {
    const sweep = sweepTimeline(timeline, headPosition(timeline.keyframes, t), 90);
    assert.ok(Math.abs(sweep.keyframes[0].p - headPosition(timeline.keyframes, t)) < 1e-9);
  }
});

test('the sweep keeps the comet\'s pace: it is quicker along a straight than through an end, wherever it starts', () => {
  const timeline = glowTimeline(W, H);
  const P = perimeter(W, H);
  const sweep = sweepTimeline(timeline, 40, 90);
  const speeds = sweep.keyframes.slice(1).map((k, i) => ({ speed: speedBetween({ t: sweep.keyframes[i].t, p: sweep.keyframes[i].p }, { t: k.t, p: k.p }, P, sweep.durationMs), at: sweep.keyframes[i].p % 100 }));
  assert.ok(Math.max(...speeds.map(s => s.speed)) > 3 * Math.min(...speeds.map(s => s.speed)));
});

test('a box too small to have straight edges still gets a valid, steady lap', () => {
  for (const [w, h] of [[40, 40], [50, 66], [10, 10], [0, 0]]) {
    const { keyframes, durationMs } = glowTimeline(w, h);
    assert.ok(durationMs > 0 && keyframes.length >= 2, `${w}x${h}`);
    for (let i = 1; i < keyframes.length; i++) assert.ok(keyframes[i].t >= keyframes[i - 1].t && keyframes[i].p >= keyframes[i - 1].p, `${w}x${h} at ${i}`);
  }
});

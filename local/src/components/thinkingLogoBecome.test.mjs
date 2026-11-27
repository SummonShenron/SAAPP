// Run with: node --test src/components/thinkingLogoBecome.test.mjs   (from local/)
// The loader turning into the first step's icon (2026-11-19): the pure maths (thinkingLogoBecome.ts) and where it is wired in.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { FADE_TAIL, GATHER_SHARE, GLYPH_BIG, GLYPH_PATH, becomePose } from './thinkingLogoBecome.ts';
import { MID_X, MID_Y, logoPose, bodyTransform } from './thinkingLogoMotion.ts';

const read = name => readFileSync(new URL(name, import.meta.url), 'utf8').replace(/\r\n/g, '\n');
const distance = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1]);
const TARGET = { travelX: -21.5, travelY: 85, endSize: 44 };
const EARLY = logoPose(250);                  // in the crouch: squashed, dots apart in no shape yet
const CIRCLE = logoPose(4200);                // on the circle, with the runner and a trail going
const STAR = logoPose(10100);                 // a graph shape, joined by links

test('it starts where the loader was: nothing has moved, nothing is hidden, and there is no sparkle yet', () => {
  for (const from of [EARLY, CIRCLE, STAR]) {
    const pose = becomePose(from, 0, TARGET);
    pose.dots.forEach((dot, i) => assert.ok(distance([dot.x, dot.y], [from.dots[i].x, from.dots[i].y]) < 1e-9 && dot.scaleX === from.dots[i].scaleX, `dot ${i} has not moved`));
    assert.equal(pose.dotsOpacity, 1);
    assert.equal(pose.edgeOpacity, from.edgeOpacity);
    assert.equal(pose.curveOpacity, from.curve.opacity);
    assert.equal(pose.runnerOpacity, from.runner.opacity);
    assert.deepEqual(pose.body, from.body);
    assert.equal(pose.transform, from.transform, 'the mark is drawn exactly as it was');
    assert.equal(pose.glyph.opacity, 0, 'no sparkle yet');
  }
  assert.equal(becomePose(CIRCLE, -50, TARGET).glyph.opacity, 0, 'and a time before the start is the start');
});

test('when it has gathered, the six dots are one at the middle and gone, the links, line, trail and runner are gone, the mark is at rest, and the sparkle has formed there', () => {
  for (const from of [EARLY, CIRCLE, STAR]) {
    const pose = becomePose(from, GATHER_SHARE, TARGET);
    pose.dots.forEach((dot, i) => assert.ok(distance([dot.x, dot.y], [MID_X, MID_Y]) < 1e-9, `dot ${i} is in the middle`));
    assert.equal(pose.dotsOpacity, 0);
    assert.equal(pose.edgeOpacity, 0);
    assert.equal(pose.curveOpacity, 0);
    assert.equal(pose.runnerOpacity, 0);
    assert.ok(pose.trail.every(piece => piece.opacity === 0), 'no trail');
    assert.deepEqual(pose.body, { lift: 0, scaleX: 1, scaleY: 1 }, 'the squash and the jump have eased out');
    assert.equal(pose.transform, bodyTransform({ lift: 0, scaleX: 1, scaleY: 1 }));
    assert.ok(distance([pose.glyph.x, pose.glyph.y], [MID_X, MID_Y]) < 1e-9, 'the sparkle is where the dots were');
    assert.ok(Math.abs(pose.glyph.size - GLYPH_BIG) < 1e-9 && pose.glyph.opacity === 1, 'and it is full size and solid');
  }
});

test('then the sparkle travels to the step\'s dot, getting as small as the dot\'s own icon, and arrives exactly as the time is up; after that it fades', () => {
  const arrived = becomePose(CIRCLE, 1, TARGET);
  assert.ok(distance([arrived.glyph.x, arrived.glyph.y], [MID_X + TARGET.travelX, MID_Y + TARGET.travelY]) < 1e-9, 'on the dot');
  assert.equal(arrived.glyph.size, TARGET.endSize);
  assert.equal(arrived.glyph.opacity, 1, 'still solid as the dot comes in under it');
  const middle = becomePose(CIRCLE, (GATHER_SHARE + 1) / 2, TARGET);
  assert.ok(middle.glyph.x < MID_X && middle.glyph.y > MID_Y && middle.glyph.y < MID_Y + TARGET.travelY, 'part way there');
  assert.ok(middle.glyph.size < GLYPH_BIG && middle.glyph.size > TARGET.endSize, 'part way smaller');
  let last = 1;
  for (let p = 1; p <= 1 + FADE_TAIL + 0.2; p += 0.01) {
    const opacity = becomePose(CIRCLE, p, TARGET).glyph.opacity;
    assert.ok(opacity <= last + 1e-9, 'it only fades');
    last = opacity;
  }
  assert.equal(becomePose(CIRCLE, 1 + FADE_TAIL, TARGET).glyph.opacity, 0, 'and it is gone');
  assert.equal(becomePose(CIRCLE, 5, TARGET).glyph.opacity, 0, 'and stays gone');
});

test('nothing jumps: from one moment to the next no dot, no sparkle position and no size changes by more than a little, and the sparkle never shows while the dots do', () => {
  for (const from of [EARLY, CIRCLE, STAR]) {
    let last = becomePose(from, 0, TARGET);
    for (let p = 0.005; p <= 1 + FADE_TAIL; p += 0.005) {
      const now = becomePose(from, p, TARGET);
      now.dots.forEach((dot, i) => assert.ok(distance([dot.x, dot.y], [last.dots[i].x, last.dots[i].y]) < 6, `dot ${i} jumped at ${p}`));
      assert.ok(distance([now.glyph.x, now.glyph.y], [last.glyph.x, last.glyph.y]) < 4, `the sparkle jumped at ${p}`);
      assert.ok(Math.abs(now.glyph.size - last.glyph.size) < 8, `the sparkle changed size at ${p}`);
      assert.ok(Math.abs(now.glyph.opacity - last.glyph.opacity) < 0.1, `the sparkle blinked at ${p}`);
      assert.ok(now.glyph.opacity * now.dotsOpacity < 0.5 || p > 0.2, 'the sparkle only comes in as the dots go');
      last = now;
    }
  }
});

test('with nowhere to go (no dot found) the sparkle forms where the mark was and fades there', () => {
  const still = { travelX: 0, travelY: 0, endSize: GLYPH_BIG };
  const pose = becomePose(CIRCLE, 1, still);
  assert.ok(distance([pose.glyph.x, pose.glyph.y], [MID_X, MID_Y]) < 1e-9 && pose.glyph.size === GLYPH_BIG);
});

test('the pose of the loader carries the numbers behind its transform, and the transform is made from them', () => {
  for (const t of [0, 100, 250, 430, 700, 1000, 1500, 4200]) {
    const pose = logoPose(t);
    assert.equal(pose.transform, bodyTransform(pose.body), `at ${t}`);
  }
  assert.ok(logoPose(250).body.scaleY === 1 && logoPose(430).body.scaleY === 1, 'stage 1 holds still: no crouch or stretch in it');
});

test('the sparkle is drawn from the same path as the think icon, so it can turn into it', () => {
  const icon = read('./StepIcon.tsx');
  const [, path] = icon.match(/think: \(\) => <path className="si-twinkle" d="([^"]+)"/) ?? [];
  assert.ok(path, 'found the think icon');
  assert.equal(GLYPH_PATH, path);
});

test('it is wired in: the logo takes becomeMs, finds the step\'s own dot on the page, starts from the pose it was last drawn in, stops when told to, and draws the sparkle outside its squashing group; the steps pass it only for a sparkle first step', () => {
  const logo = read('./ThinkingLogo.tsx');
  assert.match(logo, /becomeMs\?: number/);
  assert.match(logo, /closest\('\.steps-inline'\)\?\.querySelector\('\.trace-node-dot'\)/, 'the step\'s own dot is the destination');
  assert.match(logo, /from: lastPose\.current/, 'from the pose last drawn');
  assert.match(logo, /return \(\) => \{ becoming\.current = null; \}/, 'and it can be stopped');
  assert.match(logo, /becomePose\(b\.from, \(now - b\.start\) \/ b\.total, b\.target\)/);
  assert.ok(logo.indexOf('</g>\n      {/* the sparkle') > logo.indexOf('<g ref={markRef}'), 'the sparkle is drawn after, outside, the squashing group');
  assert.match(logo, /<path d=\{GLYPH_PATH\}/);
  assert.doesNotMatch(logo, /useState/, 'still no React state per frame');
  const steps = read('./StepsDisclosure.tsx');
  assert.match(steps, /const becomesSparkle = steps\[0\]\?\.kind === 'think';/);
  assert.match(steps, /leaving=\{landing && !becomesSparkle\}/, 'any other first step keeps the plain fade');
  assert.match(steps, /becomeMs=\{landing && becomesSparkle \? scaled\(ARRIVE_DELAY_MS, pace\) : undefined\}/, 'and the time is the dot\'s own arrival delay, at the landing\'s pace');
});

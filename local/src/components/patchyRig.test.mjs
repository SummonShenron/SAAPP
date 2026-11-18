// Run with: node --test src/components/patchyRig.test.mjs   (from local/)
// How Mini Patchy carries his laptop in the stretch (2026-11-18): one small state (where the laptop is, how much his hands hold it, where the free arm rests) gives the laptop's place and the shape of both arms, so his hands are
// always on its corners. The maths is components/patchyRig.ts, the frame loop hooks/useStretchRig.ts, the drawing MiniPatchy.tsx.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { GRIP_MS, LAP_LIFT, LAP_TRAVEL, LEFT_HANG, LEFT_TYPE, RIGHT_HANG, RIG_REST, SHOULDER_LEFT, SHOULDER_RIGHT, armPath, blendRig, easeInOut, lapOffset, rigPose } from './patchyRig.ts';

const read = name => readFileSync(new URL(name, import.meta.url), 'utf8').replace(/\r\n/g, '\n');
const mini = read('./MiniPatchy.tsx');
const parts = read('./PatchyParts.tsx');
const hook = read('../hooks/useStretchRig.ts');

const near = (a, b, eps = 0.01) => Math.abs(a - b) <= eps;

test('the laptop goes from his lap to the floor beside him along an arc: picked up, carried, put down', () => {
  assert.deepEqual(lapOffset(0), [0, 0]);
  assert.deepEqual(lapOffset(1), LAP_TRAVEL);
  const [mx, my] = lapOffset(0.5);
  assert.ok(near(mx, LAP_TRAVEL[0] / 2));
  assert.ok(my < LAP_TRAVEL[1] / 2 - LAP_LIFT / 2, 'lifted above the straight line halfway');
  // never underneath where it starts or ends
  for (let i = 0; i <= 20; i++) {
    const [, y] = lapOffset(i / 20);
    assert.ok(y <= Math.max(0, LAP_TRAVEL[1]) + 0.01, `${i / 20}: not pushed into the edge`);
  }
  assert.deepEqual(lapOffset(-3), [0, 0]);
  assert.deepEqual(lapOffset(7), LAP_TRAVEL);
  assert.deepEqual(lapOffset(NaN), [0, 0]);
});

test('an ease starts and stops gently, and the blend gives the quicker grip its own clock', () => {
  assert.equal(easeInOut(0), 0);
  assert.equal(easeInOut(1), 1);
  assert.equal(easeInOut(0.5), 0.5);
  assert.ok(easeInOut(0.1) < 0.1 && easeInOut(0.9) > 0.9);
  const from = { lap: 0, grip: 0, mode: 0 };
  const to = { lap: 1, grip: 1, mode: 1 };
  assert.deepEqual(blendRig(from, to, 0), from);
  assert.deepEqual(blendRig(from, to, 1), to);
  const half = blendRig(from, to, 0.5, 1);
  assert.ok(near(half.lap, 0.5) && near(half.mode, 0.5) && near(half.grip, 1), 'the grip is already on when the carrying is half done');
  assert.ok(GRIP_MS >= 200 && GRIP_MS <= 600);
});

test('at rest the rig IS his ordinary pose: the laptop on his lap, the left hand on the keys, the right arm hanging', () => {
  const pose = rigPose(RIG_REST);
  assert.deepEqual(pose.lap, [0, 0]);
  assert.deepEqual(pose.left, LEFT_TYPE);
  assert.deepEqual(pose.right, RIGHT_HANG);
  assert.deepEqual(rigPose({ lap: 0, grip: 0, mode: 1 }).left, LEFT_HANG, 'the left arm resting, hanging');
  // the shapes are the ones already drawn: the typing arm, the hanging arm, and the right arm's hang pose
  assert.match(mini, /<path d="M24 62 C 16 70, 22 82, 32\.5 78\.5"/);
  assert.match(mini, /<path d="M24 62 C 16 66, 16 76, 22 82"/);
  assert.match(parts, /hang: \{ v: \[84, 68, 84, 76, 78, 82\] \},/);
  assert.deepEqual(LEFT_TYPE, [16, 70, 22, 82, 32.5, 78.5]);
  assert.deepEqual(LEFT_HANG, [16, 66, 16, 76, 22, 82]);
  assert.deepEqual(RIGHT_HANG, [84, 68, 84, 76, 78, 82]);
  assert.deepEqual(SHOULDER_LEFT, [24, 62]);
  assert.deepEqual(SHOULDER_RIGHT, [76, 62]);
  assert.match(parts, /M 76 62 C/, 'the right arm starts at that shoulder');
  assert.equal(armPath(SHOULDER_LEFT, LEFT_TYPE), 'M 24 62 C 16 70, 22 82, 32.5 78.5');
});

test('holding the laptop, his hands are on its two corners wherever it is, and they go with it', () => {
  for (const lap of [0, 0.25, 0.5, 0.75, 1]) {
    const pose = rigPose({ lap, grip: 1, mode: 0 });
    const [ox, oy] = lapOffset(lap);
    assert.ok(near(pose.left[4], 34 + ox) && near(pose.left[5], 77 + oy), `left hand at ${lap}`);
    assert.ok(near(pose.right[4], 74 + ox) && near(pose.right[5], 76 + oy), `right hand at ${lap}`);
    assert.deepEqual(pose.lap, [ox, oy]);
  }
  // the corners are the corners of the laptop's base in the drawing (34,77 and 74,76)
  assert.match(mini, /<polygon points="34,77 64,70 74,76 44,83"/);
  // holding it, the hands come off the keys: the grip is not where the typing hand was
  assert.notDeepEqual(rigPose({ lap: 0, grip: 1, mode: 0 }).left.slice(4), LEFT_TYPE.slice(4));
});

test('letting go brings the hands from the laptop to where the arms rest, smoothly', () => {
  const held = rigPose({ lap: 1, grip: 1, mode: 1 });
  const half = rigPose({ lap: 1, grip: 0.5, mode: 1 });
  const free = rigPose({ lap: 1, grip: 0, mode: 1 });
  assert.deepEqual(free.left, LEFT_HANG);
  assert.deepEqual(free.right, RIGHT_HANG);
  for (let i = 0; i < 6; i++) assert.ok(half.left[i] >= Math.min(held.left[i], free.left[i]) - 1e-9 && half.left[i] <= Math.max(held.left[i], free.left[i]) + 1e-9, `left ${i} between`);
  for (let i = 0; i < 6; i++) assert.ok(half.right[i] >= Math.min(held.right[i], free.right[i]) - 1e-9 && half.right[i] <= Math.max(held.right[i], free.right[i]) + 1e-9, `right ${i} between`);
});

test('the frame loop eases from wherever the rig is to the target, keeps its state between frames, stops when it is there, and writes the pose out', () => {
  assert.match(hook, /const from = \{ \.\.\.current\.current \};/, 'a stage cut short carries on from where it is');
  assert.match(hook, /const gripDuration = Math\.min\(duration, GRIP_MS\);/);
  assert.match(hook, /const state = blendRig\(from, to, Math\.min\(elapsed \/ duration, 1\), Math\.min\(elapsed \/ gripDuration, 1\)\);/);
  assert.match(hook, /applyRef\.current\(rigPose\(state\)\);/);
  assert.match(hook, /if \(elapsed < duration\) frame = requestAnimationFrame\(tick\);/);
  assert.match(hook, /return \(\) => cancelAnimationFrame\(frame\);\n  \}, \[lap, grip, mode, ms\]\);/);
  assert.match(hook, /const elapsed = Math\.max\(0, now - start\);/, 'a frame stamped just before the start is not a negative time');
  // no React state is touched per frame
  assert.doesNotMatch(hook, /useState|setState/);
});

test('the carrying arms are drawn at rest in the ordinary pose and have the ordinary look: same strokes, the right hand in the accent colour', () => {
  assert.match(mini, /<path ref=\{leftArmRef\} d=\{armPath\(SHOULDER_LEFT, LEFT_TYPE\)\} stroke="#8E95A2" strokeWidth="4" strokeLinecap="round" fill="none" \/>/);
  assert.match(mini, /<circle ref=\{leftHandRef\} cx=\{LEFT_TYPE\[4\]\} cy=\{LEFT_TYPE\[5\]\} r="3\.5" fill="#8E95A2" \/>/);
  assert.match(mini, /<path ref=\{rightArmRef\} d=\{armPath\(SHOULDER_RIGHT, RIGHT_HANG\)\} stroke="#8E95A2" strokeWidth="4" strokeLinecap="round" fill="none" \/>/);
  assert.match(mini, /<circle ref=\{rightHandRef\} className="mp-accent" cx=\{RIGHT_HANG\[4\]\} cy=\{RIGHT_HANG\[5\]\} r="3\.5" \/>/);
  // the same width and colour as the arms they swap with
  assert.match(parts, /<path ref=\{pathRef\} d=\{armPath\(first\)\} stroke="#8E95A2" strokeWidth="4"/);
});

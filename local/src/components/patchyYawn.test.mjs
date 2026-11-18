// Run with: node --test src/components/patchyYawn.test.mjs   (from local/)
// Patchy's yawn (2026-11-18): once he has gone drowsy he yawns every so often: mouth wide open, eyes squeezed shut, head tipped back, a hand over his mouth. Timings in patchyYawn.ts, played by hooks/useYawn.ts.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { YAWN_EVERY_MS, YAWN_FIRST_MS, YAWN_MS } from './patchyYawn.ts';

const read = name => readFileSync(new URL(name, import.meta.url), 'utf8').replace(/\r\n/g, '\n');
const hook = read('../hooks/useYawn.ts');
const mini = read('./MiniPatchy.tsx');
const hero = read('./PatchyHero.tsx');
const parts = read('./PatchyParts.tsx');
const look = read('./patchyLook.ts');
const css = read('./__styles__/MiniPatchy.css');

test('a yawn is a few seconds, the first comes soon after he goes drowsy, and there is a long rest between two', () => {
  assert.ok(YAWN_MS >= 2000 && YAWN_MS <= 4000);
  assert.ok(YAWN_FIRST_MS >= 1000 && YAWN_FIRST_MS <= 3000, 'long enough to see the heavy lids first');
  assert.ok(YAWN_EVERY_MS >= YAWN_MS + 4000, 'never back to back');
});

test('the lift of his body in the stylesheet lasts as long as the yawn', () => {
  const match = css.match(/\.mp-yawn \.mp-bob \{ animation: mp-yawn-lift ([\d.]+)s ease-in-out 1; \}/);
  assert.ok(match, 'the lift is declared');
  assert.equal(Number(match[1]) * 1000, YAWN_MS);
});

test('the hook yawns only while he is drowsy and motion is allowed, first and then repeatedly, and puts everything away when he wakes', () => {
  assert.match(hook, /if \(!drowsy \|\| !motionAllowed\(\)\) return undefined;/);
  assert.match(hook, /timers\.push\(window\.setTimeout\(\(\) => setYawning\(false\), YAWN_MS\)\);/);
  assert.match(hook, /timers\.push\(window\.setTimeout\(yawn, YAWN_EVERY_MS\)\);/);
  assert.match(hook, /timers\.push\(window\.setTimeout\(yawn, YAWN_FIRST_MS\)\);/);
  assert.match(hook, /return \(\) => \{ timers\.forEach\(timer => window\.clearTimeout\(timer\)\); setYawning\(false\); \};\n  \}, \[drowsy\]\);/);
  assert.match(hook, /return drowsy && yawning;/, 'a stale yawn is never drawn for a Patchy who is awake');
});

test('both Patchys use it: the arm goes to his mouth, the head draws the yawn, and the stylesheet tips the head back', () => {
  for (const [name, source] of [['mini', mini], ['hero', hero]]) {
    assert.match(source, /import \{ useYawn \} from '\.\.\/hooks\/useYawn';/, name);
    assert.match(source, /const yawning = useYawn\(mood === 'drowsy'\);/, name);
    assert.match(source, /yawning \? 'yawn'/, name);
    assert.match(source, /' mp-yawn'/, name);
    assert.match(source, /yawning=\{yawning\}/, name);
  }
  assert.match(look, /'hug' \| 'yawn';/);
  assert.match(parts, /yawn: \{ v: \[84, 58, 72, 52, 60, 46\] \},/);
  assert.match(css, /\.mp-yawn \.mp-turn \{ transform: rotate\(-6deg\) translateY\(-1px\); \}/);
  assert.ok(css.indexOf('.mp-yawn .mp-turn') > css.indexOf('.mp-drowsy .mp-turn'), 'after the drooping head it replaces, so it wins');
});

test('mid-yawn the head has eyes squeezed shut and an open mouth and none of the usual ones', () => {
  assert.match(parts, /const eyesOpen = !closedEyes && !laughing && !scrunched && !yawning;/);
  assert.match(parts, /const mouthShown = !laughing && mood !== 'soft' && !scrunched && !mouthless && !yawning;/);
  assert.match(parts, /fade\(scrunched \|\| yawning\)/);
  assert.match(parts, /<g className=\{fade\(yawning\)\}>\s+<ellipse className="mp-accent" cx="50" cy="43\.5" rx="4" ry="4\.8" \/>/);
  // the open mouth fits inside the visor (y 25 to 49)
  assert.ok(43.5 - 4.8 >= 25 && 43.5 + 4.8 <= 49);
});

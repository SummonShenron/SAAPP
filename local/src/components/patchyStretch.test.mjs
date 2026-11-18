// Run with: node --test src/components/patchyStretch.test.mjs   (from local/)
// Mini Patchy's stretch (2026-11-18): once a conversation has started, and on every refresh of one, the first time he is idle he sets his laptop down, stands up, stretches, sits back down and picks it up.
// The script is components/patchyStretch.ts; MiniPatchy.tsx plays it and MiniPatchy.css draws it.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { STRETCH_STAGES, STRETCH_START_DELAY_MS, STRETCH_TOTAL_MS, stretchFlags } from './patchyStretch.ts';

const read = name => readFileSync(new URL(name, import.meta.url), 'utf8').replace(/\r\n/g, '\n');
const mini = read('./MiniPatchy.tsx');
const css = read('./__styles__/MiniPatchy.css');

test('the script puts the laptop down before he stands, has him standing before he reaches, and ends with him sitting at the laptop again', () => {
  assert.deepEqual(STRETCH_STAGES.map(s => s.name), ['lift', 'rise', 'reach', 'lean back', 'sway', 'lean back again', 'relax', 'sit', 'pick up']);
  for (const stage of STRETCH_STAGES) {
    if (stage.stand) assert.equal(stage.down, true, `${stage.name}: he does not stand with the laptop on his lap`);
    if (stage.reach) assert.equal(stage.stand, true, `${stage.name}: he does not stretch sitting down`);
    if (!stage.reach) assert.ok(Math.abs(stage.lean) < 1, `${stage.name}: he only leans while his arms are up`);
    assert.ok(stage.ms >= 500 && stage.ms <= 1500, `${stage.name}: long enough to see, short enough to be a little stretch`);
  }
  const first = STRETCH_STAGES[0];
  assert.deepEqual([first.down, first.stand, first.reach], [true, false, false], 'the first thing he does is put the laptop down');
  const last = STRETCH_STAGES[STRETCH_STAGES.length - 1];
  assert.deepEqual([last.down, last.stand, last.reach, last.lean], [false, false, false, 0], 'the last thing is picking it up, sitting as he began');
  // the lean goes back, then forward, then back, so it reads as a stretch and not a stuck pose
  const leans = STRETCH_STAGES.filter(s => s.reach).map(s => s.lean);
  assert.ok(Math.max(...leans) >= 8 && Math.min(...leans) < 0);
});

test('it is a little stretch: a few seconds in all, and he is seen typing for a moment before it starts', () => {
  assert.equal(STRETCH_TOTAL_MS, STRETCH_STAGES.reduce((sum, s) => sum + s.ms, 0));
  assert.ok(STRETCH_TOTAL_MS >= 4000 && STRETCH_TOTAL_MS <= 8000, String(STRETCH_TOTAL_MS));
  assert.ok(STRETCH_START_DELAY_MS >= 1000 && STRETCH_START_DELAY_MS <= 4000);
});

test('what is drawn at a stage comes from the list, and anything that is not a stage is just sitting at the laptop', () => {
  const rest = { stretching: false, down: false, stand: false, reach: false, lean: 0 };
  for (const stage of [-1, -5, STRETCH_STAGES.length, 99, 1.5, NaN, Infinity]) assert.deepEqual(stretchFlags(stage), rest, String(stage));
  assert.deepEqual(stretchFlags(0), { stretching: true, down: true, stand: false, reach: false, lean: 0 });
  assert.deepEqual(stretchFlags(3), { stretching: true, down: true, stand: true, reach: true, lean: 11 });
  assert.deepEqual(stretchFlags(STRETCH_STAGES.length - 1), { stretching: true, down: false, stand: false, reach: false, lean: 0 });
  STRETCH_STAGES.forEach((stage, i) => {
    const flags = stretchFlags(i);
    assert.deepEqual([flags.down, flags.stand, flags.reach, flags.lean], [stage.down, stage.stand, stage.reach, stage.lean]);
  });
});

test('Mini Patchy plays it: once, when he is idle, never for someone who asks for less motion, and any other mood cuts it off', () => {
  assert.match(mini, /import \{ STRETCH_STAGES, STRETCH_START_DELAY_MS, stretchFlags \} from '\.\/patchyStretch';/);
  assert.match(mini, /const \[stage, setStage\] = useState\(-1\);/);
  assert.match(mini, /const \[stretchedOnce, setStretchedOnce\] = useState\(false\);/);          // state, so a hello can arm it again (patchyFlourish.test.mjs)
  assert.match(mini, /const idle = mood === 'idle';/);
  assert.match(mini, /if \(!idle \|\| hello \|\| stretchedOnce \|\| !motionAllowed\(\)\) return undefined;/);
  assert.match(mini, /timer = window\.setTimeout\(\(\) => step\(0\), STRETCH_START_DELAY_MS\);/);
  assert.match(mini, /else \{ setStretchedOnce\(true\); setStage\(-1\); \}/);
  assert.match(mini, /return \(\) => \{ window\.clearTimeout\(timer\); setStage\(-1\); \};\n  \}, \[idle, hello, stretchedOnce\]\);/);
  assert.match(mini, /const stretch = stretchFlags\(idle && !tada \? stage : -1\);/);          // (a ta-da takes over: patchyFlourish.test.mjs)
  assert.match(read('./patchyLook.ts'), /export function motionAllowed\(\)/);
});

test('while he stretches his arms and face follow the script: the free arm hangs, then goes up with the cheer pose, the eyes squeeze shut, and the idle wave is off', () => {
  assert.match(mini, /stretch\.stretching \? \(stretch\.reach \? 'cheer' : 'hang'\)/);
  assert.match(mini, /const wave = mood === 'idle' && !stretch\.stretching && energy !== 'subdued' && budget !== 'throttled';/);
  assert.match(mini, /<PatchyHead [^>]*scrunched=\{stretch\.reach\}[^>]*\/>/);
  assert.match(mini, /\$\{stretch\.stretching \? ' mp-stretching' : ''\}\$\{stretch\.down \? ' mp-down' : ''\}\$\{stretch\.stand \? ' mp-stand' : ''\}\$\{stretch\.reach \? ' mp-reach' : ''\}/);
  assert.match(mini, /style=\{stretch\.stretching \? \(\{ '--mp-lean': stretch\.lean \} as CSSProperties\) : undefined\}/);
});

test('he is drawn in layers that can move on their own: legs and torso, the laptop, then arms and head, which rise and lean together', () => {
  assert.equal((mini.match(/<g className="mp-rise">/g) ?? []).length, 2, 'two layers rise together: legs and torso, and (after the laptop) the arms and head');
  assert.equal((mini.match(/<g className="mp-lap">/g) ?? []).length, 1);
  assert.ok(mini.indexOf('<g className="mp-lap">') > mini.indexOf('className="mp-leg mp-leg-l"'));
  assert.ok(mini.indexOf('<g className="mp-lap">') < mini.indexOf('<g className="mp-arm-typing">'));
  assert.ok(mini.indexOf('<g className="mp-arm-typing">') < mini.indexOf('<PatchyHead'));
  assert.match(mini, /<g className="mp-arm-free mp-arm-hang">/);
  assert.match(mini, /<g className="mp-arm-free mp-arm-up">/);
  // the laptop's polygons and lines are all inside its layer: none left in front of him
  const lap = mini.slice(mini.indexOf('<g className="mp-lap">'), mini.indexOf('<g className="mp-rise">', mini.indexOf('<g className="mp-lap">')));
  assert.equal((lap.match(/<polygon /g) ?? []).length, 4);
  assert.equal((mini.match(/<polygon /g) ?? []).length, 4);
  assert.ok(lap.includes('mp-cursor'));
});

test('the stylesheet: the laptop goes down beside him, he rises by the length of his legs and leans from the hips, the arms and the typing hand fade, and the legs stop tapping while he stands', () => {
  assert.match(css, /\.mp-stretching svg \{ overflow: visible; \}/);
  assert.match(css, /\.mp-rise \{ transform-origin: 50px 86px; transition: translate 0\.8s ease-in-out, rotate 0\.7s ease-in-out; \}/);
  assert.match(css, /\.mp-stand \.mp-rise \{ translate: 0 -12px; \}/);
  assert.match(css, /\.mp-stretching \.mp-rise \{ rotate: calc\(var\(--mp-lean, 0\) \* 1deg\); \}/);
  assert.match(css, /\.mp-stand \.mp-leg \{ animation: none; \}/);
  assert.match(css, /\.mp-lap \{ transition: translate 0\.7s ease-in-out; \}/);
  assert.match(css, /\.mp-down \.mp-lap \{ translate: -36px 3px; \}/);
  assert.match(css, /\.mp-down \.mp-arm-typing \{ opacity: 0; \}/);
  assert.match(css, /\.mp-arm-free \{ opacity: 0; transition: opacity 0\.3s ease; \}/);
  assert.match(css, /\.mp-down \.mp-arm-hang \{ opacity: 1; \}/);
  assert.match(css, /\.mp-reach \.mp-arm-hang \{ opacity: 0; \}/);
  assert.match(css, /\.mp-reach \.mp-arm-up \{ opacity: 1; \}/);
  // the rise is 12 units: the 13 of leg and 7 of foot that dangled below the torso (the input's edge is the floor)
  assert.ok(css.indexOf('.mp-rise {') > css.indexOf('@keyframes mp-leg-tap'), "after the legs' own animation");
});

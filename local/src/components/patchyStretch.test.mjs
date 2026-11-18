// Run with: node --test src/components/patchyStretch.test.mjs   (from local/)
// Mini Patchy's stretch (2026-11-18): once a conversation has started, and on every refresh of one, the first time he is idle he takes his laptop in both hands and sets it down beside him, stands up, stretches, sits back
// down and picks it up again. The script is components/patchyStretch.ts; the carrying is patchyRig.ts (patchyRig.test.mjs); MiniPatchy.tsx plays it and MiniPatchy.css draws it.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { RIG_SETTLE_MS, STRETCH_STAGES, STRETCH_START_DELAY_MS, STRETCH_TOTAL_MS, stretchFlags } from './patchyStretch.ts';

const read = name => readFileSync(new URL(name, import.meta.url), 'utf8').replace(/\r\n/g, '\n');
const mini = read('./MiniPatchy.tsx');
const css = read('./__styles__/MiniPatchy.css');

test('he takes hold of the laptop, sets it down, lets go, and only then stands; he sits, takes hold again, brings it back and puts his hands on the keys', () => {
  assert.deepEqual(STRETCH_STAGES.map(s => s.name), ['take hold', 'set it down', 'let go', 'rise', 'reach', 'lean back', 'sway', 'lean back again', 'relax', 'sit', 'take hold again', 'bring it back', 'hands to the keys']);
  STRETCH_STAGES.forEach((stage, i) => {
    if (stage.stand) assert.ok(stage.lap === 1 && stage.arms === 'free', `${stage.name}: the laptop is on the floor and his arms are free when he stands`);
    if (stage.reach) assert.equal(stage.stand, true, `${stage.name}: he does not stretch sitting down`);
    if (!stage.reach) assert.ok(Math.abs(stage.lean) < 1, `${stage.name}: he only leans while his arms are up`);
    assert.ok(stage.ms >= 400 && stage.ms <= 1300, `${stage.name}: long enough to see, short enough to be a little stretch`);
    // the laptop only moves while his hands are on it
    const before = i === 0 ? 0 : STRETCH_STAGES[i - 1].lap;
    if (stage.lap !== before) assert.equal(stage.arms, 'grip', `${stage.name}: the laptop moves only in his hands`);
    if (stage.arms === 'grip') assert.equal(stage.stand, false, `${stage.name}: he carries it sitting down`);
  });
  const first = STRETCH_STAGES[0];
  assert.deepEqual([first.lap, first.arms, first.stand], [0, 'grip', false], 'the first thing he does is take hold of it where it is');
  const last = STRETCH_STAGES[STRETCH_STAGES.length - 1];
  assert.deepEqual([last.lap, last.arms, last.mode, last.stand, last.reach, last.lean], [0, 'type', 0, false, false, 0], 'the last thing is his hand back on the keys, sitting as he began');
  // it goes out in his hands and comes back in them
  assert.deepEqual(STRETCH_STAGES.filter(s => s.arms === 'grip').map(s => s.lap), [0, 1, 1, 0]);
  // the lean goes back, then forward, then back, so it reads as a stretch and not a stuck pose
  const leans = STRETCH_STAGES.filter(s => s.reach).map(s => s.lean);
  assert.ok(Math.max(...leans) >= 8 && Math.min(...leans) < 0);
});

test('it is a stretch of a few seconds, he is seen typing for a moment before it starts, and a carry is slow enough to see', () => {
  assert.equal(STRETCH_TOTAL_MS, STRETCH_STAGES.reduce((sum, s) => sum + s.ms, 0));
  assert.ok(STRETCH_TOTAL_MS >= 6000 && STRETCH_TOTAL_MS <= 11000, String(STRETCH_TOTAL_MS));
  assert.ok(STRETCH_START_DELAY_MS >= 1000 && STRETCH_START_DELAY_MS <= 4000);
  const carrying = STRETCH_STAGES.filter(s => s.name === 'set it down' || s.name === 'bring it back').map(s => s.ms);
  assert.ok(carrying.every(ms => ms >= 1000), 'the hands visibly go with the laptop');
});

test('what is drawn at a stage comes from the list, and anything that is not a stage is just sitting at the laptop', () => {
  const rest = { stretching: false, rigging: false, down: false, stand: false, reach: false, lean: 0, rig: { lap: 0, grip: 0, mode: 0, ms: RIG_SETTLE_MS } };
  for (const stage of [-1, -5, STRETCH_STAGES.length, 99, 1.5, NaN, Infinity]) assert.deepEqual(stretchFlags(stage), rest, String(stage));
  assert.ok(RIG_SETTLE_MS >= 200 && RIG_SETTLE_MS <= 800, 'a stretch cut short glides back to rest');
  // the first stage: hands on the laptop, which is still on his lap
  assert.deepEqual(stretchFlags(0), { stretching: true, rigging: true, down: false, stand: false, reach: false, lean: 0, rig: { lap: 0, grip: 1, mode: 0, ms: 450 } });
  // set it down: the laptop goes to the floor in his hands
  assert.deepEqual(stretchFlags(1).rig, { lap: 1, grip: 1, mode: 0, ms: 1200 });
  // let go: still the carrying arms, hands off, the left arm to hang
  assert.deepEqual([stretchFlags(2).rigging, stretchFlags(2).rig.grip, stretchFlags(2).rig.mode], [true, 0, 1]);
  // standing: the ordinary arms, and the laptop stays where it was put
  const rise = stretchFlags(3);
  assert.deepEqual([rise.rigging, rise.down, rise.stand, rise.rig.lap, rise.rig.grip], [false, true, true, 1, 0]);
  assert.deepEqual(stretchFlags(5), { stretching: true, rigging: false, down: true, stand: true, reach: true, lean: 11, rig: { lap: 1, grip: 0, mode: 1, ms: 800 } });
  const last = stretchFlags(STRETCH_STAGES.length - 1);
  assert.deepEqual([last.rigging, last.down, last.rig.lap, last.rig.grip, last.rig.mode], [true, false, 0, 0, 0]);
  STRETCH_STAGES.forEach((stage, i) => {
    const flags = stretchFlags(i);
    assert.deepEqual([flags.stand, flags.reach, flags.lean, flags.rig.lap, flags.rig.ms], [stage.stand, stage.reach, stage.lean, stage.lap, stage.ms]);
    assert.equal(flags.rigging, stage.arms !== 'free');
    assert.equal(flags.rig.grip, stage.arms === 'grip' ? 1 : 0);
  });
});

test('Mini Patchy plays it: once, when he is idle, never for someone who asks for less motion, and any other mood cuts it off', () => {
  assert.match(mini, /import \{ STRETCH_STAGES, STRETCH_START_DELAY_MS, stretchFlags \} from '\.\/patchyStretch';/);
  assert.match(mini, /const \[stage, setStage\] = useState\(-1\);/);
  assert.match(mini, /const \[stretchedOnce, setStretchedOnce\] = useState\(false\);/);
  assert.match(mini, /const idle = mood === 'idle';/);
  assert.match(mini, /if \(!idle \|\| hello \|\| stretchedOnce \|\| !motionAllowed\(\)\) return undefined;/);
  assert.match(mini, /timer = window\.setTimeout\(\(\) => step\(0\), STRETCH_START_DELAY_MS\);/);
  assert.match(mini, /else \{ setStretchedOnce\(true\); setStage\(-1\); \}/);
  assert.match(mini, /return \(\) => \{ window\.clearTimeout\(timer\); setStage\(-1\); \};\n  \}, \[idle, hello, stretchedOnce\]\);/);
  assert.match(mini, /const stretch = stretchFlags\(idle && !tada \? stage : -1\);/);
  assert.match(read('./patchyLook.ts'), /export function motionAllowed\(\)/);
});

test('while he stretches his arms and face follow the script: the free arm hangs, then goes up with the cheer pose, the eyes squeeze shut, and the idle wave is off', () => {
  assert.match(mini, /stretch\.stretching \? \(stretch\.reach \? 'cheer' : 'hang'\)/);
  assert.match(mini, /const wave = mood === 'idle' && !stretch\.stretching && energy !== 'subdued' && budget !== 'throttled';/);
  assert.match(mini, /<PatchyHead [^>]*scrunched=\{stretch\.reach\}[^>]*\/>/);
  assert.match(mini, /\$\{stretch\.stretching \? ' mp-stretching' : ''\}\$\{stretch\.down \? ' mp-down' : ''\}\$\{stretch\.rigging \? ' mp-rigging' : ''\}\$\{stretch\.stand \? ' mp-stand' : ''\}\$\{stretch\.reach \? ' mp-reach' : ''\}/);
  assert.match(mini, /style=\{stretch\.stretching \? \(\{ '--mp-lean': stretch\.lean \} as CSSProperties\) : undefined\}/);
});

test('the laptop and the carrying arms are driven by the rig, from one state, and drawn in layers that can move on their own', () => {
  assert.match(mini, /useStretchRig\(stretch\.rig, rig => \{/);
  assert.match(mini, /lapRef\.current\?\.setAttribute\('transform', `translate\(\$\{rig\.lap\[0\]\} \$\{rig\.lap\[1\]\}\)`\);/);
  assert.match(mini, /leftArmRef\.current\?\.setAttribute\('d', armPath\(SHOULDER_LEFT, rig\.left\)\);/);
  assert.match(mini, /rightArmRef\.current\?\.setAttribute\('d', armPath\(SHOULDER_RIGHT, rig\.right\)\);/);
  assert.match(mini, /leftHandRef\.current\?\.setAttribute\('cx', String\(rig\.left\[4\]\)\);/);
  assert.match(mini, /leftHandRef\.current\?\.setAttribute\('cy', String\(rig\.left\[5\]\)\);/);
  assert.match(mini, /rightHandRef\.current\?\.setAttribute\('cx', String\(rig\.right\[4\]\)\);/);
  assert.match(mini, /rightHandRef\.current\?\.setAttribute\('cy', String\(rig\.right\[5\]\)\);/);
  assert.equal((mini.match(/<g className="mp-rise">/g) ?? []).length, 2, 'two layers rise together: legs and torso, and (after the laptop) the arms and head');
  assert.equal((mini.match(/<g className="mp-lap" ref=\{lapRef\}>/g) ?? []).length, 1);
  assert.ok(mini.indexOf('<g className="mp-lap"') > mini.indexOf('className="mp-leg mp-leg-l"'));
  assert.ok(mini.indexOf('<g className="mp-lap"') < mini.indexOf('<g className="mp-arm-typing">'));
  assert.ok(mini.indexOf('<g className="mp-arm-typing">') < mini.indexOf('<PatchyHead'));
  assert.ok(mini.indexOf('<g className="mp-rig mp-rig-back">') < mini.indexOf('<g className="mp-lap"'), 'the carrying arms are drawn before the laptop, so behind it (patchyChest.test.mjs)');
  assert.match(mini, /<g className="mp-rarm">\s+<PatchyRightArm /);
  assert.match(mini, /<g className="mp-arm-free mp-arm-hang">/);
  assert.match(mini, /<g className="mp-arm-free mp-arm-up">/);
  // the laptop's polygons and lines are all inside its layer: none left in front of him
  const lap = mini.slice(mini.indexOf('<g className="mp-lap"'), mini.indexOf('<g className="mp-rise">', mini.indexOf('<g className="mp-lap"')));
  assert.equal((lap.match(/<polygon /g) ?? []).length, 4);
  assert.equal((mini.match(/<polygon /g) ?? []).length, 4);
  assert.ok(lap.includes('mp-cursor'));
});

test('the stylesheet: he rises by the length of his legs and leans from the hips, the ordinary arms and the carrying arms swap places with a fade, and the legs stop tapping while he stands', () => {
  assert.match(css, /\.mp-stretching svg \{ overflow: visible; \}/);
  assert.match(css, /\.mp-rise \{ transform-origin: 50px 86px; transition: translate 0\.8s ease-in-out, rotate 0\.7s ease-in-out; \}/);
  assert.match(css, /\.mp-stand \.mp-rise \{ translate: 0 -12px; \}/);
  assert.match(css, /\.mp-stretching \.mp-rise \{ rotate: calc\(var\(--mp-lean, 0\) \* 1deg\); \}/);
  assert.match(css, /\.mp-stand \.mp-leg \{ animation: none; \}/);
  // the laptop is moved by script now (the rig), not by a stylesheet slide
  assert.doesNotMatch(css, /\.mp-lap \{ transition/);
  assert.doesNotMatch(css, /\.mp-down \.mp-lap/);
  assert.match(css, /\.mp-down \.mp-arm-typing \{ opacity: 0; \}/);
  assert.match(css, /\.mp-arm-free \{ opacity: 0; transition: opacity 0\.3s ease; \}/);
  assert.match(css, /\.mp-down \.mp-arm-hang \{ opacity: 1; \}/);
  assert.match(css, /\.mp-reach \.mp-arm-hang \{ opacity: 0; \}/);
  assert.match(css, /\.mp-reach \.mp-arm-up \{ opacity: 1; \}/);
  assert.match(css, /\.mp-rig \{ opacity: 0; transition: opacity 0\.3s ease; \}/);
  assert.match(css, /\.mp-rarm \{ transition: opacity 0\.3s ease; \}/);
  assert.match(css, /\.mp-rigging \.mp-rig \{ opacity: 1; \}/);
  assert.match(css, /\.mp-rigging \.mp-arm-typing, \.mp-rigging \.mp-arm-hang, \.mp-rigging \.mp-rarm \{ opacity: 0; \}/);
  assert.ok(css.indexOf('.mp-rise {') > css.indexOf('@keyframes mp-leg-tap'), "after the legs' own animation");
});

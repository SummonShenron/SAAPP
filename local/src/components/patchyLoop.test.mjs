// Run with: node --test src/components/patchyLoop.test.mjs   (from local/)
// Patchy in the tool loop (2026-11-18): from a turn's second tool step, two little gears turn beside his head. The rule is patchyLook.ts inToolLoop, the drawing patchyGear.ts, MiniPatchy.tsx and MiniPatchy.css.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { gearPath } from './patchyGear.ts';
import { TOOL_LOOP_AFTER, inToolLoop } from './patchyLook.ts';

const read = name => readFileSync(new URL(name, import.meta.url), 'utf8').replace(/\r\n/g, '\n');
const mini = read('./MiniPatchy.tsx');
const css = read('./__styles__/MiniPatchy.css');
const chat = read('../pages/Chat.tsx');
const mood = read('../hooks/usePatchyMood.ts');

test('one tool is just a tool: the loop starts at the second', () => {
  assert.equal(TOOL_LOOP_AFTER, 2);
  for (const n of [0, 1, -1, NaN, -Infinity]) assert.equal(inToolLoop(n), false, String(n));
  for (const n of [2, 3, 10]) assert.equal(inToolLoop(n), true, String(n));
});

test('a gear is a closed outline with four points to a tooth, between its inner and outer radius', () => {
  const path = gearPath(12, 52, 9, 7, 9);
  assert.ok(path.startsWith('M ') && path.endsWith(' Z'));
  assert.equal((path.match(/[ML] /g) ?? []).length, 36, '9 teeth, 4 points each');
  const points = [...path.matchAll(/[ML] (-?[\d.]+) (-?[\d.]+)/g)].map(m => [Number(m[1]), Number(m[2])]);
  const radii = points.map(([x, y]) => Math.hypot(x - 12, y - 52));
  assert.ok(Math.abs(Math.max(...radii) - 9) < 0.05 && Math.abs(Math.min(...radii) - 7) < 0.05, 'the teeth stand at the outer radius and the gaps at the inner');
  assert.equal(path, gearPath(12, 52, 9, 7, 9), 'the same every time');
  assert.notEqual(path, gearPath(12, 52, 9, 7, 7));
  assert.equal((gearPath(0, 0, 5, 4, 1).match(/[ML] /g) ?? []).length, 12, 'fewer than three teeth is three');
});

test('the gears sit to the left of his head and inside his drawing, turn opposite ways, and the small one goes round faster by the ratio of their teeth', () => {
  const big = mini.match(/GEAR_BIG = gearPath\((\d+), (\d+), ([\d.]+), ([\d.]+), (\d+)\);/);
  const small = mini.match(/GEAR_SMALL = gearPath\((\d+), ([\d.]+), ([\d.]+), ([\d.]+), (\d+)\);/);
  assert.ok(big && small);
  const [bx, by, bo, , bt] = big.slice(1).map(Number);
  const [sx, sy, so, , st] = small.slice(1).map(Number);
  for (const [x, , outer] of [[bx, by, bo], [sx, sy, so]]) {
    assert.ok(x - outer >= 0, 'inside the drawing');
    assert.ok(x + outer < 23, 'clear of his head (the left ear is at x 23)');
  }
  assert.ok(sy + so < by - bo + 4 && Math.abs(by - sy) < bo + so + 3, 'one above the other, close enough to read as meshed');
  // the stylesheet turns each about its own centre
  assert.match(css, new RegExp(`\\.mp-gear-big \\{ transform-origin: ${bx}px ${by}px; \\}`));
  assert.match(css, new RegExp(`\\.mp-gear-small \\{ transform-origin: ${sx}px ${sy}px; animation-duration: ([\\d.]+)s; animation-direction: reverse; \\}`));
  const bigSeconds = Number(css.match(/\.mp-gear \{ transform-box: view-box; animation: mp-gear-spin ([\d.]+)s linear infinite; \}/)[1]);
  const smallSeconds = Number(css.match(/animation-duration: ([\d.]+)s; animation-direction: reverse; \}/)[1]);
  assert.ok(Math.abs(bigSeconds * st / bt - smallSeconds) < 0.1, `a gear with ${st} teeth meshed with one of ${bt} turns ${bt / st} times as fast`);
  assert.match(css, /@keyframes mp-gear-spin \{ to \{ transform: rotate\(360deg\); \} \}/);
  assert.match(css, /@media \(prefers-reduced-motion: reduce\) \{\s+\.mp-gear \{ animation: none; \}\s+\}/);
  assert.match(css, /\.mp-looping \.mp-antenna \{ animation-duration: 0\.45s; \}/);
});

test('Mini Patchy shows them only while a turn is running and the page says it is in the loop, and fades them like his other expressions', () => {
  assert.match(mini, /const looping = props\.loop === true && mood === 'active';/);
  assert.match(mini, /<g className=\{`mp-fade mp-gears\$\{looping \|\| victory \? ' mp-on' : ''\}\$\{victory \? ' mp-gears-pop' : ''\}`\}>/);          // (the victory hop shows them too: patchyVictory.test.mjs)
  assert.match(mini, /\$\{looping \? ' mp-looping' : ''\}/);
  assert.equal((mini.match(/mp-gear /g) ?? []).length, 2);
  assert.match(mood, /loop\?: boolean;/);
});

test('the chat counts the tool steps of a turn, tells Patchy when it is in the loop, and starts again with every turn', () => {
  assert.match(chat, /import \{ TURN_SENT_STATUS, budgetFromLevel, inToolLoop, lookForStep \} from '\.\.\/components\/patchyLook';/);
  assert.match(chat, /const \[toolSteps, setToolSteps\] = useState\(0\);/);
  assert.match(chat, /if \(item\.tool\) setToolSteps\(n => n \+ 1\);/);
  assert.equal((chat.match(/setAgentStatus\(''\); setAgentTool\(''\); setToolSteps\(0\);/g) ?? []).length, 3, 'every place the status is cleared');
  assert.match(chat, /setAgentStatus\(TURN_SENT_STATUS\);\n\s+setAgentTool\(''\);\n\s+setToolSteps\(0\);/);
  assert.match(chat, /loop=\{loading && inToolLoop\(toolSteps\)\}/);
});

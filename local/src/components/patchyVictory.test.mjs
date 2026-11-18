// Run with: node --test src/components/patchyVictory.test.mjs   (from local/)
// Patchy's victory hop (2026-11-18): when a turn that went round the tool loop ends well he crouches, springs high, lands squashed and bounces once more, one arm up, and the two gears fly off him.
// The rule is patchyLook.ts loopVictory; MiniPatchy.tsx draws it, MiniPatchy.css animates it, Chat.tsx says when.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { CELEBRATE_MS, loopVictory } from './patchyLook.ts';

const read = name => readFileSync(new URL(name, import.meta.url), 'utf8').replace(/\r\n/g, '\n');
const mini = read('./MiniPatchy.tsx');
const css = read('./__styles__/MiniPatchy.css');
const chat = read('../pages/Chat.tsx');
const mood = read('../hooks/usePatchyMood.ts');

test('the victory hop is for a celebration after a tool loop, and only for someone with the energy for it', () => {
  assert.equal(loopVictory('done', true, 'open'), true);
  assert.equal(loopVictory('done', false, 'open'), false, 'an ordinary turn keeps the ordinary celebration');
  assert.equal(loopVictory('done', undefined, 'open'), false);
  assert.equal(loopVictory('done', true, 'easing'), false, 'a gentler celebration when they have only started to lift');
  assert.equal(loopVictory('done', true, 'subdued'), false, 'and a quiet nod when they are low');
  for (const m of ['idle', 'active', 'error', 'drowsy', 'watching', 'laugh', 'praise', 'sad', 'asleep', 'soft']) assert.equal(loopVictory(m, true, 'open'), false, m);
});

test('the hop fits inside his celebration, and the gears are gone by the time it ends', () => {
  const hop = Number(css.match(/\.mp-victory \.mp-bob \{[^}]*animation: mp-victory ([\d.]+)s ease-out 1;/)[1]) * 1000;
  const pop = Number(css.match(/\.mp-gears-pop \{ animation: mp-gears-pop ([\d.]+)s ease-out forwards; \}/)[1]) * 1000;
  assert.ok(hop <= CELEBRATE_MS && hop >= 1000, `${hop} of ${CELEBRATE_MS}`);
  assert.ok(pop <= CELEBRATE_MS);
});

test('the keyframes: crouch, a high spring that is taller than the plain hop, a squashed landing, one more bounce, and back to rest', () => {
  const frames = css.match(/@keyframes mp-victory \{([\s\S]*?)\n\}/)[1];
  const at = pct => frames.match(new RegExp(`${pct}% \\{ transform: translateY\\((-?[\\d.]+)(?:px)?\\) scale\\(([\\d.]+), ([\\d.]+)\\); \\}`)).slice(1).map(Number);
  assert.deepEqual(at(0), [0, 1, 1]);
  assert.deepEqual(at(100), [0, 1, 1], 'ends exactly where it began, so nothing pops when it stops');
  const [crouchY, crouchX, crouchH] = at(12);
  assert.ok(crouchY > 0 && crouchX > 1 && crouchH < 1, 'down and squashed before the jump');
  const [apexY, apexX, apexH] = at(34);
  assert.ok(apexY <= -12 && apexX < 1 && apexH > 1, 'high and stretched at the top');
  const plain = Number(css.match(/@keyframes mp-hop \{[\s\S]*?50% \{ transform: translateY\((-?[\d.]+)px\)/)[1]);
  assert.ok(apexY < plain, 'higher than the ordinary hop');
  const [landY, landX, landH] = at(52);
  assert.ok(landY === 0 && landX > 1 && landH < 1, 'squashed when he lands');
  assert.ok(at(66)[0] < 0 && at(82)[0] === 0, 'one smaller bounce, then still');
  // squash and stretch pivot on the input's edge (his torso rests on y 86)
  assert.match(css, /\.mp-victory \.mp-bob \{ transform-origin: 50px 86px; transform-box: view-box;/);
});

test('the gears fly up and away and fade, and he is not cut off at the top of his drawing while he jumps', () => {
  assert.match(css, /@keyframes mp-gears-pop \{\s+0% \{ translate: 0 0; opacity: 1; \}\s+100% \{ translate: -4px -26px; opacity: 0; \}\s+\}/);
  assert.match(css, /\.mp-victory svg \{ overflow: visible; \}/);
  assert.match(css, /@media \(prefers-reduced-motion: reduce\) \{\s+\.mp-victory \.mp-bob, \.mp-gears-pop \{ animation: none; \}\s+\}/);
  assert.ok(css.indexOf('.mp-victory .mp-bob') > css.indexOf('.mp-done .mp-bob { animation: mp-hop'), 'after the plain hop it replaces');
});

test('Mini Patchy: the victory raises an arm, shows the gears (even from a turn that was not in the loop at the last step) and says so in a class', () => {
  assert.match(mini, /import \{ loopVictory, motionAllowed, patchyRootClass, patchyTitle, type ArmPose \} from '\.\/patchyLook';/);
  assert.match(mini, /const victory = loopVictory\(mood, props\.loopCheer, energy\);/);
  assert.match(mini, /: victory \? 'cheer'/);
  assert.match(mini, /\$\{victory \? ' mp-victory' : ''\}/);
  assert.match(mini, /mp-gears\$\{looping \|\| victory \? ' mp-on' : ''\}\$\{victory \? ' mp-gears-pop' : ''\}/);
  assert.match(mood, /loopCheer\?: boolean;/);
});

test('the chat remembers whether the turn that just ended well had been in the loop, from a reference, because the code that ends a turn holds an old copy of the state', () => {
  assert.match(chat, /const \[patchyLoopCheer, setPatchyLoopCheer\] = useState\(false\);/);
  assert.match(chat, /const toolStepsRef = useRef\(toolSteps\);/);
  assert.match(chat, /useEffect\(\(\) => \{ toolStepsRef\.current = toolSteps; \}, \[toolSteps\]\);/);
  assert.match(chat, /else if \(!stoppedByUser\) \{ setPatchyCheer\(c => c \+ 1\); setPatchyLoopCheer\(inToolLoop\(toolStepsRef\.current\)\); \}/);
  assert.match(chat, /loopCheer=\{patchyLoopCheer\}/);
  // it is read before the count is cleared on the next line
  const end = chat.indexOf('setPatchyLoopCheer(inToolLoop(toolStepsRef.current))');
  assert.ok(end > 0 && chat.indexOf("setAgentStatus(''); setAgentTool(''); setToolSteps(0);", end) > end);
});

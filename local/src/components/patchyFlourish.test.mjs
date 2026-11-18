// Run with: node --test src/components/patchyFlourish.test.mjs   (from local/)
// Mini Patchy's three small flourishes (2026-11-18): a hello wave when an older conversation is opened (then his stretch), a ta-da when a long plan finishes, and the gears shifting when you steer a running tool loop.
// Rules and timings in patchyFlourish.ts; MiniPatchy.tsx plays them, MiniPatchy.css draws them, Chat.tsx says when.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { HELLO_MS, HELLO_WAVE_SECONDS, PLAN_LONG_STEPS, SPARKLES, TADA_AFTER_MS, TADA_MS, isLongPlanDone, sparklePath } from './patchyFlourish.ts';
import { CELEBRATE_MS, STEER_MS } from './patchyLook.ts';
import { STRETCH_START_DELAY_MS } from './patchyStretch.ts';

const read = name => readFileSync(new URL(name, import.meta.url), 'utf8').replace(/\r\n/g, '\n');
const mini = read('./MiniPatchy.tsx');
const css = read('./__styles__/MiniPatchy.css');
const chat = read('../pages/Chat.tsx');
const mood = read('../hooks/usePatchyMood.ts');
const parts = read('./PatchyParts.tsx');

// ---- the ta-da ----

test('a plan is long at three steps, and it is the finished ones that get a ta-da', () => {
  assert.equal(PLAN_LONG_STEPS, 3);
  assert.equal(isLongPlanDone('done', 3), true);
  assert.equal(isLongPlanDone('done', 12), true);
  assert.equal(isLongPlanDone('done', 2), false, 'a short plan is not worth one');
  assert.equal(isLongPlanDone('done', 0), false);
  assert.equal(isLongPlanDone('approved', 5), false);
  assert.equal(isLongPlanDone('proposed', 5), false);
  assert.equal(isLongPlanDone(undefined, 5), false);
  assert.equal(isLongPlanDone(null, 5), false);
  assert.equal(isLongPlanDone('done', NaN), false);
});

test('the ta-da waits for the turn\'s own celebration to be over, and its pose and sparkles end inside its time', () => {
  assert.ok(TADA_AFTER_MS >= CELEBRATE_MS, 'after the celebration of the turn that finished the plan');
  const pose = Number(css.match(/\.mp-tada \.mp-bob \{[^}]*animation: mp-tada ([\d.]+)s ease-in-out 1;/)[1]) * 1000;
  assert.equal(pose, TADA_MS);
  const sparkle = Number(css.match(/\.mp-sparkle \{[^}]*animation: mp-sparkle ([\d.]+)s ease-out both;/)[1]) * 1000;
  const last = Math.max(...SPARKLES.map(s => s.delay)) * 1000;
  assert.ok(last + sparkle <= TADA_MS, 'the last sparkle has faded before he puts his arms down');
});

test('the sparkles are inside and around his drawing, big enough to see at his size, each a little after the one before', () => {
  assert.equal(SPARKLES.length, 6);
  SPARKLES.forEach((s, i) => {
    assert.ok(s.x - s.r >= 0 && s.x + s.r <= 100 && s.y - s.r >= 0, `sparkle ${i} stays inside the drawing`);
    assert.ok(s.r >= 4, `sparkle ${i} is at least 4 units (about 2px) from centre to point`);
    if (i > 0) assert.ok(s.delay > SPARKLES[i - 1].delay);
  });
  // none sits on his face (the head is x 28 to 72, y 20 to 54)
  for (const s of SPARKLES) assert.ok(s.x + s.r < 28 || s.x - s.r > 72 || s.y + s.r < 20, 'clear of his head');
});

test('a sparkle is a four-pointed star with curved-in sides, drawn about its centre', () => {
  const path = sparklePath(50, 50, 6);
  assert.ok(path.startsWith('M 50 44 ') && path.endsWith(' Z'));
  assert.equal((path.match(/Q/g) ?? []).length, 4);
  for (const point of ['50 44', '56 50', '50 56', '44 50']) assert.ok(path.includes(point), point);
  assert.equal(sparklePath(0, 0, 3), sparklePath(0, 0, 3));
});

test('Mini Patchy notices a plan turning finished while rendering, waits, then does it, and only for someone who is not low', () => {
  assert.match(mini, /const \[seenPlan, setSeenPlan\] = useState\(props\.planDone === true\);/);
  assert.match(mini, /const \[tadaStage, setTadaStage\] = useState<'off' \| 'wait' \| 'on'>\('off'\);/);
  assert.match(mini, /if \(\(props\.planDone === true\) !== seenPlan\) \{\s+setSeenPlan\(props\.planDone === true\);\s+if \(props\.planDone === true && motionAllowed\(\)\) setTadaStage\('wait'\);/);
  assert.match(mini, /tadaStage === 'wait' \? TADA_AFTER_MS : TADA_MS/);
  assert.match(mini, /const tada = tadaStage === 'on' && idle && energy !== 'subdued';/);
  assert.match(mini, /tada \? 'cheer'\n/);
  assert.match(mini, /\$\{tada \? ' mp-tada' : ''\}/);
  assert.match(mini, /\{tada && \(\s+<g className="mp-sparkles mp-accent" aria-hidden="true">/);
  assert.match(mini, /SPARKLES\.map\(\(s, i\) => <path key=\{i\} className="mp-sparkle" d=\{sparklePath\(s\.x, s\.y, s\.r\)\}/);
  assert.match(mood, /planDone\?: boolean;/);
});

test('the ta-da: both arms up, he rises and holds, sparkles from nothing and back to nothing, nothing for reduced motion', () => {
  assert.match(css, /\.mp-tada svg \{ overflow: visible; \}/);
  assert.match(css, /\.mp-tada \.mp-arm-typing \{ opacity: 0; \}\n\.mp-tada \.mp-arm-up \{ opacity: 1; \}/);
  const frames = css.match(/@keyframes mp-tada \{([\s\S]*?)\n\}/)[1];
  assert.match(frames, /0%, 100% \{ transform: translateY\(0\) scale\(1, 1\); \}/, 'ends where it began');
  assert.match(frames, /38%, 62% \{ transform: translateY\(-4px\)/, 'held at the top');
  const sparkle = css.match(/@keyframes mp-sparkle \{([\s\S]*?)\n\}/)[1];
  assert.match(sparkle, /0% \{ opacity: 0; transform: scale\(0\)/);
  assert.match(sparkle, /100% \{ opacity: 0; /);
  assert.match(css, /\.mp-sparkle \{ opacity: 0; /, 'invisible unless animated');
  assert.match(css, /\.mp-steered\.mp-looping \.mp-gears, \.mp-hello \.mp-bob, \.mp-tada \.mp-bob, \.mp-sparkle \{ animation: none; \}/);
});

test('the chat says a long plan is done only where the plan card is shown (a plan the person can edit)', () => {
  assert.match(chat, /import \{ isLongPlanDone \} from '\.\.\/components\/patchyFlourish';/);
  assert.match(chat, /planDone=\{!!codePlan && workspace\.canEdit && isLongPlanDone\(codePlan\.state, codePlan\.steps\.length\)\}/);
});

// ---- the hello ----

test('his hello is one trip through the waving arm\'s cycle, and the stretch follows it', () => {
  assert.equal(HELLO_MS, HELLO_WAVE_SECONDS * 1000);
  assert.match(parts, /const WAVE_TIMES = \[0, 0\.1, 0\.2, 0\.3, 0\.4, 0\.5, 0\.6, 1\];/, 'the cycle the hello restarts');
  assert.ok(HELLO_WAVE_SECONDS < 8, 'quicker than his idle wave, which takes 8 seconds a cycle');
  assert.ok(STRETCH_START_DELAY_MS >= 1000, 'and a moment after it, before the stretch');
});

test('Mini Patchy notices a greeting while rendering, waves from the start of the cycle, re-arms the stretch and holds it until the hello is over', () => {
  assert.match(mini, /const \[seenGreet, setSeenGreet\] = useState\(props\.greet \?\? 0\);/);
  assert.match(mini, /if \(\(props\.greet \?\? 0\) !== seenGreet\) \{\s+setSeenGreet\(props\.greet \?\? 0\);\s+if \(motionAllowed\(\)\) \{ setHello\(true\); setStretchedOnce\(false\); setStage\(-1\); \}/);
  assert.match(mini, /const timer = window\.setTimeout\(\(\) => setHello\(false\), HELLO_MS\);/);
  assert.match(mini, /const greeting = hello && idle;/);
  assert.match(mini, /const waveDur = greeting \? HELLO_WAVE_SECONDS : energy === 'easing' \? 14 : 8;/);
  assert.match(mini, /\$\{greeting \? ' mp-hello' : ''\}/);
  assert.match(mini, /\}, \[idle, hello, stretchedOnce\]\);/);
  assert.match(mood, /greet\?: number;/);
  assert.match(css, /\.mp-hello \.mp-bob \{ animation: mp-hello-hop 0\.5s ease-out 1; \}/);
});

test('the chat greets only when an older conversation with something in it is opened, not a new one', () => {
  assert.match(chat, /const \[patchyGreet, setPatchyGreet\] = useState\(0\);/);
  assert.match(chat, /setHasChatted\(restored\.some\(m => m\.sender === 'user'\)\);\n\s+if \(restored\.some\(m => m\.sender === 'user'\)\) setPatchyGreet\(c => c \+ 1\);/);
  assert.match(chat, /greet=\{patchyGreet\}/);
  assert.equal((chat.match(/setPatchyGreet\(/g) ?? []).length, 1, 'only switchConversation bumps it');
});

// ---- steering a tool loop ----

test('steering a running tool loop makes the gears swell and speed up for as long as his nod lasts', () => {
  const shift = css.match(/\.mp-steered\.mp-looping \.mp-gears \{[^}]*animation: mp-gear-shift ([\d.]+)s ease-out (\d+);/);
  assert.ok(shift);
  assert.ok(Number(shift[1]) * Number(shift[2]) * 1000 <= STEER_MS + 200, 'about as long as the steer nod');
  assert.match(css, /@keyframes mp-gear-shift \{\s+0%, 100% \{ transform: scale\(1\); \}\s+40% \{ transform: scale\(1\.35\); \}\s+\}/);
  assert.match(css, /\.mp-steered\.mp-looping \.mp-gear \{ animation-duration: 0\.5s; \}/);
  assert.match(css, /\.mp-steered\.mp-looping \.mp-gears \{ transform-box: view-box; transform-origin: 12px 43px;/, 'about the middle of the pair');
});

// Run with: node --experimental-test-module-mocks --test src/components/graphLoader.test.mjs   (from local/; Node 22.18+ runs the TypeScript module directly)
// The graph loader's rules (components/graphLoader.ts): the vertical graph (before the first step and under the newest step), how it lands in the next step, and which arrival is which.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import {
  ARRIVE_DELAY_MS, GAP_OVERHANG_PX, GAP_SPACE_PX, GAP_STUB_END_PX, LANDING_STAGGER_MS, LAND_END_AT, LAND_LEAVE_AT, START_BOX, START_FRAMES, START_PASSES, START_ROUTES, START_SIDES, START_SPACE_PX, START_TRUNK,
  STATIC_START_FRAME, TICK_MS, arrivalOf, downPath, firstStepLanded, startLit, startWay,
} from './graphLoader.ts';

test('the reduced-motion graph holds on a beat with part of the way lit and nothing faded', () => {
  const still = START_FRAMES[STATIC_START_FRAME];
  assert.equal(still.fade, false);
  assert.ok(startLit(still).size > 1);
});

test('a downward branch is drawn as a flow-chart curve that leaves and arrives vertically', () => {
  assert.equal(downPath(7, 6, 24, 15), 'M 7 6 C 7 10.5, 24 10.5, 24 15');
});

test('the graph is a trunk on the line the dots sit on, with one side node to the right in each of three stretches and two ways down through each', () => {
  assert.equal(START_TRUNK.length, 4);
  assert.ok(START_TRUNK.every(([x]) => x === 7), 'the trunk is at x = 7, where every step dot is centred (the dot is 14px wide)');
  assert.ok(START_TRUNK.every(([, y], i, all) => i === 0 || y > all[i - 1][1]), 'and runs downward');
  assert.equal(START_SIDES.length, 3);
  START_SIDES.forEach(([x, y], k) => assert.ok(x > 7 && y > START_TRUNK[k][1] && y < START_TRUNK[k + 1][1], `side node ${k} lies to the right, between its two trunk nodes`));
  assert.equal(START_ROUTES.length, 3);
  for (const [seg, ways] of START_ROUTES.entries()) {
    assert.deepEqual(ways[0], [`ts${seg}`, `s${seg}`, `st${seg}`, `t${seg + 1}`]);          // out through the side node
    assert.deepEqual(ways[1], [`tt${seg}`, `t${seg + 1}`]);                                // or straight down
  }
  assert.deepEqual(START_PASSES, [[0, 1, 0], [1, 0, 1]]);
  START_PASSES[0].forEach((route, seg) => assert.notEqual(route, START_PASSES[1][seg]));
});

test('the graph\'s beats take each stretch in order, light what has been travelled, and fade at the end of each pass', () => {
  const frames = START_FRAMES.filter(f => f.pass === 0);
  assert.deepEqual(frames.map(f => [f.seg, f.lit]).slice(0, 5), [[0, 1], [0, 2], [0, 3], [0, 4], [0, 4]]);
  assert.equal(frames.at(-1).fade, true);
  assert.equal(frames.filter(f => f.fade).length, 1);
  // what is lit is cumulative: the first node, the whole of the earlier stretches' ways, and what has been reached in this one
  assert.deepEqual([...startLit({ pass: 0, seg: 0, lit: 1, fade: false })].sort(), ['t0', 'ts0']);
  assert.deepEqual([...startLit({ pass: 0, seg: 1, lit: 1, fade: false })].sort(), ['s0', 'st0', 't0', 't1', 'ts0', 'tt1']);
  assert.deepEqual([...startLit({ pass: 1, seg: 1, lit: 2, fade: false })].sort(), ['s1', 't0', 't1', 'ts1', 'tt0']);          // the first stretch straight down, the second through its side node as far as it has got
  assert.equal(START_FRAMES.length, (5 + 3 + 5 + 1) + (3 + 5 + 3 + 1));
});

test('when a step lands the light carries on down the pass\'s own way, detours included, to the last node, and not all of the graph is on that way, so it keeps its shape', () => {
  const everything = new Set(['t0', ...START_ROUTES.flat(2)]);
  for (const pass of [0, 1]) {
    const way = startWay(pass);
    assert.equal(way[0], 't0');
    assert.equal(way.at(-1), 't3');
    assert.equal(new Set(way).size, way.length, 'no element twice');
    for (const trunk of ['t0', 't1', 't2', 't3']) assert.ok(way.includes(trunk), `${trunk} is on the way`);
    assert.ok(way.length < everything.size, 'some of the graph (the ways not taken) is not on the way: it stays, dimmed, and the graph is not straightened');
    assert.ok(way.every(id => everything.has(id)));
  }
  assert.equal(startWay(0).length, 1 + 4 + 2 + 4);
  assert.equal(startWay(1).length, 1 + 2 + 4 + 2);
  assert.ok(startWay(0).includes('s0') && !startWay(0).includes('s1'), 'the first pass detours in the first and third stretch');
  assert.ok(startWay(1).includes('s1') && !startWay(1).includes('s0'), 'the second pass in the second');
  assert.equal(LANDING_STAGGER_MS * (startWay(0).length - 1) < ARRIVE_DELAY_MS + 100, true);          // the light has all but reached the node by the time the node pops in
});

test('the first step lands exactly when the list goes from empty to not empty', () => {
  assert.equal(firstStepLanded(0, 1), true);
  assert.equal(firstStepLanded(0, 3), true);          // a burst of steps still lands the start graph once
  assert.equal(firstStepLanded(1, 2), false);
  assert.equal(firstStepLanded(2, 0), false);          // a new turn emptying the list is not a landing
  assert.equal(firstStepLanded(0, 0), false);
});

const ids = (...names) => names.map(id => ({ id }));

test('the graph under a step lands in the next step only when that step arrives right after the one that was showing it, and goes by ids so that the five drawn while a turn runs still work', () => {
  assert.deepEqual(arrivalOf('b', ids('a', 'b', 'c')), { kind: 'merge', mergeId: 'b' });
  assert.deepEqual(arrivalOf('a', ids('a', 'b')), { kind: 'merge', mergeId: 'a' });
  assert.equal(arrivalOf(null, ids('a')), null);                      // the first step: the start graph lands, nothing else does
  assert.equal(arrivalOf(null, ids('a', 'b')), null);
  assert.equal(arrivalOf('c', ids('a', 'b', 'c')), null);             // nothing new
  assert.equal(arrivalOf('a', ids('a', 'b', 'c')), null);             // two steps came in a burst: the one that held the graph is not the one just before the newest
  assert.equal(arrivalOf('zz', ids('a', 'b')), null);                 // the earlier newest is no longer drawn
  assert.equal(arrivalOf('a', ids('a')), null);
  assert.equal(arrivalOf('a', []), null);
  // the live list draws only the newest five: when the window slides the count stays at five but the ids still say what arrived
  assert.deepEqual(arrivalOf('s5', ids('s2', 's3', 's4', 's5', 's6')), { kind: 'merge', mergeId: 's5' });
});

test('the timings are in order: the node pops in about when the light reaches it, then the graph fades, then it goes', () => {
  assert.ok(LAND_LEAVE_AT < LAND_END_AT && LAND_END_AT - LAND_LEAVE_AT >= 300, 'the fade has its 300ms');
  assert.ok(LAND_LEAVE_AT > ARRIVE_DELAY_MS + 350, 'the graph fades only once the node has finished popping in');
  assert.ok(TICK_MS >= 200 && TICK_MS <= 400);
});

test('the graph puts its last node exactly on the dot of the step that lands, both before the first step and in the gap under a step', () => {
  // before the first step: the list has a 4px top margin, the dot's centre is 10px below its row's top, and the last trunk node is at y = 60
  assert.equal(START_SPACE_PX, 46);
  assert.equal(START_SPACE_PX + 4 + 10, START_TRUNK[3][1]);
  // under a step: the row keeps 50px below its text, the box starts at the top of that room, and hangs 16px below the row's bottom edge, which is where the next row (and its dot, 10px down) begins
  const last = START_TRUNK[3][1];
  assert.equal(GAP_SPACE_PX, 50);
  assert.equal(GAP_OVERHANG_PX, 16);
  assert.equal(GAP_OVERHANG_PX - START_BOX.height, -GAP_SPACE_PX, 'the top of the box is at the top of the room');
  assert.equal(GAP_OVERHANG_PX - (START_BOX.height - last), 10, 'the last node is 10px below the row\'s edge: on the next dot');
  // the dashed stub from the step's dot stops at the first node
  assert.equal(GAP_STUB_END_PX, 44);
  assert.equal(GAP_SPACE_PX - GAP_STUB_END_PX, START_TRUNK[0][1]);
});

// ---- how the rules are wired into the components and the stylesheet (read as text: the same way the other component tests pin the page) -------------------------------------------------------------

const read = name => readFileSync(new URL(name, import.meta.url), 'utf8').replace(/\r\n/g, '\n');
const traceSteps = read('./TraceSteps.tsx');
const disclosure = read('./StepsDisclosure.tsx');
const graphs = read('./LoaderGraphs.tsx');
const rules = read('./graphLoader.ts');
const css = read('./__styles__/TracePanel.css');

test('the landing is worked out in the render that first has the new step, by ids, for the live list of a running turn and only when motion is allowed: never in an effect, which flickered', () => {
  assert.match(traceSteps, /const lastId = steps\.length > 0 \? steps\[steps\.length - 1\]\.id : null;/);
  assert.match(traceSteps, /if \(lastId !== lastSeen\) \{\s+setLastSeen\(lastId\);\s+const arrival = live && running && !reduced \? arrivalOf\(lastSeen, steps\) : null;\s+if \(arrival\) setMerging\(arrival\.mergeId\);/);
  assert.ok(traceSteps.indexOf('setMerging(arrival.mergeId)') < traceSteps.indexOf('useEffect('), 'it is set while rendering, before the effect that only clears it');
  assert.match(traceSteps, /useEffect\(\(\) => \{\s+if \(merging === null \|\| hold\) return undefined;\s+const timer = window\.setTimeout\(\(\) => setMerging\(null\), LAND_END_AT\);\s+return \(\) => window\.clearTimeout\(timer\);/);
  assert.equal((traceSteps.match(/setMerging\(/g) ?? []).length, 2, 'it is set once and cleared once');
});

test('the graph hangs under the newest active step, waits for the first step\'s landing and for a landing to finish, and the ring is gone', () => {
  assert.match(traceSteps, /const loaderNode = live && running && last && !done;/);
  assert.match(traceSteps, /const forking = live && \(\(loaderNode && merging === null && !landing\) \|\| merging === step\.id\);/);
  assert.match(traceSteps, /const arriving = live && \(\(merging !== null && last\) \|\| \(landing && i === 0\)\);/);
  assert.match(traceSteps, /\{forking && <span className="gl-below" aria-hidden="true"><StartGraph stem landing=\{merging === step\.id\} reducedMotion=\{reduced\} hold=\{hold\} \/><\/span>\}/);
  assert.match(traceSteps, /\(last && running\) \|\| merging === step\.id \? ' trace-node-running' : ''/);          // the held step keeps its dashed stub while the graph is landing
  assert.match(traceSteps, /\$\{forking \? ' trace-node-forking' : ''\}\$\{merging === step\.id \? ' trace-node-merging' : ''\}\$\{arriving \? ' trace-node-arriving' : ''\}/);
  assert.doesNotMatch(traceSteps, /trace-node-ring|trace-node-spinning|\bspinner\b/);
  // the trace panel (the embedded drawer) is not given the loader: it keeps its pulsing dot
  assert.match(read('./TracePanel.tsx'), /<TraceSteps key=\{steps\.length === 0 \? 'none' : 'steps'\} steps=\{steps\} running=\{running\} \/>/);
  const chat = read('../pages/Chat.tsx');
  assert.equal((chat.match(/<StepsDisclosure steps=\{liveSteps\} running idPrefix="live" \/>/g) ?? []).length, 2, 'the page asks for the live steps exactly as before');
});

test('the graph is one instance from waiting through the first landing, because it sits in the same wrapper in the same place in both, and the first step\'s landing is worked out in the render too', () => {
  assert.equal((disclosure.match(/<StartGraph /g) ?? []).length, 1, 'drawn once in the source, so it stays one instance');
  assert.match(disclosure, /\{\(waiting \|\| landing\) && <StartGraph landing=\{landing\} reducedMotion=\{reduced\} hold=\{hold\} \/>\}/);
  assert.match(disclosure, /if \(steps\.length !== seen\) \{\s+setSeen\(steps\.length\);\s+if \(running && !reduced && firstStepLanded\(seen, steps\.length\)\) setLanding\(true\);\s+else if \(steps\.length === 0\) setLanding\(false\);/);
  assert.ok(disclosure.indexOf('setLanding(true)') < disclosure.indexOf('useEffect('), 'set while rendering, then only cleared by the effect');
  assert.match(disclosure, /setTimeout\(\(\) => setLanding\(false\), LAND_END_AT\)/);
  assert.match(disclosure, /style=\{landing \? \{ paddingTop: START_SPACE_PX \} : undefined\}/);
  assert.match(disclosure, /className=\{`gl-startwrap\$\{waiting \? ' gl-start-wait' : landing \? '' : ' gl-start-settled'\}`\}/);
  assert.match(disclosure, /role=\{waiting \? 'status' : undefined\}/);          // "Thinking..." is announced while waiting, as the old loader's text was
  assert.match(disclosure, /<TraceSteps steps=\{hidden > 0 \? all\.slice\(hidden\) : all\} running live landing=\{landing\}/);
  assert.match(disclosure, /<TraceSteps steps=\{steps\} running=\{false\} idPrefix=\{idPrefix\} \/>/);          // a finished turn draws its steps with no loader
});

test('the drawing uses the rules\' timings, is hidden from assistive technology, holds still for reduced motion, cleans up its timers, and nothing is left of the horizontal fork', () => {
  for (const name of ['LAND_LEAVE_AT', 'TICK_MS', 'LANDING_STAGGER_MS']) assert.ok(graphs.includes(name), `${name} is used`);
  assert.equal((graphs.match(/aria-hidden="true" focusable="false"/g) ?? []).length, 1, 'the one drawing is decoration');
  assert.match(graphs, /const liveIndex = reducedMotion \? STATIC_START_FRAME : tick % START_FRAMES\.length;/);
  assert.match(graphs, /useTick\(TICK_MS, !reducedMotion\)/);
  assert.match(graphs, /return \(\) => window\.clearInterval\(timer\);/);
  assert.match(graphs, /return \(\) => window\.clearTimeout\(timer\);/);
  assert.match(graphs, /const way = landing \? startWay\(frame\.pass\) : \[\];/);          // the landing carries on down the pass's own way, nothing removed
  assert.doesNotMatch(graphs, /Math\.random|Date\.now|dangerouslySetInnerHTML/);
  for (const [name, text] of [['LoaderGraphs.tsx', graphs], ['graphLoader.ts', rules], ['TraceSteps.tsx', traceSteps], ['TracePanel.css', css]]) {
    assert.doesNotMatch(text, /PagedFork|FORK_|MERGE_|mergePath|forkView|branchPath|gl-fork|gl-wrap|gl-page|gl-drawn/, `${name} has no horizontal fork left`);
  }
  const hook = read('./useReducedMotion.ts');
  assert.match(hook, /matchMedia\(query\)/);
  assert.match(hook, /return force \?\? system;/);
  assert.match(hook, /media\.removeEventListener\('change', update\)/);          // it follows the setting live and lets go on unmount
});

test('the stylesheet agrees with the rules: the room under a step, where the box hangs, the stub, when the new step fades in, and nothing left of the ring', () => {
  assert.match(css, new RegExp(`\\.steps-inline-live \\.trace-node-forking, \\.steps-inline-live \\.trace-node-merging \\{ padding-bottom: ${GAP_SPACE_PX}px; \\}`));
  assert.match(css, new RegExp(`\\.steps-inline-live \\.trace-node-forking::after, \\.steps-inline-live \\.trace-node-merging::after \\{ height: auto; bottom: ${GAP_STUB_END_PX}px;`));
  // the graph grows out of the step's own node: the stem is solid, in the graph's colour and line width (1.8 units, centred on x = 7), and the graph draws no first node of its own
  assert.match(css, /merging::after \{[^}]*left: 6\.1px; width: 1\.8px; background: var\(--gl\); animation: none; opacity: 1; \}/);
  assert.equal(START_TRUNK[0][0], 7);
  assert.match(graphs, /stem && n === 0 \? null : <circle key=\{`t\$\{n\}`\}/);
  assert.match(graphs, /stem = false/);
  assert.equal((disclosure.match(/<StartGraph /g) ?? []).length, 1);
  assert.doesNotMatch(disclosure, /<StartGraph[^>]*\bstem\b/, 'the graph before the first step keeps its own first node');
  // the newest step keeps room below it for the graph's last 16px (and 4px of air), so what follows the list (a run card) is never drawn over
  assert.match(css, new RegExp(`\\.steps-inline-live \\.trace-node-forking\\.trace-node-last \\{ margin-bottom: ${GAP_OVERHANG_PX + 4}px; \\}`));
  assert.match(css, new RegExp(`\\.gl-below \\{ position: absolute; left: 0; bottom: -${GAP_OVERHANG_PX}px; z-index: 2; \\}`));
  assert.match(css, new RegExp(`animation: gl-arrive \\.35s ease ${ARRIVE_DELAY_MS}ms both;`));          // the same moment the rules name
  assert.match(css, /\.gl-below \.gl-svg \{ animation: gl-fade-in \.35s ease backwards; \}/);          // backwards: both would lock the opacity and stop the fade-out
  assert.match(css, /\.gl-svg\.gl-leaving \{ opacity: 0; transition: opacity \.3s ease; \}/);
  assert.match(css, /\.gl-start-settled \{ transition: padding-top \.35s ease; \}/);
  assert.match(css, /\.steps-inline-live \.trace-node-merging::before \{ opacity: 0; \}/);          // the plain line is hidden while the graph is the connection
  assert.match(css, /\.steps-inline-live \{ --gl: var\(--accent-primary\); \}/);
  const reduced = css.slice(css.lastIndexOf('@media (prefers-reduced-motion: reduce) {'));          // the last such block is the loader's
  for (const rule of ['.gl-link', '.gl-start-settled', '.gl-below .gl-svg', '.gl-start-text-out', '.trace-node-arriving']) assert.ok(reduced.includes(rule), `reduced motion stills ${rule}`);
  assert.doesNotMatch(css, /trace-node-ring|trace-node-spinning|sonic-spin|sonic-loader-container/);
  assert.match(graphs, /width=\{START_BOX\.width\} height=\{START_BOX\.height\}/);
  assert.equal(START_BOX.height, 66);
  assert.match(css, /\.gl-startwrap > \.gl-svg\.gl-start \{ position: absolute; left: 0; top: 0; z-index: 2; \}/);
  assert.match(css, /\.gl-start-text \{ position: absolute; left: 56px; top: 23px; \}/);          // the text keeps its place beside the graph when the graph goes absolute (graph 44px wide + a 12px gap)
});

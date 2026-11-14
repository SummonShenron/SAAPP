// Run with: node --experimental-test-module-mocks --test src/components/graphLoader.test.mjs   (from local/; Node 22.18+ runs the TypeScript module directly)
// The graph loader's rules (components/graphLoader.ts): the start graph, the paged fork, the merge into the next step, and which arrival is which.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import {
  ARRIVE_DELAY_MS, FORK_FRAMES, FORK_PAGES, FORK_PASSES, LANDING_STAGGER_MS, MERGE_DRAW_AT, MERGE_END_AT, MERGE_LAND_AT, MERGE_LAND_Y, MERGE_LEAVE_AT, MERGE_NODE_Y, MERGE_RIM, START_END_AT, START_FRAMES,
  START_LEAVE_AT, START_PASSES, START_ROUTES, START_SIDES, START_SPACE_PX, START_TRUNK, STATIC_FORK_FRAME, STATIC_START_FRAME, TICK_MS, arrivalOf, branchPath, downPath, firstStepLanded, forkView,
  mergePath, startLit, startWay,
} from './graphLoader.ts';

const idsOf = page => new Set([...Object.keys(page.nodes), ...Object.keys(page.edges)]);

test('every page of the fork is a sound little graph with exactly two ways through it, each ending in a node on the right', () => {
  assert.equal(FORK_PAGES.length, 3);
  for (const [index, page] of FORK_PAGES.entries()) {
    const ids = idsOf(page);
    assert.equal(page.root, 'R');
    assert.equal(page.routes.length, 2, `page ${index} has two routes`);
    for (const [from, to] of Object.values(page.edges)) assert.ok(page.nodes[from] && page.nodes[to], `an edge of page ${index} joins nodes that exist`);
    for (const route of page.routes) {
      assert.ok(route.every(id => ids.has(id)), `page ${index}: every element of a route exists`);
      route.forEach((id, i) => assert.equal(id in page.edges, i % 2 === 0, `page ${index}: a route alternates edge, node, edge, node`));
      const [x] = page.nodes[route[route.length - 1]];
      assert.ok(x >= 44, `page ${index}: a route ends on the right (x = ${x})`);
      assert.deepEqual(page.edges[route[0]][0], 'R');                                  // and starts at the root
    }
    // nothing on a page is unreachable: every node lies on one of the routes (or is the root)
    const onRoutes = new Set(page.routes.flat());
    for (const id of Object.keys(page.nodes)) assert.ok(id === 'R' || onRoutes.has(id), `page ${index}: ${id} is on a route`);
  }
  // they are three different shapes: 5, 4 and 6 nodes (a Y, a diamond that rejoins, and an uneven pair)
  assert.deepEqual(FORK_PAGES.map(page => Object.keys(page.nodes).length), [5, 4, 6]);
  assert.equal(FORK_PAGES[1].routes[0].at(-1), FORK_PAGES[1].routes[1].at(-1));          // the diamond's two ways end on the same node
  assert.notEqual(FORK_PAGES[2].routes[0].length, FORK_PAGES[2].routes[1].length);        // the uneven page's ways differ in length
});

test('the loop goes through the three pages in order and then again by the other routes: six different animations', () => {
  assert.deepEqual(FORK_PASSES, [[0, 1, 0], [1, 0, 1]]);
  FORK_PASSES[0].forEach((route, page) => assert.notEqual(route, FORK_PASSES[1][page], `page ${page} is taken by the other route the second time`));
  const order = [];
  for (const frame of FORK_FRAMES) {
    const key = `${frame.pass}:${frame.page}`;
    if (order.at(-1) !== key) order.push(key);
  }
  assert.deepEqual(order, ['0:0', '0:1', '0:2', '1:0', '1:1', '1:2']);
  // each page: one beat per element, two beats holding on the chosen end, then two beats of the view moving on
  for (const [pass, routes] of FORK_PASSES.entries()) {
    FORK_PAGES.forEach((page, p) => {
      const length = page.routes[routes[p]].length;
      const frames = FORK_FRAMES.filter(f => f.pass === pass && f.page === p);
      assert.deepEqual(frames.map(f => f.lit), [...Array.from({ length }, (_, i) => i + 1), length, length, length, length]);
      assert.deepEqual(frames.map(f => f.pan), [...Array(length + 2).fill(0), 1, 2]);
    });
  }
  assert.equal(FORK_FRAMES.length, 4 + 2 + 2 + 4 + 2 + 2 + 4 + 2 + 2 + 4 + 2 + 2 + 4 + 2 + 2 + 6 + 2 + 2);
});

test('what is on screen at a beat: its route lit so far, where the view moves to, and what comes next', () => {
  const first = forkView(FORK_FRAMES[0]);
  assert.equal(first.pageIndex, 0);
  assert.deepEqual([...first.lit], ['RT']);
  const done = forkView({ pass: 0, page: 0, lit: 4, pan: 0 });
  assert.deepEqual([...done.lit], ['RT', 'T', 'TTL', 'TL']);
  assert.deepEqual(done.leaf, [44, 6]);
  // the view follows the chosen end: moving by `move` brings it to where the next page's root is
  assert.deepEqual(done.move, [5 - 44, 16 - 6]);
  assert.equal(done.next.pageIndex, 1);
  assert.equal(done.next.pass, 0);
  // after the last page it goes round to the first, by the other routes
  const last = forkView({ pass: 0, page: 2, lit: 4, pan: 1 });
  assert.deepEqual([last.next.pageIndex, last.next.pass, last.next.route], [0, 1, 1]);
  const lastOfAll = forkView({ pass: 1, page: 2, lit: 6, pan: 1 });
  assert.deepEqual([lastOfAll.next.pageIndex, lastOfAll.next.pass, lastOfAll.next.route], [0, 0, 0]);
  // a frame in the other pass takes the other route
  assert.deepEqual(forkView({ pass: 1, page: 0, lit: 1, pan: 0 }).routeIds.slice(0, 2), ['RB', 'B']);
});

test('the reduced-motion fork holds on the first page with its first route lit to the end, and the reduced start graph on a beat with part of the way lit', () => {
  assert.deepEqual([...forkView(FORK_FRAMES[STATIC_FORK_FRAME]).lit], ['RT', 'T', 'TTL', 'TL']);
  assert.equal(FORK_FRAMES[STATIC_FORK_FRAME].pan, 0);
  const still = START_FRAMES[STATIC_START_FRAME];
  assert.equal(still.fade, false);
  assert.ok(startLit(still).size > 1);
});

test('the way back always leaves from the far right end of the chosen route, whatever page and route, runs along a low line clear of the pages and the next title, and ends on the node rim', () => {
  const lowestBottom = Math.max(...FORK_PAGES.flatMap(page => Object.values(page.nodes).map(([, y, r]) => y + r)));
  assert.ok(MERGE_LAND_Y >= lowestBottom, 'the low line is below every node of every page');
  assert.ok(MERGE_LAND_Y < MERGE_NODE_Y - 6, 'and above the new step\'s node (the title sits level with the node, so a line at that height would strike through it)');
  assert.ok(MERGE_RIM[0] < 22, 'the rim is in the left gutter, left of where a title starts');
  assert.ok(MERGE_RIM[1] < MERGE_NODE_Y && MERGE_RIM[1] > MERGE_NODE_Y - 7, 'and on the upper part of the node\'s edge');
  for (const [pass, routes] of FORK_PASSES.entries()) {
    FORK_PAGES.forEach((page, p) => {
      const view = forkView({ pass, page: p, lit: 1, pan: 0 });            // even when only the first element had lit by the time the step arrived
      const d = mergePath(view);
      assert.ok(view.leaf[0] >= 44);
      assert.ok(d.startsWith(`M ${view.leaf[0]} ${view.leaf[1]} C `), `page ${p} pass ${pass} leaves from its far end`);
      assert.ok(d.endsWith(`${MERGE_RIM[0]} ${MERGE_RIM[1]}`), 'and ends on the rim');
      assert.ok(d.includes(` L 17 ${MERGE_LAND_Y} `), 'along the low line');
    });
  }
  assert.equal(mergePath({ leaf: [44, 6] }), mergePath(forkView({ pass: 0, page: 0, lit: 4, pan: 0 })));
});

test('a branch is drawn as a flow-chart curve, and a downward one leaves and arrives vertically', () => {
  assert.equal(branchPath(5, 16, 22, 8), 'M 5 16 C 13.5 16, 13.5 8, 22 8');
  assert.equal(downPath(7, 6, 24, 15), 'M 7 6 C 7 10.5, 24 10.5, 24 15');
});

test('the start graph is a trunk on the line the dots sit on, with one side node to the right in each of three stretches and two ways down through each', () => {
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

test('the start graph\'s beats take each stretch in order, light what has been travelled, and fade at the end of each pass', () => {
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

test('when the first step lands the light carries on down the pass\'s own way, detours included, to the last node, and not all of the graph is on that way, so it keeps its shape', () => {
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

test('a fork merges into the next step only when that step arrives right after the one that was showing it, and goes by ids so that the five drawn while a turn runs still work', () => {
  assert.deepEqual(arrivalOf('b', ids('a', 'b', 'c')), { kind: 'merge', mergeId: 'b' });
  assert.deepEqual(arrivalOf('a', ids('a', 'b')), { kind: 'merge', mergeId: 'a' });
  assert.equal(arrivalOf(null, ids('a')), null);                      // the first step: the start graph lands, nothing merges
  assert.equal(arrivalOf(null, ids('a', 'b')), null);
  assert.equal(arrivalOf('c', ids('a', 'b', 'c')), null);             // nothing new
  assert.equal(arrivalOf('a', ids('a', 'b', 'c')), null);             // two steps came in a burst: the one that held the fork is not the one just before the newest
  assert.equal(arrivalOf('zz', ids('a', 'b')), null);                 // the earlier newest is no longer drawn
  assert.equal(arrivalOf('a', ids('a')), null);
  assert.equal(arrivalOf('a', []), null);
  // the live list draws only the newest five: when the window slides the count stays at five but the ids still say what arrived
  assert.deepEqual(arrivalOf('s5', ids('s2', 's3', 's4', 's5', 's6')), { kind: 'merge', mergeId: 's5' });
});

test('the timings are in order: the way back is drawn, lands, the held fork fades and is removed; the node pops in about when the line reaches it; the start graph fades, then goes', () => {
  assert.ok(MERGE_DRAW_AT < MERGE_LAND_AT && MERGE_LAND_AT < MERGE_LEAVE_AT && MERGE_LEAVE_AT < MERGE_END_AT);
  assert.ok(MERGE_END_AT - MERGE_LEAVE_AT >= 300, 'the fade has its 300ms');
  assert.ok(ARRIVE_DELAY_MS > MERGE_DRAW_AT && ARRIVE_DELAY_MS < MERGE_LEAVE_AT, 'the node pops in after the line starts and before the fork fades');
  assert.ok(START_LEAVE_AT < START_END_AT && START_END_AT - START_LEAVE_AT >= 300);
  assert.ok(START_LEAVE_AT > ARRIVE_DELAY_MS + 350, 'the start graph fades only once the first node has finished popping in');
  assert.ok(TICK_MS >= 200 && TICK_MS <= 400);
});

test('the start graph leaves exactly the room that puts its last node on the first step\'s dot', () => {
  // the list has a 4px top margin, the dot's centre is 10px below its row's top (3px + half of 14px), and the last trunk node is at y = 60
  assert.equal(START_SPACE_PX, 46);
  assert.equal(START_SPACE_PX + 4 + 10, START_TRUNK[3][1]);
});

// ---- how the rules are wired into the components and the stylesheet (read as text: the same way the other component tests pin the page) -------------------------------------------------------------

const read = name => readFileSync(new URL(name, import.meta.url), 'utf8').replace(/\r\n/g, '\n');
const traceSteps = read('./TraceSteps.tsx');
const disclosure = read('./StepsDisclosure.tsx');
const graphs = read('./LoaderGraphs.tsx');
const css = read('./__styles__/TracePanel.css');

test('the merge is worked out in the render that first has the new step, by ids, for the live list of a running turn and only when motion is allowed: never in an effect, which flickered', () => {
  assert.match(traceSteps, /const lastId = steps\.length > 0 \? steps\[steps\.length - 1\]\.id : null;/);
  assert.match(traceSteps, /if \(lastId !== lastSeen\) \{\s+setLastSeen\(lastId\);\s+const arrival = live && running && !reduced \? arrivalOf\(lastSeen, steps\) : null;\s+if \(arrival\) setMerging\(arrival\.mergeId\);/);
  assert.ok(traceSteps.indexOf('setMerging(arrival.mergeId)') < traceSteps.indexOf('useEffect('), 'the merge is set while rendering, before the effect that only clears it');
  assert.match(traceSteps, /useEffect\(\(\) => \{\s+if \(merging === null \|\| hold\) return undefined;\s+const timer = window\.setTimeout\(\(\) => setMerging\(null\), MERGE_END_AT\);\s+return \(\) => window\.clearTimeout\(timer\);/);
  assert.equal((traceSteps.match(/setMerging\(/g) ?? []).length, 2, 'it is set once and cleared once');
});

test('the fork hangs under the newest active step, waits for the first step\'s landing and for a merge to finish, and the ring is gone', () => {
  assert.match(traceSteps, /const loaderNode = live && running && last && !done;/);
  assert.match(traceSteps, /const forking = live && \(\(loaderNode && merging === null && !landing\) \|\| merging === step\.id\);/);
  assert.match(traceSteps, /const arriving = live && \(\(merging !== null && last\) \|\| \(landing && i === 0\)\);/);
  assert.match(traceSteps, /\{forking && <span className="gl-below" aria-hidden="true"><PagedFork merging=\{merging === step\.id\} reducedMotion=\{reduced\} hold=\{hold\} \/><\/span>\}/);
  assert.match(traceSteps, /\(last && running\) \|\| merging === step\.id \? ' trace-node-running' : ''/);          // the held step keeps its dashed stub while the way back is on screen
  assert.match(traceSteps, /\$\{forking \? ' trace-node-forking' : ''\}\$\{merging === step\.id \? ' trace-node-merging' : ''\}\$\{arriving \? ' trace-node-arriving' : ''\}/);
  assert.doesNotMatch(traceSteps, /trace-node-ring|trace-node-spinning|\bspinner\b/);
  // the trace panel (the embedded drawer) is not given the loader: it keeps its pulsing dot
  assert.match(read('./TracePanel.tsx'), /<TraceSteps key=\{steps\.length === 0 \? 'none' : 'steps'\} steps=\{steps\} running=\{running\} \/>/);
  const chat = read('../pages/Chat.tsx');
  assert.equal((chat.match(/<StepsDisclosure steps=\{liveSteps\} running idPrefix="live" \/>/g) ?? []).length, 2, 'the page asks for the live steps exactly as before');
});

test('the start graph is one instance from waiting through the landing, because it sits in the same wrapper in the same place in both, and the first step\'s landing is worked out in the render too', () => {
  assert.equal((disclosure.match(/<StartGraph /g) ?? []).length, 1, 'drawn once in the source, so it stays one instance');
  assert.match(disclosure, /\{\(waiting \|\| landing\) && <StartGraph landing=\{landing\} reducedMotion=\{reduced\} hold=\{hold\} \/>\}/);
  assert.match(disclosure, /if \(steps\.length !== seen\) \{\s+setSeen\(steps\.length\);\s+if \(running && !reduced && firstStepLanded\(seen, steps\.length\)\) setLanding\(true\);\s+else if \(steps\.length === 0\) setLanding\(false\);/);
  assert.ok(disclosure.indexOf('setLanding(true)') < disclosure.indexOf('useEffect('), 'set while rendering, then only cleared by the effect');
  assert.match(disclosure, /setTimeout\(\(\) => setLanding\(false\), START_END_AT\)/);
  assert.match(disclosure, /style=\{landing \? \{ paddingTop: START_SPACE_PX \} : undefined\}/);
  assert.match(disclosure, /className=\{`gl-startwrap\$\{waiting \? ' gl-start-wait' : landing \? '' : ' gl-start-settled'\}`\}/);
  assert.match(disclosure, /role=\{waiting \? 'status' : undefined\}/);          // "Thinking..." is announced while waiting, as the old loader's text was
  assert.match(disclosure, /<TraceSteps steps=\{hidden > 0 \? all\.slice\(hidden\) : all\} running live landing=\{landing\}/);
  assert.match(disclosure, /<TraceSteps steps=\{steps\} running=\{false\} idPrefix=\{idPrefix\} \/>/);          // a finished turn draws its steps with no loader
});

test('the drawings use the rules\' timings, are hidden from assistive technology, hold still for reduced motion, and clean up their timers', () => {
  for (const name of ['MERGE_DRAW_AT', 'MERGE_LAND_AT', 'MERGE_LEAVE_AT', 'START_LEAVE_AT', 'TICK_MS', 'LANDING_STAGGER_MS']) assert.ok(graphs.includes(name), `${name} is used`);
  assert.equal((graphs.match(/aria-hidden="true" focusable="false"/g) ?? []).length, 2, 'both drawings are decoration');
  assert.match(graphs, /const liveIndex = reducedMotion \? STATIC_FORK_FRAME : tick % FORK_FRAMES\.length;/);
  assert.match(graphs, /const liveIndex = reducedMotion \? STATIC_START_FRAME : tick % START_FRAMES\.length;/);
  assert.match(graphs, /useTick\(TICK_MS, !reducedMotion\)/);
  assert.match(graphs, /return \(\) => window\.clearInterval\(timer\);/);
  assert.match(graphs, /return \(\) => timers\.forEach\(window\.clearTimeout\);/);
  assert.match(graphs, /\{ \.\.\.base, pan: 0, lit: forkView\(base\)\.routeIds\.length \}/);          // a merge lights the chosen route to its end at once
  assert.match(graphs, /d=\{mergePath\(view\)\}/);
  assert.match(graphs, /const way = landing \? startWay\(frame\.pass\) : \[\];/);          // the landing carries on down the pass's own way, nothing removed
  assert.doesNotMatch(graphs, /Math\.random|Date\.now|dangerouslySetInnerHTML/);
  const hook = read('./useReducedMotion.ts');
  assert.match(hook, /matchMedia\(query\)/);
  assert.match(hook, /return force \?\? system;/);
  assert.match(hook, /media\.removeEventListener\('change', update\)/);          // it follows the setting live and lets go on unmount
});

test('the stylesheet agrees with the rules: the room the fork and the start graph take, where the fork sits, when the new step fades in, and nothing left of the ring', () => {
  assert.match(css, /\.steps-inline-live \.trace-node-forking, \.steps-inline-live \.trace-node-merging \{ padding-bottom: 46px; \}/);
  assert.match(css, /\.steps-inline-live \.trace-node-forking::after, \.steps-inline-live \.trace-node-merging::after \{ height: auto; bottom: 22px;/);
  assert.match(css, /\.gl-below \{ position: absolute; left: 0; bottom: -17px; z-index: 2; \}/);
  assert.match(css, new RegExp(`animation: gl-arrive \\.35s ease ${ARRIVE_DELAY_MS}ms both;`));          // the same moment the rules name
  assert.match(css, /\.gl-below \.gl-svg \{ animation: gl-fade-in \.35s ease backwards; \}/);          // backwards: both would lock the opacity and stop the fade-out
  assert.match(css, /\.gl-svg\.gl-leaving \{ opacity: 0; transition: opacity \.3s ease; \}/);
  assert.match(css, /\.gl-start-settled \{ transition: padding-top \.35s ease; \}/);
  assert.match(css, /\.steps-inline-live \.trace-node-merging::before \{ opacity: 0; \}/);          // the plain line is hidden while the way back is the connection
  assert.match(css, /\.steps-inline-live \{ --gl: var\(--accent-primary\); \}/);
  const reduced = css.slice(css.lastIndexOf('@media (prefers-reduced-motion: reduce) {'));          // the last such block is the loader's
  for (const rule of ['.gl-page', '.gl-wrap', '.gl-start-settled', '.gl-below .gl-svg', '.gl-start-text-out', '.trace-node-arriving']) assert.ok(reduced.includes(rule), `reduced motion stills ${rule}`);
  assert.doesNotMatch(css, /trace-node-ring|trace-node-spinning|sonic-spin|sonic-loader-container/);
  // the fork's drawing (60 x 46 units, shown 70 x 54px, its bottom 17px below its row) puts the new step's node level with the step's dot, which is 10px below the row's bottom
  const scale = 70 / 60;
  const topOfDrawing = -(54 - 17);
  const nodeBelowRow = topOfDrawing + MERGE_NODE_Y * scale + (54 - 46 * scale) / 2;
  assert.ok(Math.abs(nodeBelowRow - 10) < 0.5, `the way back lands on the node (${nodeBelowRow.toFixed(2)}px below the row, the dot is at 10px)`);
  assert.match(graphs, /width=\{70\} height=\{54\}/);
  assert.match(css, /\.gl-startwrap > \.gl-svg\.gl-start \{ position: absolute; left: 0; top: 0; z-index: 2; \}/);
  assert.match(css, /\.gl-start-text \{ position: absolute; left: 56px; top: 23px; \}/);          // the text keeps its place beside the graph when the graph goes absolute (graph 44px wide + a 12px gap)
});

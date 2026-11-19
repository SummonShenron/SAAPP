// Run with: node --experimental-test-module-mocks --test src/components/graphLoader.test.mjs   (from local/; Node 22.18+ runs the TypeScript module directly)
// The graph loader's rules (components/graphLoader.ts): the shapes and which one is picked, the beats, how it lands in the next step, how fast it plays, and which arrival is which.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { STEP_KINDS } from './stepKinds.ts';
import {
  ARRIVE_DELAY_MS, FIRST_NODE_Y, GAP_OVERHANG_PX, GAP_SPACE_PX, GAP_STUB_END_PX, GRAPH_SHAPES, LANDING_STAGGER_MS, LAND_END_AT, LAND_LEAVE_AT, LAST_NODE_Y, MAX_PACE, PASSES, START_BOX, START_SPACE_PX, TICK_MS,
  COMPACT_AFTER, COMPACT_OVERHANG_PX, pickFor, seededRand, COMPACT_SCALE, COMPACT_SPACE_PX, COMPACT_STUB_END_PX, GENERIC_SHAPES, GRAPH_TEXT_INDENT_PX, KIND_SHAPE, TRUNK_X, gapSpaceFor, keptScale, arrivalsOf, buildStartFrames, downPath, firstStepLanded, landingStagger, paceFor, pickGraph, randomPick, scaled, staticFrameIndex, startLit, startWay, without, wayIndex,
} from './graphLoader.ts';

/** Every pick there can be: each shape with every combination of starting ways. */
function everyPick() {
  const out = [];
  for (const shape of GRAPH_SHAPES) {
    let all = [[]];
    for (const ways of shape.routes) all = all.flatMap(prefix => ways.map((_, w) => [...prefix, w]));
    for (const starts of all) out.push({ shape, starts });
  }
  return out;
}
/** A random source that walks through the numbers in order, wrapping round. */
const sequence = (...values) => { let i = 0; return () => values[i++ % values.length]; };

test('a downward branch is drawn as a flow-chart curve that leaves and arrives vertically', () => {
  assert.equal(downPath(7, 6, 24, 15), 'M 7 6 C 7 10.5, 24 10.5, 24 15');
});

// ---- the shapes ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

test('there are several shapes, and each is a trunk on the line the dots sit on with the first and last node where the page expects them', () => {
  assert.ok(GRAPH_SHAPES.length >= 18, 'a shape for each kind of action and several more');
  assert.equal(new Set(GRAPH_SHAPES.map(s => s.id)).size, GRAPH_SHAPES.length, 'each shape has its own id');
  for (const shape of GRAPH_SHAPES) {
    const trunk = shape.nodes.filter(n => /^t\d+$/.test(n.id));
    assert.ok(trunk.length >= 2, `${shape.id}: at least one stretch`);
    assert.ok(trunk.every(n => n.x === TRUNK_X), `${shape.id}: the trunk is at x = 7, where every step dot is centred (the dot is 14px wide)`);
    assert.ok(trunk.every((n, i, all) => i === 0 || n.y > all[i - 1].y), `${shape.id}: and runs downward`);
    assert.equal(trunk[0].y, FIRST_NODE_Y, `${shape.id}: the first node is where the dashed stub ends`);
    assert.equal(trunk.at(-1).y, LAST_NODE_Y, `${shape.id}: the last node is where the next step's dot will be`);
    assert.equal(shape.routes.length, trunk.length - 1, `${shape.id}: one set of ways per stretch`);
  }
  assert.equal(FIRST_NODE_Y, 6);
  assert.equal(LAST_NODE_Y, 60);
});

test('every shape fits its box, keeps side nodes to the right and inside their stretch, and every way ends on the next trunk node with ids that exist', () => {
  for (const shape of GRAPH_SHAPES) {
    const byId = new Map(shape.nodes.map(n => [n.id, n]));
    assert.equal(byId.size, shape.nodes.length, `${shape.id}: node ids are unique`);
    assert.equal(new Set(shape.edges.map(e => e.id)).size, shape.edges.length, `${shape.id}: edge ids are unique`);
    for (const n of shape.nodes) assert.ok(n.x - n.r >= 0 && n.x + n.r <= START_BOX.width && n.y - n.r >= 0 && n.y + n.r <= START_BOX.height, `${shape.id}: ${n.id} lies inside the ${START_BOX.width} x ${START_BOX.height} box`);
    const edges = new Set(shape.edges.map(e => e.id));
    shape.routes.forEach((ways, seg) => {
      assert.ok(ways.length >= 1, `${shape.id}: stretch ${seg} has a way`);
      const top = byId.get(`t${seg}`).y;
      const bottom = byId.get(`t${seg + 1}`).y;
      for (const ids of ways) {
        assert.equal(ids.at(-1), `t${seg + 1}`, `${shape.id}: every way of stretch ${seg} ends on the next trunk node`);
        assert.ok(ids.every(id => byId.has(id) || edges.has(id)), `${shape.id}: every id in a way is drawn`);
        const sides = ids.filter(id => id.startsWith('s')).map(id => byId.get(id));
        sides.forEach((s, i) => {
          assert.ok(s.x > TRUNK_X + s.r, `${shape.id}: ${s.id} is to the right of the trunk`);
          assert.ok(s.y >= top + 3 && s.y <= bottom - 3, `${shape.id}: ${s.id} lies inside its stretch`);
          if (i > 0) assert.ok(s.y > sides[i - 1].y, `${shape.id}: ${s.id} is lower than the side node before it`);
        });
        assert.equal(ids.length, 2 * sides.length + 2, `${shape.id}: edge, node, edge, node ... and the trunk node last`);
      }
    });
  }
});

test('the classic shape is the graph that was there before: three stretches, a side node and a straight way in each', () => {
  const classic = GRAPH_SHAPES.find(s => s.id === 'classic');
  assert.deepEqual(classic.nodes.filter(n => n.id.startsWith('t')).map(n => n.y), [6, 24, 42, 60]);
  assert.equal(classic.nodes.find(n => n.id === 't3').r, 3.6, 'the last trunk node is a little larger');
  assert.deepEqual(classic.routes[0][0], ['e0.0.0', 's0.0.0', 'e0.0.1', 't1']);          // out through the side node
  assert.deepEqual(classic.routes[0][1], ['e0.1.0', 't1']);                               // or straight down
  assert.equal(classic.edges.find(e => e.id === 'e0.1.0').d, 'M 7 6 L 7 24');
  assert.equal(classic.edges.find(e => e.id === 'e0.0.0').d, downPath(7, 6, 24, 15));
});

test('the shapes are different from one another: not just the same graph with other numbers', () => {
  const signature = s => JSON.stringify(s.routes.map(ways => ways.map(w => w.length)));
  assert.ok(new Set(GRAPH_SHAPES.map(signature)).size >= 6, 'at least six distinct arrangements of ways');
  assert.ok(GRAPH_SHAPES.some(s => s.routes.length === 2) && GRAPH_SHAPES.some(s => s.routes.length === 4), 'some have fewer and some more stretches');
  assert.ok(GRAPH_SHAPES.some(s => s.routes.some(ways => ways.length >= 3)), 'one has three ways through a stretch');
  assert.ok(GRAPH_SHAPES.some(s => s.routes.some(ways => ways.some(w => w.length >= 6))), 'one has a detour through two or more side nodes');
});

// ---- which graph, and which way it starts ---------------------------------------------------------------------------------------------------------------------------------------------------------------

test('a pick is a shape and a starting way for every stretch, taken from the random source and never out of range, even for a source that returns 1', () => {
  const first = pickGraph(sequence(0, 0, 0, 0, 0));
  assert.equal(first.shape.id, GENERIC_SHAPES[0].id, 'with no kind: one of the shapes that belong to no kind');
  assert.deepEqual(first.starts, first.shape.routes.map(() => 0));
  const last = pickGraph(() => 1);          // a bad source: clamped, not an out-of-range index
  assert.equal(last.shape.id, GENERIC_SHAPES.at(-1).id);
  assert.deepEqual(last.starts, last.shape.routes.map(ways => ways.length - 1));
  for (const pick of everyPick()) pick.starts.forEach((w, seg) => assert.ok(w >= 0 && w < pick.shape.routes[seg].length));
  let seed = 1;
  const lcg = () => { seed = (seed * 1664525 + 1013904223) % 4294967296; return seed / 4294967296; };
  const shapes = new Set();
  const starts = new Set();
  for (let i = 0; i < 400; i++) {
    const pick = pickGraph(lcg);
    shapes.add(pick.shape.id);
    pick.starts.forEach(w => starts.add(w));
    pick.starts.forEach((w, seg) => assert.ok(w < pick.shape.routes[seg].length));
  }
  assert.deepEqual([...shapes].sort(), GENERIC_SHAPES.map(s => s.id).sort(), 'every general shape comes up, and only those');
  assert.deepEqual([...starts].sort(), [0, 1, 2], 'every starting way comes up, the third only where there is one');
});

test('each kind of action has its own shape, the same every time, and no two kinds share one or take one of the general shapes', () => {
  const kinds = STEP_KINDS.filter(kind => kind !== 'generic');
  assert.deepEqual(Object.keys(KIND_SHAPE).sort(), [...kinds].sort(), 'every kind except the general one has a shape');
  const ids = kinds.map(kind => KIND_SHAPE[kind]);
  assert.equal(new Set(ids).size, ids.length, 'no two kinds share a shape');
  for (const id of ids) {
    assert.ok(GRAPH_SHAPES.some(s => s.id === id), `${id} is a drawn shape`);
    assert.ok(!GENERIC_SHAPES.some(s => s.id === id), `${id} belongs to its kind, not to the general shapes`);
  }
  assert.ok(GENERIC_SHAPES.length >= 4, 'enough general shapes left to vary');
  assert.equal(GENERIC_SHAPES.length + ids.length, GRAPH_SHAPES.length, 'every shape is one or the other');
  for (const kind of kinds) {
    for (const r of [0, 0.3, 0.7, 1]) {
      assert.equal(pickGraph(() => r, null, kind).shape.id, KIND_SHAPE[kind], `${kind} always draws ${KIND_SHAPE[kind]}`);
      assert.equal(pickGraph(() => r, KIND_SHAPE[kind], kind).shape.id, KIND_SHAPE[kind], 'whatever was drawn last');
    }
    for (let i = 0; i < 20; i++) assert.equal(randomPick(kind).shape.id, KIND_SHAPE[kind]);
  }
  // the way each stretch starts on is still random, so two web searches in a row do not light the same way
  const startsSeen = new Set();
  for (const r of [0, 0.4, 0.8]) startsSeen.add(JSON.stringify(pickGraph(() => r, null, 'web').starts));
  assert.ok(startsSeen.size > 1);
  // the general kind, and a kind nobody knows, take a general shape
  for (const kind of ['generic', undefined, 'rocket']) assert.ok(GENERIC_SHAPES.some(s => s.id === pickGraph(() => 0.5, null, kind).shape.id), String(kind));
  // the silhouettes the kinds are named for: a web search fans out three ways, reading runs a rail of lines, a database stacks layers beside the trunk
  const shape = id => GRAPH_SHAPES.find(s => s.id === id);
  assert.equal(shape('comb').routes[0].length, 3);
  assert.equal(shape('rail').routes[0].at(-1).filter(id => id.startsWith('s')).length, 3);
  assert.equal(shape('stack').nodes.filter(n => n.id.startsWith('s')).length, 4);
  assert.equal(new Set(shape('stack').nodes.filter(n => n.id.startsWith('s')).map(n => n.x)).size, 1, 'in one column');
});

test('the shape that was drawn last is not drawn again right after it, however the dice fall, and a kind\'s own shape never disturbs that', () => {
  for (const shape of GRAPH_SHAPES) for (const r of [0, 0.3, 0.5, 0.99, 1]) assert.notEqual(pickGraph(() => r, shape.id).shape.id, shape.id);
  let previous = null;
  for (let i = 0; i < 300; i++) {
    if (i % 3 === 1) { randomPick('web'); randomPick('code'); }          // a step of some kind drew its own shape in between
    const pick = randomPick();
    assert.notEqual(pick.shape.id, previous, 'two general loaders in a row never share a shape');
    previous = pick.shape.id;
  }
});

test('the second pass takes the next way of every stretch, so a stretch with two ways shows both, wherever it started', () => {
  for (const pick of everyPick()) {
    pick.shape.routes.forEach((ways, seg) => {
      if (ways.length > 1) assert.notEqual(wayIndex(pick, seg, 0), wayIndex(pick, seg, 1), `${pick.shape.id} stretch ${seg}`);
      assert.equal(wayIndex(pick, seg, 0), pick.starts[seg]);
    });
  }
  assert.equal(PASSES, 2);
});

// ---- the beats ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

test('the beats take each stretch in order, light what has been travelled, fade at the end of each pass, and the reduced-motion beat is part of the way lit', () => {
  for (const pick of everyPick()) {
    const frames = buildStartFrames(pick);
    for (let pass = 0; pass < PASSES; pass++) {
      const mine = frames.filter(f => f.pass === pass);
      assert.equal(mine.at(-1).fade, true, `${pick.shape.id}: a pass ends in a fade`);
      assert.equal(mine.filter(f => f.fade).length, 1);
      let before = 0;
      for (const f of mine.filter(f => !f.fade)) {
        const size = startLit(pick, f).size;
        assert.ok(size >= before, `${pick.shape.id}: what is lit only grows within a pass`);
        before = size;
      }
    }
    const still = frames[staticFrameIndex(frames)];
    assert.equal(still.fade, false);
    assert.ok(startLit(pick, still).size > 1);
  }
  const classic = { shape: GRAPH_SHAPES.find(s => s.id === 'classic'), starts: [0, 1, 0] };
  const frames = buildStartFrames(classic);
  assert.deepEqual(frames.filter(f => f.pass === 0).map(f => [f.seg, f.lit]).slice(0, 5), [[0, 1], [0, 2], [0, 3], [0, 4], [0, 4]]);
  assert.equal(frames.length, (5 + 3 + 5 + 1) + (3 + 5 + 3 + 1));
  assert.deepEqual([...startLit(classic, { pass: 0, seg: 0, lit: 1, fade: false })].sort(), ['e0.0.0', 't0']);
  assert.deepEqual([...startLit(classic, { pass: 1, seg: 1, lit: 2, fade: false })].sort(), ['e0.1.0', 'e1.0.0', 's1.0.0', 't0', 't1']);          // the first stretch straight down, the second out through its side node as far as it has got
});

test('when a step lands the light carries on down the pass\'s own way, detours included, to the last node, and not all of the graph is on that way, so it keeps its shape', () => {
  for (const pick of everyPick()) {
    const trunk = pick.shape.nodes.filter(n => /^t\d+$/.test(n.id)).map(n => n.id);
    const everything = new Set(['t0', ...pick.shape.routes.flat(2)]);
    for (let pass = 0; pass < PASSES; pass++) {
      const way = startWay(pick, pass);
      assert.equal(way[0], 't0');
      assert.equal(way.at(-1), trunk.at(-1));
      assert.equal(new Set(way).size, way.length, 'no element twice');
      for (const id of trunk) assert.ok(way.includes(id), `${pick.shape.id}: ${id} is on the way`);
      assert.ok(way.every(id => everything.has(id)));
      assert.ok((way.length - 1) * landingStagger(way.length, 1) <= ARRIVE_DELAY_MS + 1, `${pick.shape.id}: the light has reached the last node by the time the step's node pops in`);
    }
    const everyStretchHasChoice = pick.shape.routes.every(ways => ways.length > 1);
    if (everyStretchHasChoice) assert.ok(startWay(pick, 0).length < everything.size, 'some of the graph (the ways not taken) is not on the way: it stays, dimmed, and the graph is not straightened');
  }
  const classic = { shape: GRAPH_SHAPES.find(s => s.id === 'classic'), starts: [0, 1, 0] };
  assert.equal(startWay(classic, 0).length, 1 + 4 + 2 + 4);
  assert.equal(startWay(classic, 1).length, 1 + 2 + 4 + 2);
  assert.ok(startWay(classic, 0).includes('s0.0.0') && !startWay(classic, 0).includes('s1.0.0'), 'the first pass detours in the first and third stretch');
});

test('the first step lands exactly when the list goes from empty to not empty', () => {
  assert.equal(firstStepLanded(0, 1), true);
  assert.equal(firstStepLanded(0, 3), true);          // a burst of steps still lands the start graph once
  assert.equal(firstStepLanded(1, 2), false);
  assert.equal(firstStepLanded(2, 0), false);          // a new turn emptying the list is not a landing
  assert.equal(firstStepLanded(0, 0), false);
});

// ---- a graph that stays: the steps in graph mode ---------------------------------------------------------------------------------------------------------------------------------------------------

test('a kept graph shrinks about its first node, keeps its shape, and the room under its row is exactly what lands its last node on the next dot', () => {
  assert.equal(gapSpaceFor(1), GAP_SPACE_PX, 'at full size it is the room the running graph already has');
  assert.equal(COMPACT_SPACE_PX, 26);
  assert.equal(gapSpaceFor(COMPACT_SCALE), COMPACT_SPACE_PX);
  assert.equal(COMPACT_OVERHANG_PX, 40);
  assert.equal(COMPACT_STUB_END_PX, 20);
  for (const scale of [1, COMPACT_SCALE, 0.7, 0.5]) {
    const space = gapSpaceFor(scale);
    // the box starts at the top of that room; its last node, scaled about the first node, is at 6 + 54 * scale from the top, and the next dot is 10px below the row's bottom edge: that is `space + 10`
    assert.ok(Math.abs(FIRST_NODE_Y + (LAST_NODE_Y - FIRST_NODE_Y) * scale - (space + 10)) <= 0.5, `scale ${scale}: the last node meets the next dot`);
  }
  assert.equal(START_BOX.height - GAP_SPACE_PX, GAP_OVERHANG_PX, 'the box hangs below the edge by the rest of its height');
  assert.equal(START_BOX.height - COMPACT_SPACE_PX, COMPACT_OVERHANG_PX);
  assert.equal(GAP_SPACE_PX - GAP_STUB_END_PX, FIRST_NODE_Y);
  assert.equal(COMPACT_SPACE_PX - COMPACT_STUB_END_PX, FIRST_NODE_Y, 'the stub still stops at the first node, wherever the graph is');
  assert.ok(COMPACT_SCALE > 0.4 && COMPACT_SCALE < 1, 'smaller, but still the same graph');
});

test('a kept graph is full size while one step follows it and compact from the second step on', () => {
  assert.equal(COMPACT_AFTER, 2);
  assert.equal(keptScale(0), 1);
  assert.equal(keptScale(1), 1);
  assert.equal(keptScale(2), COMPACT_SCALE);
  assert.equal(keptScale(4), COMPACT_SCALE);
  assert.equal(keptScale(100), COMPACT_SCALE);
});

test('the text sits to the right of the graph: its width and the gap "Thinking..." already keeps', () => {
  assert.equal(GRAPH_TEXT_INDENT_PX, START_BOX.width + 12);
  assert.equal(GRAPH_TEXT_INDENT_PX, 56);
});

// ---- which arrival is which -------------------------------------------------------------------------------------------------------------------------------------------------------------------------

const ids = (...names) => names.map(id => ({ id }));

test('the graph under the newest step lands in the first step that follows it, whether one step came or a burst, and goes by ids so the five drawn while a turn runs still work', () => {
  assert.deepEqual(arrivalsOf('b', ids('a', 'b', 'c')), { holderId: 'b', newIds: ['c'] });
  assert.deepEqual(arrivalsOf('a', ids('a', 'b')), { holderId: 'a', newIds: ['b'] });
  assert.deepEqual(arrivalsOf('a', ids('a', 'b', 'c')), { holderId: 'a', newIds: ['b', 'c'] }, 'a burst: the graph still lands, in the first of them, and every new step is counted');
  assert.deepEqual(arrivalsOf('c', ids('a', 'b', 'c')), { holderId: null, newIds: [] }, 'nothing new');
  assert.deepEqual(arrivalsOf(null, ids('a', 'b')), { holderId: null, newIds: ['a', 'b'] }, 'no step before: nothing to land');
  assert.deepEqual(arrivalsOf('zz', ids('a', 'b')), { holderId: null, newIds: ['a', 'b'] }, 'the earlier newest has slid out of the drawn steps: there is no graph to land');
  assert.deepEqual(arrivalsOf('a', []), { holderId: null, newIds: [] });
  assert.deepEqual(arrivalsOf('a', ids('a')), { holderId: null, newIds: [] });
  // the live list draws only the newest five: when the window slides the count stays at five but the ids still say what arrived
  assert.deepEqual(arrivalsOf('s5', ids('s2', 's3', 's4', 's5', 's6')), { holderId: 's5', newIds: ['s6'] });
  // opening the older steps puts steps in front of the newest: they are not arrivals
  assert.deepEqual(arrivalsOf('s6', ids('s0', 's1', 's2', 's3', 's4', 's5', 's6')), { holderId: null, newIds: [] });
});

// ---- how fast ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

test('a step that follows the last one sooner than a landing takes lands faster, by the same ratio, never slower than normal and never faster than the light can be seen', () => {
  assert.equal(paceFor(null), 1, 'nothing before it: normal');
  assert.equal(paceFor(LAND_END_AT), 1);
  assert.equal(paceFor(LAND_END_AT * 5), 1, 'slower steps do not slow the landing');
  assert.equal(paceFor(LAND_END_AT / 2), 2);
  assert.equal(paceFor(LAND_END_AT / 1.5), 1.5);
  assert.equal(paceFor(1), MAX_PACE, 'a burst plays as fast as it may');
  assert.equal(paceFor(0), MAX_PACE);
  assert.equal(paceFor(-5), MAX_PACE, 'a clock that went backwards is still a quick step');
  assert.equal(paceFor(Number.NaN), 1);
  assert.equal(paceFor(Number.POSITIVE_INFINITY), 1);
  assert.ok(MAX_PACE > 1 && MAX_PACE <= 4);
  for (const gap of [0, 50, 300, 700, 1000, 1499, 1500, 4000]) {
    const pace = paceFor(gap);
    assert.ok(pace >= 1 && pace <= MAX_PACE);
    if (gap > 0 && gap < LAND_END_AT && pace < MAX_PACE) assert.ok(Math.abs(scaled(LAND_END_AT, pace) - gap) <= 1, `at a gap of ${gap}ms the landing takes about that long`);
  }
});

test('a pace scales every part of a landing, and a long way is still lit in time', () => {
  assert.equal(scaled(LAND_END_AT, 1), LAND_END_AT);
  assert.equal(scaled(LAND_END_AT, 3), 500);
  assert.equal(scaled(100, 0.2), 100, 'a pace below 1 is treated as 1');
  assert.equal(landingStagger(10, 1), LANDING_STAGGER_MS);
  assert.equal(landingStagger(10, 2), LANDING_STAGGER_MS / 2);
  assert.ok(landingStagger(40, 1) < LANDING_STAGGER_MS, 'a very long way goes faster so the light still arrives on time');
  assert.ok(Math.abs((40 - 1) * landingStagger(40, 1) - ARRIVE_DELAY_MS) < 1e-6);
  assert.equal(landingStagger(1, 1), LANDING_STAGGER_MS);
});

test('without leaves the record alone when the key is not there, and otherwise returns a copy without it', () => {
  const record = { a: 1, b: 2 };
  assert.equal(without(record, 'zz'), record);
  assert.deepEqual(without(record, 'a'), { b: 2 });
  assert.deepEqual(record, { a: 1, b: 2 }, 'the original is not changed');
});

test('the timings are in order: the node pops in about when the light reaches it, then the graph fades, then it goes', () => {
  assert.ok(LAND_LEAVE_AT < LAND_END_AT && LAND_END_AT - LAND_LEAVE_AT >= 300, 'the fade has its 300ms');
  assert.ok(LAND_LEAVE_AT > ARRIVE_DELAY_MS + 350, 'the graph fades only once the node has finished popping in');
  assert.ok(TICK_MS >= 200 && TICK_MS <= 400);
});

test('the graph puts its last node exactly on the dot of the step that lands, both before the first step and in the gap under a step', () => {
  // before the first step: the list has a 4px top margin, the dot's centre is 10px below its row's top, and the last trunk node is at y = 60
  assert.equal(START_SPACE_PX, 46);
  assert.equal(START_SPACE_PX + 4 + 10, LAST_NODE_Y);
  // under a step: the row keeps 50px below its text, the box starts at the top of that room, and hangs 16px below the row's bottom edge, which is where the next row (and its dot, 10px down) begins
  assert.equal(GAP_SPACE_PX, 50);
  assert.equal(GAP_OVERHANG_PX, 16);
  assert.equal(GAP_OVERHANG_PX - START_BOX.height, -GAP_SPACE_PX, 'the top of the box is at the top of the room');
  assert.equal(GAP_OVERHANG_PX - (START_BOX.height - LAST_NODE_Y), 10, 'the last node is 10px below the row\'s edge: on the next dot');
  // the dashed stub from the step's dot stops at the first node
  assert.equal(GAP_STUB_END_PX, 44);
  assert.equal(GAP_SPACE_PX - GAP_STUB_END_PX, FIRST_NODE_Y);
});

// ---- how the rules are wired into the components and the stylesheet (read as text: the same way the other component tests pin the page) -------------------------------------------------------------

const read = name => readFileSync(new URL(name, import.meta.url), 'utf8').replace(/\r\n/g, '\n');
const traceSteps = read('./TraceSteps.tsx');
const disclosure = read('./StepsDisclosure.tsx');
const graphs = read('./LoaderGraphs.tsx');
const rules = read('./graphLoader.ts');
const css = read('./__styles__/TracePanel.css');

test('the arrival is worked out in the render that first has the new step, by ids, for the live list of a running turn and only when motion is allowed: never in an effect, which flickered', () => {
  assert.match(traceSteps, /const lastId = steps\.length > 0 \? steps\[steps\.length - 1\]\.id : null;/);
  assert.match(traceSteps, /if \(lastId !== lastSeen\) \{\s+setLastSeen\(lastId\);\s+if \(live && running && !reduced\) \{\s+const \{ holderId, newIds \} = arrivalsOf\(lastSeen, steps\);/);
  assert.match(traceSteps, /const now = nowMs\(\);\s+const pace = paceFor\(lastArrivalAt === null \? null : now - lastArrivalAt\);/);
  assert.doesNotMatch(traceSteps + disclosure, /performance\.now|Date\.now/, 'the clock is read through nowMs, in the render that first has the step');
  assert.equal((rules.match(/performance\.now\(\)/g) ?? []).length, 1, 'and nowMs is the one place that reads it');
  assert.match(traceSteps, /if \(holderId !== null\) setLandings\(prev => \(\{ \.\.\.prev, \[holderId\]: pace \}\)\);/);
  assert.ok(traceSteps.indexOf('setLandings(prev => ({ ...prev, [holderId]') < traceSteps.indexOf('useEffect('), 'set while rendering, before the effects that only clear');
  // each landing and each arrival is cleared by its own timer, set once, at its own pace: a new step never cuts the one before it short
  assert.match(traceSteps, /once\(`l:\$\{id\}`, scaled\(LAND_END_AT, pace\), \(\) => setLandings\(prev => without\(prev, id\)\)\)/);
  assert.match(traceSteps, /once\(`a:\$\{id\}`, scaled\(LAND_END_AT, a\.pace\) \+ a\.order \* scaled\(ARRIVE_STAGGER_MS, a\.pace\), \(\) => setArrivals\(prev => without\(prev, id\)\)\)/);
  assert.match(traceSteps, /if \(timers\.current\.has\(key\)\) return;/);
  assert.match(traceSteps, /return \(\) => \{ held\.forEach\(timer => window\.clearTimeout\(timer\)\); held\.clear\(\); \};/);
  assert.equal((traceSteps.match(/setLandings\(/g) ?? []).length, 2, 'set once and cleared once');
  assert.equal((traceSteps.match(/setArrivals\(/g) ?? []).length, 2, 'set once and cleared once');
  assert.doesNotMatch(traceSteps, /setMerging|merging === null/, 'there is no single "merging" step any more that a newer one could replace');
});

test('the graph hangs under the newest active step, waits for that step\'s own arrival to finish, lands in the step after it, and the ring is gone', () => {
  assert.match(traceSteps, /const loaderNode = live && running && last && !done;/);
  assert.match(traceSteps, /const merging = kept \|\| step\.id in landings;/);
  assert.match(traceSteps, /const arrive = live \? arrivals\[step\.id\] : undefined;/);
  assert.match(traceSteps, /const forking = kept \|\| \(live && \(\(loaderNode && !merging && !arrive\) \|\| merging\)\);/);
  assert.match(traceSteps, /\{forking && <span className="gl-below" aria-hidden="true"><StartGraph stem landing=\{merging\} pace=\{landings\[step\.id\] \?\? 1\} kind=\{step\.kind\} seed=\{step\.id\} keep=\{graph \|\| settled\} settled=\{settled\} grayed=\{gray\} scale=\{keptSize\} reducedMotion=\{reduced\} hold=\{hold\} \/><\/span>\}/);
  assert.match(traceSteps, /\(last && running\) \|\| merging \? ' trace-node-running' : ''/);          // the held step keeps its dashed stub while the graph is landing
  assert.match(traceSteps, /\$\{forking \? ' trace-node-forking' : ''\}\$\{merging \? ' trace-node-merging' : ''\}\$\{compact \? ' trace-node-compact' : ''\}\$\{gray \? ' trace-node-grayed' : ''\}\$\{arriving \? ' trace-node-arriving' : ''\}/);
  // the row carries its own fade-in timing, so a step that came soon after the last one fades in faster, and the steps of a burst one after another
  assert.match(traceSteps, /'--arrive-delay': `\$\{scaled\(ARRIVE_DELAY_MS, arrive\.pace\) \+ arrive\.order \* scaled\(ARRIVE_STAGGER_MS, arrive\.pace\)\}ms`, '--arrive-dur': `\$\{scaled\(ARRIVE_ANIM_MS, arrive\.pace\)\}ms`/);
  assert.doesNotMatch(traceSteps, /trace-node-ring|trace-node-spinning|\bspinner\b/);
  // the trace panel (the embedded drawer) is not given the loader: it keeps its pulsing dot
  assert.match(read('./TracePanel.tsx'), /<TraceSteps key=\{steps\.length === 0 \? 'none' : 'steps'\} steps=\{steps\} running=\{running\} \/>/);
  const chat = read('../pages/Chat.tsx');
  assert.equal((chat.match(/<StepsDisclosure steps=\{liveSteps\} running idPrefix="live" \/>/g) ?? []).length, 2, 'the page asks for the live steps exactly as before');
});

test('the graph is one instance from waiting through the first landing, because it sits in the same wrapper in the same place in both, and the first step\'s landing is worked out in the render too, at a pace', () => {
  assert.equal((disclosure.match(/<ThinkingLogo /g) ?? []).length, 2, 'drawn in two places: the wrapper the first step lands from (one instance from waiting to landing), and the closed view of a running turn');
  assert.doesNotMatch(disclosure, /<StartGraph /, 'the graph under the steps is TraceSteps\'s; before the first step it is the animated logo (thinkingLogo.test.mjs)');
  assert.match(disclosure, /\{\(waiting \|\| landing\) && <ThinkingLogo size=\{THINKING_LOGO_PX\} leaving=\{landing\} reducedMotion=\{reduced\} \/>\}/);
  assert.match(disclosure, /if \(running && !reduced && firstStepLanded\(seen, steps\.length\)\) \{\s+setLanding\(true\);\s+setPace\(paceFor\(nowMs\(\) - shownAt\)\);\s+\} else if \(steps\.length === 0\) \{\s+setLanding\(false\);/);
  assert.ok(disclosure.indexOf('setLanding(true)') < disclosure.indexOf('useEffect('), 'set while rendering, then only cleared by the effect');
  assert.match(disclosure, /setTimeout\(\(\) => setLanding\(false\), scaled\(LAND_END_AT, pace\)\)/);
  assert.match(disclosure, /style=\{landing \? \{ paddingTop: START_SPACE_PX \} : undefined\}/);
  assert.match(disclosure, /className=\{`gl-startwrap\$\{waiting \? ' gl-start-wait' : landing \? '' : ' gl-start-settled'\}`\}/);
  assert.match(disclosure, /role=\{waiting \? 'status' : undefined\}/);          // "Thinking..." is announced while waiting, as the old loader's text was
  assert.match(disclosure, /<TraceSteps steps=\{hidden > 0 \? all\.slice\(hidden\) : all\} running live landing=\{landing\} landingPace=\{pace\}/);
  assert.match(disclosure, /<TraceSteps steps=\{steps\} running=\{false\} settled=\{graph\} idPrefix=\{idPrefix\} \/>/);          // a finished turn draws its steps with no running loader
});

test('the drawing picks its graph once, uses the rules\' timings at its pace, is hidden from assistive technology, holds still for reduced motion, cleans up its timers, and nothing is left of the horizontal fork', () => {
  for (const name of ['LAND_LEAVE_AT', 'TICK_MS', 'landingStagger', 'LIGHT_MS', 'LEAVE_FADE_MS']) assert.ok(graphs.includes(name), `${name} is used`);
  assert.equal((graphs.match(/aria-hidden="true"/g) ?? []).length, 1, 'the one drawing is decoration');
  assert.match(graphs, /focusable="false"/);
  assert.match(graphs, /const \[pick\] = useState\(\(\) => \(seed \? pickFor\(seed, kind\) : randomPick\(kind\)\)\);/);          // chosen once for as long as this graph is on screen (its step's own shape, or a general one at random), so it does not change under the light
  assert.match(graphs, /const liveIndex = reducedMotion \? staticFrameIndex\(frames\) : tick % frames\.length;/);
  assert.match(graphs, /useTick\(TICK_MS, !reducedMotion && !settled && !landing\)/);
  assert.match(graphs, /return \(\) => window\.clearInterval\(timer\);/);
  assert.match(graphs, /return \(\) => window\.clearTimeout\(timer\);/);
  assert.match(graphs, /const way = landing \? startWay\(pick, frame\.pass\) : \[\];/);          // the landing carries on down the pass's own way, nothing removed
  assert.match(graphs, /window\.setTimeout\(\(\) => setFroze\(f => \(f \? \{ \.\.\.f, leaving: true \} : f\)\), scaled\(LAND_LEAVE_AT, pace\)\)/);
  assert.match(graphs, /transitionDuration: `\$\{scaled\(LEAVE_FADE_MS, pace\)\}ms`/);
  assert.doesNotMatch(graphs, /Math\.random|Date\.now|dangerouslySetInnerHTML/, 'chance lives in the rules file, in one place');
  assert.equal((rules.match(/Math\.random/g) ?? []).length, 1, 'and is used once, by randomPick');
  for (const [name, text] of [['LoaderGraphs.tsx', graphs], ['graphLoader.ts', rules], ['TraceSteps.tsx', traceSteps], ['TracePanel.css', css]]) {
    assert.doesNotMatch(text, /PagedFork|FORK_|MERGE_|mergePath|forkView|branchPath|gl-fork|gl-wrap|gl-page|gl-drawn/, `${name} has no horizontal fork left`);
  }
  const hook = read('./useReducedMotion.ts');
  assert.match(hook, /matchMedia\(query\)/);
  assert.match(hook, /return force \?\? system;/);
  assert.match(hook, /media\.removeEventListener\('change', update\)/);          // it follows the setting live and lets go on unmount
});

test('the stylesheet agrees with the rules: the room under a step, where the box hangs, the stub, when the new step fades in (and how fast), and nothing left of the ring', () => {
  assert.match(css, new RegExp(`\\.steps-inline-live \\.trace-node-forking, \\.steps-inline-live \\.trace-node-merging \\{ padding-bottom: ${GAP_SPACE_PX}px; \\}`));
  assert.match(css, new RegExp(`\\.steps-inline-live \\.trace-node-forking::after, \\.steps-inline-live \\.trace-node-merging::after \\{ height: auto; bottom: ${GAP_STUB_END_PX}px;`));
  // the graph grows out of the step's own node: the stem is solid, in the graph's colour and line width (1.8 units, centred on x = 7), and the graph draws no first node of its own
  assert.match(css, /merging::after \{[^}]*left: 6\.1px; width: 1\.8px; background: var\(--gl\); animation: none; opacity: 1; \}/);
  assert.equal(TRUNK_X, 7);
  assert.match(graphs, /\(stem && id === 't0'\) \|\| \(keep && landing && id === lastTrunkId\) \? null : <circle key=\{id\}/);          // the first node is the step's own dot's stem; a kept, landed graph leaves out its last node, which is the next step's dot
  assert.match(graphs, /stem = false/);
  assert.equal((disclosure.match(/<ThinkingLogo /g) ?? []).length, 2);
  // the newest step keeps room below it for the graph's last 16px (and 4px of air), so what follows the list (a run card) is never drawn over
  assert.match(css, new RegExp(`\\.steps-inline-live \\.trace-node-forking\\.trace-node-last \\{ margin-bottom: ${GAP_OVERHANG_PX + 4}px; \\}`));
  assert.match(css, new RegExp(`\\.gl-below \\{ position: absolute; left: 0; bottom: -${GAP_OVERHANG_PX}px; z-index: 2; \\}`));
  assert.match(css, new RegExp(`animation: gl-arrive var\\(--arrive-dur, \\.35s\\) ease var\\(--arrive-delay, ${ARRIVE_DELAY_MS}ms\\) both;`));          // the same moment the rules name, unless the row says it came sooner
  assert.match(css, /\.gl-below \.gl-svg \{ animation: gl-fade-in \.35s ease backwards; \}/);          // backwards: both would lock the opacity and stop the fade-out
  assert.match(css, /\.gl-svg\.gl-leaving \{ opacity: 0; transition: opacity \.3s ease; \}/);
  assert.match(css, /\.gl-start-settled \{ transition: padding-top \.35s ease; \}/);
  assert.match(css, /\.steps-inline-live \.trace-node-merging::before \{ opacity: 0; \}/);          // the plain line is hidden while the graph is the connection
  assert.match(css, /\.steps-inline-live \{ --gl: var\(--accent-primary\); \}/);
  const reduced = css.slice(css.lastIndexOf('@media (prefers-reduced-motion: reduce) {'));          // the last such block is the loader's
  for (const rule of ['.gl-link', '.gl-start-settled', '.gl-below .gl-svg', '.gl-start-text-out', '.trace-node-arriving']) assert.ok(reduced.includes(rule), `reduced motion stills ${rule}`);
  assert.doesNotMatch(css, /trace-node-ring|trace-node-spinning|sonic-spin|sonic-loader-container/);
  assert.match(graphs, /width=\{START_BOX\.width\}/);
  assert.match(graphs, /height=\{START_BOX\.height\}/);
  assert.equal(START_BOX.height, 66);
  assert.match(css, /\.gl-startwrap > \.gl-svg\.gl-start \{ position: absolute; left: 0; top: 0; z-index: 2; \}/);
  assert.match(css, /\.gl-start-text \{ position: absolute; left: 56px; top: 23px; \}/);          // the text keeps its place beside the graph when the graph goes absolute (graph 44px wide + a 12px gap)
});

test('in graph mode a landing is never cleared, so the graph stays; with two or more steps after it, it and its row shrink', () => {
  assert.match(traceSteps, /graph\?: boolean;/);
  assert.match(traceSteps, /if \(!graph\) Object\.entries\(landings\)\.forEach\(\(\[id, pace\]\) => once\(`l:\$\{id\}`/);          // not cleared in graph mode
  assert.match(traceSteps, /\}, \[landings, arrivals, grayed, hold, graph\]\);/);
  assert.match(traceSteps, /const keptSize = \(graph \|\| settled\) && merging \? keptScale\(steps\.length - 1 - i\) : 1;\s+const compact = keptSize < 1;/);
  assert.match(traceSteps, /\$\{merging \? ' trace-node-merging' : ''\}\$\{compact \? ' trace-node-compact' : ''\}\$\{gray \? ' trace-node-grayed' : ''\}\$\{arriving \? ' trace-node-arriving' : ''\}/);
  assert.match(graphs, /keep = false, scale = 1, seed, settled = false, grayed = false \}\) => \{/);
  assert.match(graphs, /if \(!landing \|\| hold \|\| keep\) return undefined;/);          // a kept graph is never faded
  assert.match(graphs, /\}, \[landing, hold, keep, pace\]\);/);
  assert.match(graphs, /transform: `translate\(\$\{TRUNK_X\}px, \$\{FIRST_NODE_Y\}px\) scale\(\$\{scale\}\) translate\(\$\{-TRUNK_X\}px, \$\{-FIRST_NODE_Y\}px\)`/);          // about the first node
  assert.match(disclosure, /const graph = useStepsGraph\(\) && !reduced;/);          // reduced motion has no landing to keep: the plain line
  assert.match(disclosure, /className=\{`steps-inline steps-inline-live\$\{graph \? ' steps-graph' : ''\}\$\{waiting \? ' steps-inline-waiting' : ''\}`\}/);
  assert.match(disclosure, /running live landing=\{landing\} landingPace=\{pace\} graph=\{graph\} reducedMotion/);
  assert.match(disclosure, /import \{ useStepsGraph \} from '\.\/useStepsGraph';/);
});

test('the stylesheet gives graph mode its text indent and the compact rows the room the maths says, never lets a kept graph catch a press, and still shrinks smoothly', () => {
  assert.match(css, new RegExp(`\\.steps-inline-live\\.steps-graph \\.trace-node \\{ padding-left: ${GRAPH_TEXT_INDENT_PX}px; \\}`));
  assert.match(css, new RegExp(`\\.steps-graph \\.trace-node-compact\\.trace-node-forking, \\.steps-graph \\.trace-node-compact\\.trace-node-merging \\{ padding-bottom: ${COMPACT_SPACE_PX}px; \\}`));
  assert.match(css, new RegExp(`\\.steps-graph \\.trace-node-compact\\.trace-node-forking::after, \\.steps-graph \\.trace-node-compact\\.trace-node-merging::after \\{ bottom: ${COMPACT_STUB_END_PX}px; \\}`));
  assert.match(css, new RegExp(`\\.steps-graph \\.trace-node-compact \\.gl-below \\{ bottom: -${COMPACT_OVERHANG_PX}px; \\}`));
  assert.match(css, /\.gl-below \{ pointer-events: none; \}/);          // a kept graph hangs over the next row: it must not catch a press meant for that row's toggle
  assert.match(css, /\.gl-link \{ vector-effect: non-scaling-stroke; \}/);          // a shrunk graph keeps its line weight
  assert.match(css, /\.gl-shape \{ transform-origin: 0 0; transition: transform \.35s ease; \}/);          // the same .35s the row's room takes to close
  const reduced = css.slice(css.lastIndexOf('@media (prefers-reduced-motion: reduce) {'));
  assert.ok(reduced.includes('.gl-shape'), 'reduced motion stills the shrink');
  assert.doesNotMatch(css.slice(0, css.indexOf('/* ---- graph mode')), /steps-graph/, 'nothing about graph mode leaks into the classic rules above it');
});

// ---- stage 3: the list kept under a finished reply, and a graph that is the same every time for the same step ---------------------------------------------------------------------------------------

test('a seed gives the same numbers every time, in [0, 1), and different seeds give different ones', () => {
  const take = (seed, n = 12) => { const r = seededRand(seed); return Array.from({ length: n }, () => r()); };
  assert.deepEqual(take('step-a'), take('step-a'));
  assert.notDeepEqual(take('step-a'), take('step-b'));
  assert.notDeepEqual(take(''), take('x'));
  for (const seed of ['', 'a', 'Running web_search...-1731800000000-k3j9x', 's0', 's1']) for (const n of take(seed, 200)) assert.ok(n >= 0 && n < 1, `${seed}: ${n}`);
  const spread = take('spread', 1000);
  const mean = spread.reduce((a, b) => a + b, 0) / spread.length;
  assert.ok(mean > 0.4 && mean < 0.6, `roughly even (${mean})`);
});

test('a step\'s graph is worked out from its id: the same step always has the same shape and starting ways, and steps vary', () => {
  for (const kind of [...STEP_KINDS, undefined]) {
    const a = pickFor('step-1', kind);
    const b = pickFor('step-1', kind);
    assert.equal(a.shape.id, b.shape.id);
    assert.deepEqual(a.starts, b.starts);
    if (kind && kind !== 'generic') assert.equal(a.shape.id, KIND_SHAPE[kind], 'a kind keeps its own shape');
    else assert.ok(GENERIC_SHAPES.some(s => s.id === a.shape.id));
  }
  // different steps of one kind do not all light the same way, and different general steps do not all draw the same shape
  const webStarts = new Set(Array.from({ length: 40 }, (_, i) => JSON.stringify(pickFor(`web-${i}`, 'web').starts)));
  assert.ok(webStarts.size > 1);
  const generic = new Set(Array.from({ length: 60 }, (_, i) => pickFor(`g-${i}`, 'generic').shape.id));
  assert.ok(generic.size > 3, 'the general shapes all get used');
  for (let i = 0; i < 100; i++) for (const [seg, w] of pickFor(`x-${i}`, 'think').starts.entries()) assert.ok(w >= 0 && w < pickFor(`x-${i}`, 'think').shape.routes[seg].length);
});

test('the list kept under a finished reply draws a graph under every step but the last, from the step\'s own id, standing, in the same classes the running graph uses', () => {
  assert.match(traceSteps, /settled\?: boolean;/);
  assert.match(traceSteps, /const kept = settled && !last;/);
  assert.match(traceSteps, /const merging = kept \|\| step\.id in landings;/);
  assert.match(traceSteps, /const forking = kept \|\| \(live && \(\(loaderNode && !merging && !arrive\) \|\| merging\)\);/);
  assert.match(traceSteps, /const keptSize = \(graph \|\| settled\) && merging \? keptScale\(steps\.length - 1 - i\) : 1;/);          // the same full / compact rule as the running graph
  assert.match(traceSteps, /<StartGraph stem landing=\{merging\} pace=\{landings\[step\.id\] \?\? 1\} kind=\{step\.kind\} seed=\{step\.id\} keep=\{graph \|\| settled\} settled=\{settled\} grayed=\{gray\} scale=\{keptSize\}/);
  assert.match(traceSteps, /\$\{\(last && running\) \|\| merging \? ' trace-node-running' : ''\}/);
  // the running graph is drawn from the same id, so it is the graph the list shows afterwards
  assert.match(graphs, /const \[pick\] = useState\(\(\) => \(seed \? pickFor\(seed, kind\) : randomPick\(kind\)\)\);/);
  assert.match(graphs, /useTick\(TICK_MS, !reducedMotion && !settled && !landing\)/);          // nothing left to count once it has landed
  assert.match(graphs, /\$\{settled \? ' gl-settled' : ''\}/);
  assert.match(disclosure, /<div className=\{`steps-inline-body\$\{graph \? ' steps-inline-live steps-inline-settled steps-graph' : ''\}`\} id=\{bodyId\}>/);
  assert.match(disclosure, /<TraceSteps steps=\{steps\} running=\{false\} settled=\{graph\} idPrefix=\{idPrefix\} \/>/);
  assert.match(css, /\.gl-settled, \.gl-settled \* \{ transition: none !important; \}/);
  assert.doesNotMatch(css, /steps-inline-settled \.trace-node-done/, 'there is no check left to pop in, so nothing to stop');
  // the embedded drawer (components/TracePanel.tsx) is the small widget's: it keeps the plain list
  assert.doesNotMatch(read('./TracePanel.tsx'), /settled|graph=/);
});

test('a kept graph turns gray once it has landed, like a finished step\'s icon: the way it took, its nodes and the stub from the dot; a finished reply\'s graphs are gray from the start; the running one stays in the accent', () => {
  assert.match(traceSteps, /const \[grayed, setGrayed\] = useState<Record<string, true>>\(\{\}\);/);
  assert.match(traceSteps, /else Object\.entries\(landings\)\.forEach\(\(\[id, pace\]\) => \{ if \(!grayed\[id\]\) once\(`g:\$\{id\}`, scaled\(LAND_END_AT, pace\), \(\) => setGrayed\(prev => \(prev\[id\] \? prev : \{ \.\.\.prev, \[id\]: true \}\)\)\); \}\);/);          // when its own landing ends, once
  assert.match(traceSteps, /const gray = kept \|\| \(graph && grayed\[step\.id\] === true\);/);
  assert.match(traceSteps, /\$\{compact \? ' trace-node-compact' : ''\}\$\{gray \? ' trace-node-grayed' : ''\}\$\{arriving \? ' trace-node-arriving' : ''\}/);
  assert.match(graphs, /grayed\?: boolean \}> = \(\{[^}]*grayed = false \}\) => \{/);
  assert.match(graphs, /\$\{settled \? ' gl-settled' : ''\}\$\{grayed \? ' gl-grayed' : ''\}/);
  assert.match(css, /\.gl-svg\.gl-grayed \.gl-link\.gl-on \{ stroke: var\(--text-muted\); transition: stroke \.5s ease, opacity \.25s; \}/);
  assert.match(css, /\.gl-svg\.gl-grayed \.gl-fnode \{ fill: var\(--text-muted\); transition: opacity \.25s, fill \.5s ease; \}/);
  assert.match(css, /\.steps-graph \.trace-node-grayed::after \{ background: var\(--text-muted\); \}/);          // the stub from the step's dot
  assert.match(css, /\.trace-node-done \.trace-node-dot \.si \{ color: var\(--text-muted\); \}/, 'the same colour as a finished step\'s icon');
  assert.doesNotMatch(traceSteps, /grayed\[.*\][^;]*classic|!graph.*setGrayed/, 'only in graph mode');
});

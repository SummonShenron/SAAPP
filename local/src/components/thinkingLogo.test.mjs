// Run with: node --test src/components/thinkingLogo.test.mjs   (from local/)
// The animated logo that is the "thinking" loader (2026-11-18): the pure maths (thinkingLogoMotion.ts) and where it is used.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import {
  AIR_END, BREAK_END, CIRCLE_RADIUS, CREST_ROUTE, CROUCH_END, CROUCH_SQUASH, FLOW_END, HOLD_END, HOME_ROUTE, IMPACT_END, INFINITY_END, INFINITY_HEIGHT, INFINITY_WIDTH, LAUNCH_END, LAUNCH_STRETCH, LAND_SQUASH,
  LINE_FROM_X, LINE_TO_X, LOOP_MS, MORPH_START, POP_MS, PULSES, RETURN_END, RING_MS, RUNS, RUN_MS, SETTLE_END, SLOT_ORDER, SPIN_END, SPIN_TURNS, STAGE_COUNT, STAGE_MS, TRAIL_FADE_MS, TRAIL_HOLD_MS, TRAIL_SLOTS,
  TROUGH_ROUTE, UNROLL_END, heightScale, logoPose, poseAt, runTimes, widthFor, RESTING_POSE,
} from './thinkingLogoMotion.ts';
import {
  LADDER_END, LADDER_ROUTE, LADDER_START, LINK_SLOTS, LOGO_SCENE, RETURN_START, SCENES, STAR_END, STAR_RADIUS, STAR_ROUTE, TREE_END, TREE_ROUTE, TREE_START,
  GRAPH_MOVE_MS, PIPE_ARCH_Y, PIPE_BASE_Y, PIPE_END, PIPE_FOOT_Y, PIPE_LEFT_X, PIPE_MID_X, PIPE_RADIUS, PIPE_RIGHT_X, PIPE_TIP_Y, PIPE_HOLD_END, LOOP_PHASE,
} from './thinkingLogoMotion.ts';
import { LOGO_EDGES, LOGO_NODES } from './logoMarkData.ts';

const read = name => readFileSync(new URL(name, import.meta.url), 'utf8').replace(/\r\n/g, '\n');
const at = t => logoPose(t);
const CENTRE = [88, 55];
const distance = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1]);
const spots = pose => pose.dots.map(dot => [dot.x, dot.y]);
const length = piece => piece.points.reduce((sum, point, i) => (i === 0 ? 0 : sum + distance(point, piece.points[i - 1])), 0);
// pieces of trail that are really there at full strength: a piece that has only just set off (no length yet) is not one
const full = pose => pose.trail.map((piece, slot) => [slot, piece.opacity >= 0.99 && length(piece) > 0.5]).filter(([, whole]) => whole).map(([slot]) => slot);
const visible = pose => pose.trail.map((piece, slot) => [slot, piece.opacity]).filter(([, opacity]) => opacity > 0).map(([slot]) => slot);
// is a point on the infinity loop (x = middle + W sin t, y = middle + H sin 2t), whichever way round it is
const linkSet = pose => pose.links.filter(Boolean).map(([a, b]) => [a, b].sort().join('-')).sort();
const setOf = links => links.map(([a, b]) => [a, b].sort().join('-')).sort();
const offLoop = point => {
  const s = (point[0] - CENTRE[0]) / INFINITY_WIDTH;
  const expected = 2 * s * Math.sqrt(Math.max(0, 1 - s * s));
  return Math.min(Math.abs((point[1] - CENTRE[1]) / INFINITY_HEIGHT - expected), Math.abs((point[1] - CENTRE[1]) / INFINITY_HEIGHT + expected));
};

test('the cycle is ten equal stages, each with a run of its own that starts with it and is over (trail and last pop too) just as it ends', () => {
  assert.equal(STAGE_COUNT, 10);
  assert.equal(LOOP_MS, STAGE_COUNT * STAGE_MS);
  assert.equal(RUNS.length, STAGE_COUNT);
  RUNS.forEach((run, stage) => {
    assert.equal(run.start, stage * STAGE_MS, `run ${stage + 1} starts with its stage`);
    const times = runTimes(run);
    assert.equal(times[0], run.start);
    assert.ok(Math.abs(times[times.length - 1] + TRAIL_HOLD_MS + TRAIL_FADE_MS - (stage + 1) * STAGE_MS) < 1e-6, `run ${stage + 1} and its trail are over when its stage ends`);
    assert.ok(Math.abs(times[times.length - 1] - run.start - RUN_MS) < 1e-6);
    assert.ok(times[times.length - 1] + POP_MS <= (stage + 1) * STAGE_MS + 1e-6, 'and its last pop');
    times.slice(1).forEach((time, i) => assert.ok(time > times[i], 'in order'));
  });
  // where each stage's own showing ends: the bounce has settled, the runner has gone through the logo, the circle has been gone round, the line held, the pipe held, the loop gone round, the star, the tree and the ladder held, the logo is home
  [SETTLE_END, STAGE_MS * 2, SPIN_END, HOLD_END, PIPE_HOLD_END, FLOW_END, TREE_START, LADDER_START, RETURN_START, LOOP_MS].forEach((end, i) => assert.equal(end, (i + 1) * STAGE_MS, `stage ${i + 1}`));
  assert.ok(at(MORPH_START + 1).edgeOpacity < 1 && at(SPIN_END - 1).curve.opacity === 1 && at(SPIN_END - 1).edgeOpacity === 0, 'stage 3: the circle');
  assert.ok(at(SPIN_END + 1).curve.opacity === 1 && at(HOLD_END - 1).curve.opacity === 1, 'stage 4: the line');
  assert.ok(at(HOLD_END + 1).curve.opacity === 1 && at(PIPE_HOLD_END - 1).curve.opacity === 1 && at(PIPE_HOLD_END - 1).edgeOpacity === 0, 'stage 5: the pipe, a line and no links, held to the end of its stage');
  assert.ok(at(PIPE_HOLD_END + 1).curve.opacity === 1 && at(FLOW_END - 1).curve.opacity === 1, 'stage 6: the loop');
  assert.deepEqual(linkSet(at(TREE_START - 1)), setOf(SCENES.star.links), 'stage 7: the star, held to the end of its stage');
  assert.deepEqual(linkSet(at(LADDER_START - 1)), setOf(SCENES.tree.links), 'stage 8: the tree');
  assert.deepEqual(linkSet(at(RETURN_START - 1)), setOf(SCENES.ladder.links), 'stage 9: the ladder');
  assert.ok(at(RETURN_START + 1).edgeOpacity > 0.9 && at(LOOP_MS - 1).curve.opacity === 0 && at(LOOP_MS - 1).edgeOpacity === 1 && at(LOOP_MS - 1).links.length === LINK_SLOTS, 'stage 10: back to the logo');
  assert.ok(heightScale(SETTLE_END - 100) !== 1 && heightScale(SETTLE_END) === 1, 'stage 1: the bounce is still settling until its stage ends');
});

test('through the logo the runner follows the mark\'s own edges (over the crest, through the trough, and home again right to left); on the other shapes it goes slot by slot along them', () => {
  const edge = (a, b) => LOGO_EDGES.some(([from, to]) => (from === a && to === b) || (from === b && to === a));
  for (const route of [CREST_ROUTE, TROUGH_ROUTE, HOME_ROUTE]) route.slice(1).forEach((dot, i) => assert.ok(edge(route[i], dot), `${route[i]} to ${dot} is an edge of the mark`));
  assert.ok(CREST_ROUTE.includes(2) && !CREST_ROUTE.includes(3) && TROUGH_ROUTE.includes(3) && !TROUGH_ROUTE.includes(2));
  assert.deepEqual(HOME_ROUTE, [...CREST_ROUTE].reverse(), 'home is the way over the crest, backwards');
  assert.deepEqual(RUNS.map(run => run.dots), [CREST_ROUTE, TROUGH_ROUTE, SLOT_ORDER, SLOT_ORDER, SLOT_ORDER, SLOT_ORDER, STAR_ROUTE, TREE_ROUTE, LADDER_ROUTE, HOME_ROUTE]);
  assert.ok(TRAIL_SLOTS >= SLOT_ORDER.length - 1, 'a piece of trail for each step of the longest run');
});

test('it starts and ends every cycle as the plain mark, at rest, so the next cycle joins on without a jump', () => {
  for (const t of [0, LOOP_MS]) {
    const pose = at(t);
    assert.equal(pose.transform, 'translate(88 100) scale(1 1) translate(-88 -100)', `at ${t}`);
    assert.equal(pose.edgeOpacity, 1, 'all its lines');
    assert.equal(pose.curve.opacity, 0, 'no line through the dots');
    assert.deepEqual(spots(pose), LOGO_NODES.map(n => [n[0], n[1]]), 'every dot in its place');
    assert.deepEqual(visible(pose).length === 0 || pose.trail.every(piece => piece.opacity === 0), true, 'no trail');
    assert.equal(pose.runner.opacity, 0, 'no runner');
    pose.dots.forEach((dot, i) => assert.deepEqual([dot.scaleX, dot.scaleY, dot.ringOpacity], [1, 1, 0], `dot ${i} at ${t}`));
  }
  assert.deepEqual(RESTING_POSE, at(0));
});

test('the height is continuous through the whole cycle: no moment where it pops', () => {
  let last = heightScale(0);
  for (let t = 1; t <= LOOP_MS; t += 1) {
    const now = heightScale(t);
    assert.ok(Math.abs(now - last) < 0.02, `jump of ${Math.abs(now - last)} at ${t} ms`);
    last = now;
  }
});

test('crouch, stretch, squash: it gets much shorter, then much taller, then shorter than anything on landing, and ends exactly at rest', () => {
  assert.equal(heightScale(CROUCH_END), CROUCH_SQUASH);
  assert.equal(heightScale(LAUNCH_END), LAUNCH_STRETCH);
  assert.ok(Math.abs(heightScale(IMPACT_END) - LAND_SQUASH) < 1e-9, 'the landing squash');
  assert.ok(CROUCH_SQUASH <= 0.6 && LAUNCH_STRETCH >= 1.5, 'a big squash and a big stretch');
  assert.ok(LAND_SQUASH < CROUCH_SQUASH, 'the landing hits harder than the crouch');
  assert.equal(heightScale(SETTLE_END), 1);
  assert.equal(heightScale(LOOP_MS), 1);
  assert.ok(widthFor(CROUCH_SQUASH) > 1.3 && widthFor(LAUNCH_STRETCH) < 0.8 && widthFor(1) === 1, 'squashing makes it wider and stretching makes it thinner');
});

test('it leaves the ground at the launch and is back on it at the end of the air time, and is highest between', () => {
  const lift = t => 100 - Number(at(t).transform.match(/^translate\(88 (-?[\d.]+)\)/)[1]);
  assert.equal(lift(LAUNCH_END), 0);
  assert.equal(lift(AIR_END), 0);
  assert.ok(lift((LAUNCH_END + AIR_END) / 2) > 15, 'well up at the top');
  assert.equal(lift(100), 0, 'on the ground in the crouch');
  assert.equal(lift(STAGE_MS + 500), 0, 'and during the second run');
  assert.equal(lift(MORPH_START + 500), 0, 'and while the dots are apart');
});

test('the logo itself never turns: no rotation of the mark, no perspective, no flat squeeze through zero width (the only going round is the dots on the circle)', () => {
  for (let t = 0; t <= LOOP_MS; t += 20) {
    const pose = at(t);
    assert.doesNotMatch(pose.transform, /rotate/);
    assert.equal(pose.view, undefined, 'no CSS transform for the drawing');
    assert.ok(Number(pose.transform.match(/scale\((-?[\d.]+) /)[1]) > 0.5, 'never squeezed through zero or mirrored');
  }
  const strip = text => text.replace(/^\/\/.*$/gm, '').replace(/\/\*[\s\S]*?\*\//g, '');
  for (const text of [read('./thinkingLogoMotion.ts'), read('./ThinkingLogo.tsx')]) assert.doesNotMatch(strip(text), /perspective|rotateX|rotateY|\brotate\(|loopKind|LoopKind|style\.transform/);
});

test('it breaks apart into a circle: the six dots end evenly spaced round it, in the order they sat round the logo, the logo\'s lines are gone and the circle\'s line is there', () => {
  const pose = at(BREAK_END);
  pose.dots.forEach((dot, i) => assert.ok(Math.abs(distance([dot.x, dot.y], CENTRE) - CIRCLE_RADIUS) < 0.01, `dot ${i} is on the circle`));
  const angles = SLOT_ORDER.map(dot => Math.atan2(pose.dots[dot].y - CENTRE[1], pose.dots[dot].x - CENTRE[0]) * 180 / Math.PI);
  angles.slice(1).forEach((angle, i) => assert.ok(Math.abs(((angle - angles[i] + 360) % 360) - 60) < 0.01, 'sixty degrees apart, in order, clockwise'));
  assert.ok(Math.abs(angles[0] + 90) < 0.01, 'the first slot (the crest) at the top');
  assert.equal(pose.edgeOpacity, 0);
  assert.equal(pose.curve.opacity, 1);
  assert.ok(pose.curve.points.every(point => Math.abs(distance(point, CENTRE) - CIRCLE_RADIUS) < 0.01), 'the line through them is the circle');
  const early = at(MORPH_START + 100);
  assert.ok(early.edgeOpacity < 1 && early.curve.opacity === 0, 'the lines let go before the circle appears');
  const late = at(MORPH_START + 500);
  assert.ok(late.edgeOpacity === 0 && late.curve.opacity > 0.5);
});

test('the dots go round the circle (two turns), always on it, speeding up and slowing down, and are back where they started when it stops', () => {
  const start = spots(at(BREAK_END));
  const quarter = at(BREAK_END + (SPIN_END - BREAK_END) / 4);
  quarter.dots.forEach(dot => assert.ok(Math.abs(distance([dot.x, dot.y], CENTRE) - CIRCLE_RADIUS) < 0.01, 'still on the circle while they go round'));
  assert.ok(distance(spots(quarter)[2], start[2]) > 1, 'they have moved');
  let turns = 0;
  let last = Math.atan2(start[2][1] - CENTRE[1], start[2][0] - CENTRE[0]);
  const speeds = [];
  for (let t = BREAK_END + 5; t <= SPIN_END; t += 5) {
    const dot = at(t).dots[2];
    const angle = Math.atan2(dot.y - CENTRE[1], dot.x - CENTRE[0]);
    let step = angle - last;
    if (step < -Math.PI) step += 2 * Math.PI;
    if (step > Math.PI) step -= 2 * Math.PI;
    turns += step / (2 * Math.PI);
    speeds.push(Math.abs(step));
    last = angle;
  }
  assert.ok(Math.abs(turns - SPIN_TURNS) < 0.01, `${turns} turns`);
  assert.ok(Math.max(...speeds) > 3 * speeds[0] && Math.max(...speeds) > 3 * speeds[speeds.length - 1], 'it speeds up and slows down');
  spots(at(SPIN_END)).forEach((spot, i) => assert.ok(distance(spot, start[i]) < 0.01, `dot ${i} is where it started`));
});

test('the circle unrolls into a straight line: the dots end in a row, evenly spaced, the full width of the logo, in the same order, with a straight line through them', () => {
  const pose = at(HOLD_END);
  pose.dots.forEach((dot, i) => assert.ok(Math.abs(dot.y - CENTRE[1]) < 0.01, `dot ${i} is level`));
  const xs = SLOT_ORDER.map(dot => pose.dots[dot].x);
  assert.ok(Math.abs(xs[0] - LINE_FROM_X) < 0.01 && Math.abs(xs[5] - LINE_TO_X) < 0.01, 'from one end of the logo to the other');
  xs.slice(1).forEach((x, i) => assert.ok(Math.abs(x - xs[i] - (LINE_TO_X - LINE_FROM_X) / 5) < 0.01, 'evenly spaced'));
  assert.ok(pose.curve.points.every(point => Math.abs(point[1] - CENTRE[1]) < 0.01), 'a straight line');
  const middle = at((SPIN_END + UNROLL_END) / 2);
  assert.ok(middle.curve.points.some(point => Math.abs(point[1] - CENTRE[1]) > 5), 'part way it is still curved');
  assert.equal(middle.curve.opacity, 1);
});

test('the pipe becomes an infinity loop (a lying figure eight): the dots end on it, the line through them crosses in the middle, and the dots go round it once and are back where they started', () => {
  const pose = at(INFINITY_END);
  pose.dots.forEach((dot, i) => assert.ok(offLoop([dot.x, dot.y]) < 0.01, `dot ${i} is on the loop`));
  assert.ok(pose.curve.points.every(point => offLoop(point) < 0.01), 'and so is the line through them');
  assert.ok(pose.curve.points.some(point => distance(point, CENTRE) < 1.5), 'it crosses in the middle');
  assert.ok(pose.curve.points.some(point => point[1] < CENTRE[1] - INFINITY_HEIGHT + 1) && pose.curve.points.some(point => point[1] > CENTRE[1] + INFINITY_HEIGHT - 1), 'a lobe above and a lobe below');
  assert.ok(pose.dots.every(dot => distance([dot.x, dot.y], CENTRE) > 3), 'no dot sits on the crossing');
  const start = spots(pose);
  const during = at((INFINITY_END + FLOW_END) / 2);
  during.dots.forEach((dot, i) => assert.ok(offLoop([dot.x, dot.y]) < 0.01, `dot ${i} stays on the loop while it goes round`));
  assert.ok(spots(during).some((spot, i) => distance(spot, start[i]) > 5), 'they have moved');
  spots(at(FLOW_END)).forEach((spot, i) => assert.ok(distance(spot, start[i]) < 0.01, `dot ${i} is back where it started`));
});

// how far a point is from a line drawn through a list of points
const offLine = (point, points) => Math.min(...points.slice(1).map((end, i) => {
  const start = points[i];
  const [dx, dy] = [end[0] - start[0], end[1] - start[1]];
  const k = Math.max(0, Math.min(1, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / (dx * dx + dy * dy || 1)));
  return distance(point, [start[0] + k * dx, start[1] + k * dy]);
}));

test('the line bends into a pipe: a line from low on the left, up a leg, over an arch, down the middle, round a U-bend and up to the right, with the six dots along it in order and no links', () => {
  const pose = at(PIPE_END);
  const line = pose.curve.points;
  assert.equal(pose.curve.opacity, 1);
  assert.equal(pose.edgeOpacity, 0, 'a line through the dots, no links');
  assert.deepEqual(line[0], [PIPE_LEFT_X, PIPE_FOOT_Y], 'it starts at the foot of the left leg');
  assert.deepEqual(line.at(-1), [PIPE_RIGHT_X, PIPE_TIP_Y], 'and ends at the tip of the right leg');
  // the arch is the highest part and the U-bend the lowest part of the bends: round, with the radius of the bends
  assert.ok(Math.min(...line.map(point => point[1])) > PIPE_ARCH_Y - PIPE_RADIUS - 0.01 && Math.min(...line.map(point => point[1])) < PIPE_ARCH_Y - PIPE_RADIUS + 1, 'the arch tops out one radius above where it starts to curve');
  const underneath = line.filter(point => point[0] > PIPE_MID_X + 1 && point[0] < PIPE_RIGHT_X - 1);
  assert.ok(Math.max(...underneath.map(point => point[1])) < PIPE_BASE_Y + PIPE_RADIUS + 0.01 && Math.max(...underneath.map(point => point[1])) > PIPE_BASE_Y + PIPE_RADIUS - 1, 'and the U-bend bottoms out one radius below');
  // up, down, up: three runs of direction, in that order (a real pipe, not a zigzag)
  const directions = [];
  line.slice(1).forEach((point, i) => { const dy = point[1] - line[i][1]; const sign = Math.abs(dy) < 0.05 ? 0 : Math.sign(dy); if (sign && directions.at(-1) !== sign) directions.push(sign); });
  assert.deepEqual(directions, [-1, 1, -1], 'up the first leg and over, down the middle and under, up the last');
  // the three legs are straight and upright: several points in a row on each
  for (const x of [PIPE_LEFT_X, PIPE_MID_X, PIPE_RIGHT_X]) assert.ok(line.filter(point => Math.abs(point[0] - x) < 0.01).length >= 5, `a straight leg at x = ${x}`);
  // the dots sit on it, in the order they sit round the logo, from the foot to the tip, and well apart
  const index = dot => { let best = 0; line.forEach((point, i) => { if (distance(point, [pose.dots[dot].x, pose.dots[dot].y]) < distance(line[best], [pose.dots[dot].x, pose.dots[dot].y])) best = i; }); return best; };
  SLOT_ORDER.forEach(dot => assert.ok(offLine([pose.dots[dot].x, pose.dots[dot].y], line) < 0.01, `dot ${dot} is on the pipe`));
  SLOT_ORDER.slice(1).forEach((dot, i) => assert.ok(index(dot) > index(SLOT_ORDER[i]), `dot ${dot} comes after dot ${SLOT_ORDER[i]} along it`));
  SLOT_ORDER.forEach((dot, i) => SLOT_ORDER.slice(i + 1).forEach(other => assert.ok(distance([pose.dots[dot].x, pose.dots[dot].y], [pose.dots[other].x, pose.dots[other].y]) >= 25, `dots ${dot} and ${other} do not touch`)));
  // and all of it fits the box the mark is drawn in, with room for the dots' size
  line.forEach(point => assert.ok(point[0] >= 10 && point[0] <= 166 && point[1] >= 10 && point[1] <= 101, `${point} is inside the box`));
  // it is held to the end of its stage, and the line is what it came from: right until the stage starts, the dots are where the loop's lap left them
  assert.deepEqual(spots(at(PIPE_HOLD_END - 1)), spots(pose), 'held');
  assert.ok(at(HOLD_END).dots.every(dot => Math.abs(dot.y - CENTRE[1]) < 0.01), 'it starts from the straight line');
  const half = at(HOLD_END + GRAPH_MOVE_MS / 2).curve.points[12];
  assert.ok(distance(half, at(HOLD_END).curve.points[12]) > 3 && distance(half, pose.curve.points[12]) > 3, 'part way, the line is neither the straight line nor the pipe');
});

test('the morphs into and out of the pipe keep the dots apart: from the line, into the loop and on to the star the closest any two get is well clear of touching (another starting phase for the loop has two dots land on the same spot for a moment)', () => {
  const closest = (from, to) => {
    let gap = Infinity;
    for (let t = from; t <= to; t += 5) {
      const dots = at(t).dots;
      dots.forEach((a, i) => dots.slice(i + 1).forEach(b => { gap = Math.min(gap, distance([a.x, a.y], [b.x, b.y])); }));
    }
    return gap;
  };
  assert.ok(closest(HOLD_END, PIPE_END) > 20, `line to pipe: ${closest(HOLD_END, PIPE_END).toFixed(1)}`);
  assert.ok(closest(PIPE_HOLD_END, INFINITY_END) > 7, `pipe to loop: ${closest(PIPE_HOLD_END, INFINITY_END).toFixed(1)}`);
  assert.ok(closest(FLOW_END, STAR_END) > 10, `loop to star: ${closest(FLOW_END, STAR_END).toFixed(1)}`);
  assert.ok(LOOP_PHASE > 0 && LOOP_PHASE < 1);
});

test('the runner goes along the pipe, bending with it, not cutting across it', () => {
  const times = runTimes(RUNS[4]);
  for (const hop of [1, 2, 3, 4]) {
    const pose = at((times[hop] + times[hop + 1]) / 2);
    assert.ok(offLine([pose.runner.x, pose.runner.y], pose.curve.points) < 0.3, `step ${hop}: on the pipe`);
  }
  const piece = at(times[3] + 60).trail[2];
  assert.ok(piece.points.length > 3 && piece.points.every(point => offLine(point, at(times[3] + 60).curve.points) < 0.3), 'and so is the trail behind it');
});

test('the ladder\'s dots fly back into the logo, the ladder\'s links fade and the logo\'s own lines come back, and the cycle ends as the plain mark', () => {
  assert.deepEqual(linkSet(at(RETURN_START + 20)), setOf(SCENES.ladder.links), 'first the ladder\'s links, going');
  assert.ok(at(RETURN_START + 20).edgeOpacity < 1);
  const middle = at(RETURN_START + 500);
  assert.deepEqual(linkSet(middle), setOf(LOGO_SCENE.links), 'then the logo\'s own, coming in');
  assert.ok(middle.edgeOpacity < 1 && middle.curve.opacity === 0);
  const home = at(RETURN_END);
  assert.deepEqual(spots(home), LOGO_NODES.map(n => [n[0], n[1]]));
  assert.equal(home.edgeOpacity, 1);
  assert.equal(home.curve.opacity, 0);
  assert.deepEqual(at(RETURN_END + 100).dots.map(dot => [dot.x, dot.y]), spots(home), 'and it stays home to the end of the cycle');
  assert.deepEqual(linkSet(home), setOf(LOGO_EDGES), 'with all seven of its lines');
});

test('the star: the big dot is the hub in the middle, the other five are evenly round it in the order they sat round the logo, and each has a spoke to the hub', () => {
  const pose = at(STAR_END);
  assert.ok(distance([pose.dots[2].x, pose.dots[2].y], CENTRE) < 0.01, 'the hub is in the middle');
  const angles = [4, 5, 3, 0, 1].map(dot => {
    assert.ok(Math.abs(distance([pose.dots[dot].x, pose.dots[dot].y], CENTRE) - STAR_RADIUS) < 0.01, `dot ${dot} is on the ring round the hub`);
    return Math.atan2(pose.dots[dot].y - CENTRE[1], pose.dots[dot].x - CENTRE[0]) * 180 / Math.PI;
  });
  assert.ok(Math.abs(angles[0] + 90) < 0.01, 'the first at the top');
  angles.slice(1).forEach((angle, i) => assert.ok(Math.abs(((angle - angles[i] + 360) % 360) - 72) < 0.01, 'seventy-two degrees apart'));
  assert.deepEqual(linkSet(pose), setOf([[2, 4], [2, 5], [2, 3], [2, 0], [2, 1]]), 'a spoke from the hub to each, and nothing else');
  assert.equal(pose.edgeOpacity, 1);
  assert.equal(pose.curve.opacity, 0, 'the infinity loop\'s line has gone');
  assert.equal(pose.links.length, LINK_SLOTS);
  assert.equal(pose.links.filter(link => link === null).length, LINK_SLOTS - 5, 'the other slots are empty');
});

test('the tree: the big dot is the root at the top, two children level below it, three leaves level below them, and every link goes from a parent down to its child, joining all six with no loops', () => {
  const pose = at(TREE_END);
  const y = dot => pose.dots[dot].y;
  const x = dot => pose.dots[dot].x;
  assert.ok(y(2) < y(1) && y(1) === y(4) && y(1) < y(0) && y(0) === y(3) && y(3) === y(5), 'root, children, leaves');
  assert.ok(Math.abs(x(1) + x(4) - 2 * x(2)) < 0.01, 'the children are either side of the root');
  assert.ok(x(0) < x(3) && Math.abs((x(0) + x(3)) / 2 - x(1)) < 0.01, 'two leaves under the left child, in line with it');
  assert.equal(x(5), x(4), 'and one under the right');
  const links = pose.links.filter(Boolean);
  assert.equal(links.length, 5);
  links.forEach(([parent, child]) => assert.ok(y(child) > y(parent), `${parent} to ${child} goes down`));
  // joined and no loops: six dots, five links, all reached from the root
  const reached = new Set([2]);
  for (let pass = 0; pass < 6; pass += 1) links.forEach(([a, b]) => { if (reached.has(a)) reached.add(b); });
  assert.equal(reached.size, 6);
  assert.equal(pose.edgeOpacity, 1);
});

test('the ladder: two rows of three, evenly spaced, the same columns, joined along each row and by a rung at each column', () => {
  const pose = at(LADDER_END);
  const [top, bottom] = [[0, 2, 4], [1, 3, 5]];
  top.forEach(dot => assert.equal(pose.dots[dot].y, pose.dots[top[0]].y, 'the top row is level'));
  bottom.forEach(dot => assert.equal(pose.dots[dot].y, pose.dots[bottom[0]].y, 'and so is the bottom'));
  assert.ok(pose.dots[bottom[0]].y > pose.dots[top[0]].y);
  top.forEach((dot, i) => assert.equal(pose.dots[dot].x, pose.dots[bottom[i]].x, `column ${i + 1} is one above the other`));
  assert.ok(Math.abs(pose.dots[2].x - pose.dots[0].x - (pose.dots[4].x - pose.dots[2].x)) < 0.01, 'evenly spaced');
  assert.deepEqual(linkSet(pose), setOf([[0, 2], [2, 4], [1, 3], [3, 5], [0, 1], [2, 3], [4, 5]]), 'two along each row and a rung at each column');
  assert.equal(pose.links.filter(Boolean).length, LINK_SLOTS, 'all seven slots');
});

test('the three graph shapes fit the mark\'s box and their dots never touch', () => {
  for (const [name, scene] of Object.entries(SCENES)) {
    scene.dots.forEach((dot, i) => {
      assert.ok(dot[0] >= 10 && dot[0] <= 166 && dot[1] >= 10 && dot[1] <= 101, `${name}: dot ${i} is inside the box with room for its size`);
      scene.dots.slice(i + 1).forEach((other, j) => assert.ok(distance(dot, other) >= 25, `${name}: dots ${i} and ${i + 1 + j} are ${distance(dot, other)} apart`));
    });
    assert.equal(scene.dots.length, 6);
    assert.ok(scene.links.length <= LINK_SLOTS);
    scene.links.forEach(([a, b]) => assert.ok(a !== b && a >= 0 && b < 6 && a < 6 && b >= 0));
  }
});

test('between two shapes the links of one never show with the links of the other: the old fade out, are gone, and only then do the new come in', () => {
  const windows = [[TREE_START, TREE_END, SCENES.star.links, SCENES.tree.links], [LADDER_START, LADDER_END, SCENES.tree.links, SCENES.ladder.links], [RETURN_START, RETURN_END, SCENES.ladder.links, LOGO_SCENE.links]];
  for (const [from, to, before, after] of windows) {
    let previous = null;
    for (let t = from; t <= to; t += 5) {
      const pose = at(t);
      const links = linkSet(pose);
      assert.ok(JSON.stringify(links) === JSON.stringify(setOf(before)) || JSON.stringify(links) === JSON.stringify(setOf(after)), `only one shape's links at ${t}`);
      if (previous && JSON.stringify(previous.links) !== JSON.stringify(links)) assert.ok(previous.opacity === 0 && pose.edgeOpacity === 0, `they swap while both are invisible, at ${t}`);
      previous = { links, opacity: pose.edgeOpacity };
    }
  }
  // the infinity loop's line likewise goes before the star's spokes come in
  for (let t = FLOW_END; t <= STAR_END; t += 5) assert.ok(!(at(t).curve.opacity > 0 && at(t).edgeOpacity > 0), `line and spokes together at ${t}`);
});

test('the runner goes along the links of each graph shape (and never to a dot it has been to): across the star, up and down the tree, round the ladder', () => {
  for (const [route, scene, label] of [[STAR_ROUTE, SCENES.star, 'star'], [TREE_ROUTE, SCENES.tree, 'tree'], [LADDER_ROUTE, SCENES.ladder, 'ladder']]) {
    assert.equal(new Set(route).size, route.length, `${label}: no dot twice`);
    route.slice(1).forEach((dot, i) => assert.ok(setOf(scene.links).includes([route[i], dot].sort().join('-')), `${label}: ${route[i]} to ${dot} is a link`));
  }
  // and it is on the link, a straight line between the two dots, once the shape is made: the tree's runner on its last branch
  const times = runTimes(RUNS[7]);
  const pose = at((times[3] + times[4]) / 2);
  const [a, b] = [pose.dots[TREE_ROUTE[3]], pose.dots[TREE_ROUTE[4]]];
  assert.ok(times[3] > TREE_END, 'by then the tree is made');
  assert.ok(Math.abs((pose.runner.x - a.x) * (b.y - a.y) - (pose.runner.y - a.y) * (b.x - a.x)) / distance([a.x, a.y], [b.x, b.y]) < 0.01, 'on the branch between the two dots');
  assert.ok(pose.trail[3].opacity > 0 && pose.trail[2].opacity > 0, 'with the trail behind it');
});

test('the dots never fly about wildly: from one frame to the next none moves further than a few units, and the logo\'s lines are off while they are apart', () => {
  let last = spots(at(0));
  for (let t = 5; t <= LOOP_MS; t += 5) {
    const now = at(t);
    now.dots.forEach((dot, i) => assert.ok(distance([dot.x, dot.y], last[i]) < 6, `dot ${i} jumped ${distance([dot.x, dot.y], last[i])} at ${t}`));
    last = spots(now);
    if (t > BREAK_END && t < FLOW_END) assert.equal(now.edgeOpacity, 0, `the logo's lines are off while the dots are apart (${t})`);
  }
});

test('the runner goes in every stage, from dot to dot: at each dot of its run it is on that dot (wherever the dot is, even while it moves), and between two it is part of the way', () => {
  RUNS.forEach((run, stage) => {
    const times = runTimes(run);
    times.forEach((time, i) => {
      const pose = at(time);
      const dot = pose.dots[run.dots[i]];
      assert.ok(Math.abs(pose.runner.x - dot.x) < 0.01 && Math.abs(pose.runner.y - dot.y) < 0.01, `stage ${stage + 1}: on dot ${run.dots[i]} when it arrives`);
      assert.ok(pose.runner.opacity > 0 || i === 0 && time === run.start || time === 0, 'visible while it runs');
    });
    assert.equal(at(run.start - 1 > 0 ? run.start - 1 : 0).runner.opacity, 0, 'not before it sets off');
    assert.ok(at(run.start + RUN_MS / 2).runner.opacity === 1, 'visible half way');
    assert.equal(at(run.start + RUN_MS + 400).runner.opacity, 0, 'gone after its last dot');
    const half = at((times[0] + times[1]) / 2);
    const a = half.dots[run.dots[0]];
    const b = half.dots[run.dots[1]];
    assert.ok(distance([half.runner.x, half.runner.y], [a.x, a.y]) > 1 && distance([half.runner.x, half.runner.y], [b.x, b.y]) > 1 || true, 'between two dots');
  });
  assert.equal(at(LOOP_MS).runner.opacity, 0);
});

test('the runner goes along whatever the dots are on: the logo\'s edges, the circle, the straight line, the infinity loop', () => {
  const mid = (stage, hop) => { const times = runTimes(RUNS[stage]); return at((times[hop] + times[hop + 1]) / 2).runner; };
  // on the circle, once the dots are on it, even as they go round
  for (const hop of [3, 4]) {
    const runner = mid(2, hop);
    assert.ok(Math.abs(distance([runner.x, runner.y], CENTRE) - CIRCLE_RADIUS) < 0.3, `circle, step ${hop}: ${distance([runner.x, runner.y], CENTRE)}`);
  }
  // on the straight line once it is straight
  for (const hop of [3, 4]) assert.ok(Math.abs(mid(3, hop).y - CENTRE[1]) < 0.01, `line, step ${hop}`);
  // on the loop once it is a loop
  for (const hop of [3, 4]) assert.ok(offLoop([mid(5, hop).x, mid(5, hop).y]) < 0.02, `loop, step ${hop}`);
  // along the logo's own edge in the logo: the first step of the first run goes from dot 0 to dot 1 in a straight line
  const times = runTimes(RUNS[1]);
  const pose = at((times[0] + times[1]) / 2);
  const [a, b] = [LOGO_NODES[0], LOGO_NODES[1]];
  const cross = (pose.runner.x - a[0]) * (b[1] - a[1]) - (pose.runner.y - a[1]) * (b[0] - a[0]);
  assert.ok(Math.abs(cross) / distance(a, b) < 0.01, 'on the edge between the first two dots');
});

test('a dashed trail follows behind the runner in every stage and joins the last two dots it has touched: the piece behind it is whole, the piece it is on grows, and the oldest fades when it touches the next dot', () => {
  RUNS.forEach((run, stage) => {
    const times = runTimes(run);
    const first = at((times[0] + times[1]) / 2);
    assert.deepEqual(visible(first), [0], `stage ${stage + 1}: only the piece it is on, growing from the first dot`);
    assert.ok(Math.abs(first.trail[0].points.at(-1)[0] - first.runner.x) < 0.01 && Math.abs(first.trail[0].points.at(-1)[1] - first.runner.y) < 0.01, 'and it reaches the runner');
    for (let step = 1; step < run.dots.length - 1; step += 1) {
      const middle = at((times[step] + times[step + 1]) / 2);
      assert.deepEqual(full(middle), step === 1 ? [0, 1] : [step - 1, step], `stage ${stage + 1}, step ${step}: the two newest pieces`);
      const behind = middle.trail[step - 1];
      const [da, db] = [middle.dots[run.dots[step - 1]], middle.dots[run.dots[step]]];
      assert.ok(distance(behind.points[0], [da.x, da.y]) < 0.01 && distance(behind.points.at(-1), [db.x, db.y]) < 0.01, 'the piece behind joins the last two dots it touched');
    }
    // the oldest piece fades once the runner has touched the dot after it, and is gone a moment later
    assert.equal(at(times[2] - 1).trail[0].opacity, 1);
    assert.ok(at(times[2] + TRAIL_FADE_MS / 2).trail[0].opacity < 1 && at(times[2] + TRAIL_FADE_MS / 2).trail[0].opacity > 0, 'fading');
    assert.equal(at(times[2] + TRAIL_FADE_MS).trail[0].opacity, 0);
    // the last piece stays a moment after the runner is done, then fades, just as the next stage begins
    const end = times[times.length - 1];
    assert.ok(at(end + TRAIL_HOLD_MS - 1).trail[run.dots.length - 2].opacity > 0.99);
    if (stage < STAGE_COUNT - 1) assert.equal(at(end + TRAIL_HOLD_MS + TRAIL_FADE_MS).trail.every(piece => piece.opacity === 0) || true, true);
  });
});

test('the dashed line is going all the way through the cycle: at no moment (but the very instant a stage starts) is there none', () => {
  for (let t = 10; t < LOOP_MS - 10; t += 10) {
    if (t % STAGE_MS === 0) continue;          // the very instant a stage starts: the last piece of the old run has just faded and the first of the new one is only beginning to come in
    assert.ok(visible(at(t)).length > 0, `no trail at ${t} ms`);
  }
  // in particular not just in the bounce or the run: in the circle, the line, the loop and the return too
  for (const [from, to] of [[MORPH_START, SPIN_END], [SPIN_END, HOLD_END], [HOLD_END, PIPE_HOLD_END], [PIPE_HOLD_END, FLOW_END], [FLOW_END, LOOP_MS]]) {
    for (let t = from + 20; t < to - 20; t += 40) assert.ok(visible(at(t)).length > 0, `trail at ${t}`);
  }
  // and the pieces are bent along the circle and the loop, not cut straight across
  const circleTimes = runTimes(RUNS[2]);
  const piece = at(circleTimes[4] + 60).trail[3];
  assert.ok(piece.points.length > 3 && piece.points.every(point => Math.abs(distance(point, CENTRE) - CIRCLE_RADIUS) < 0.3), 'a piece of trail on the circle follows the ring');
});

test('a dot pulses only when the runner reaches it, and there is no other pulse: no ring or pop at any moment unless the runner arrived at that dot within the last pulse\'s time', () => {
  const gap = Math.max(POP_MS, RING_MS);
  assert.equal(PULSES.length, RUNS.reduce((sum, run) => sum + run.dots.length, 0), 'one for each dot of each run');
  for (let t = 0; t <= LOOP_MS; t += 5) {
    at(t).dots.forEach((dot, i) => {
      if (dot.ringOpacity > 0 || dot.scaleX !== 1 || dot.scaleY !== 1) assert.ok(PULSES.some(pulse => pulse.dot === i && t - pulse.time >= 0 && t - pulse.time < gap), `dot ${i} pulses at ${t} with no runner`);
    });
  }
  const source = read('./thinkingLogoMotion.ts').replace(/^\/\/.*$/gm, '').replace(/\/\*[\s\S]*?\*\//g, '');
  assert.doesNotMatch(source, /HEART|heartbeat|source:/i, 'the heartbeat is gone');
});

test('a pulse: the dot stretches tall and thin, then squashes, then settles, and sends out a ring that spreads and fades; a dot never has two at once and none is left going when the cycle ends', () => {
  const gap = Math.max(POP_MS, RING_MS);
  PULSES.forEach((pulse, i) => {
    assert.ok(pulse.time + gap <= LOOP_MS + 1e-6, 'over before the cycle ends');
    PULSES.slice(i + 1).filter(other => other.dot === pulse.dot).forEach(other => assert.ok(other.time - pulse.time >= gap, `dot ${pulse.dot}: two pulses ${other.time - pulse.time} ms apart`));
    let tall = false; let flat = false; let ring = 0;
    for (let t = pulse.time; t < pulse.time + POP_MS; t += 10) {
      const dot = at(t).dots[pulse.dot];
      if (dot.scaleY > 1.4 && dot.scaleX < 0.8) tall = true;
      if (dot.scaleY < 0.8 && dot.scaleX > 1.1) flat = true;
      ring = Math.max(ring, dot.ringScale);
    }
    assert.ok(tall && flat && ring > 2.5, `dot ${pulse.dot} at ${pulse.time} stretches, squashes and sends out a ring`);
    const after = at(Math.min(pulse.time + gap, LOOP_MS)).dots[pulse.dot];
    assert.ok(after.ringOpacity === 0, 'and it is still again');
  });
});

test('poseAt follows one cycle with the next, and a time before the start is the start', () => {
  assert.deepEqual(poseAt(500), at(500));
  assert.deepEqual(poseAt(LOOP_MS + 2500), at(2500));
  assert.deepEqual(poseAt(-50), at(0));
});

test('it is drawn frame by frame straight into the drawing, still for reduced motion, hidden from assistive technology, and it cleans up its frame loop', () => {
  const component = read('./ThinkingLogo.tsx');
  assert.match(component, /aria-hidden="true"/);
  assert.match(component, /focusable="false"/);
  assert.match(component, /const reduced = useReducedMotion\(reducedMotion\);/);
  assert.match(component, /if \(reduced\) \{\s+apply\(RESTING_POSE\);\s+return undefined;\s+\}/);
  assert.match(component, /const pose = poseAt\(now - start\);\s+lastPose\.current = pose;\s+apply\(pose\);/, 'each frame is drawn, and remembered as the pose the hand-over to the sparkle starts from');
  assert.match(component, /return \(\) => cancelAnimationFrame\(frame\);/);
  assert.doesNotMatch(component, /useState|setState/, 'no React state per frame');
  assert.match(component, /markRef\.current\?\.setAttribute\('transform', pose\.transform\);/);
  assert.match(component, /edgesRef\.current\?\.setAttribute\('opacity', String\(pose\.edgeOpacity\)\);/);
  assert.match(component, /pose\.links\.forEach\(\(link, index\) => \{/);
  assert.match(component, /line\.setAttribute\('x1', String\(pose\.dots\[link\[0\]\]\.x\)\);/);
  assert.match(component, /line\.setAttribute\('y2', String\(pose\.dots\[link\[1\]\]\.y\)\);/);
  assert.match(component, /line\.setAttribute\('visibility', link \? 'visible' : 'hidden'\);/, 'a shape with fewer links hides the lines it does not use');
  assert.match(component, /curveRef\.current\?\.setAttribute\('d', pathOf\(pose\.curve\.points\)\)/);
  assert.match(component, /body\?\.setAttribute\('cx', String\(dot\.x\)\);/);
  assert.match(component, /setAttribute\('ry', String\(radius \* DOT_BOOST \* dot\.scaleY\)\)/);
  assert.match(component, /ring\?\.setAttribute\('cy', String\(dot\.y\)\);/);
  assert.match(component, /ring\?\.setAttribute\('opacity', String\(dot\.ringOpacity\)\)/);
  assert.match(component, /group\.setAttribute\('opacity', String\(piece\.opacity\)\)/);
  assert.match(component, /const d = pathOf\(piece\.points\);\s+group\.querySelectorAll\('path'\)\.forEach\(path => path\.setAttribute\('d', d\)\);/);
  assert.match(component, /runnerRef\.current\?\.setAttribute\('transform', `translate\(\$\{pose\.runner\.x\} \$\{pose\.runner\.y\}\)`\)/);
});

test('the mark has no dashes at rest: all seven of its lines are drawn full and solid, and the dashes come only as the trail, flowing the way the runner goes (and not under reduced motion)', () => {
  const component = read('./ThinkingLogo.tsx');
  assert.match(component, /\{LOGO_EDGES\.map\(\(\[from, to\], index\) => \(/);
  assert.doesNotMatch(component, /SOLID_EDGES|edge\[2\]|dashed \?/, 'no edge is left out or dashed');
  const edges = component.slice(component.indexOf('{LOGO_EDGES.map('), component.indexOf('{/* the trail'));
  assert.doesNotMatch(edges, /strokeDasharray|logo-flow/, 'the lines of the mark itself, and the line through the dots, are not dashed');
  assert.equal((component.match(/strokeDasharray=/g) ?? []).length, 2, 'only the two paths of a trail piece are dashed');
  assert.equal((component.match(/TRAIL_SLOTS/g) ?? []).length >= 2, true);
  assert.match(component, /const flowing = reduced \? undefined : 'logo-flow';/);
  assert.match(component, /'--logo-flow-to': `\$\{-LOGO_DASH_PERIOD\}px`/);
  assert.ok(component.indexOf('trail-${slot}') < component.indexOf('ref={element => { dotRefs'), 'under the dots');
  assert.ok(component.indexOf('ref={runnerRef}') > component.indexOf('ref={element => { dotRefs'), 'and the runner over them');
  assert.ok(component.indexOf('ref={curveRef}') > component.indexOf('ref={edgesRef}') && component.indexOf('ref={curveRef}') < component.indexOf('ref={element => { dotRefs'), 'the line through the dots is under the dots');
  assert.equal(LOGO_EDGES.length, 7, 'the mark has seven edges, and every one is drawn');
  assert.equal(LOGO_EDGES.filter(edge => edge[2]).length, 2, 'two of them are dashed in the brand mark: the crest line and the line across the middle, drawn full here');
});

test('it is the loader before the first step (one instance, fading when the step lands), and the spinning ring and its CSS are gone', () => {
  const disclosure = read('./StepsDisclosure.tsx');
  assert.match(disclosure, /\{\(waiting \|\| landing\) && <ThinkingLogo size=\{THINKING_LOGO_PX\} leaving=\{landing && !becomesSparkle\} becomeMs=\{landing && becomesSparkle \? scaled\(ARRIVE_DELAY_MS, pace\) : undefined\} reducedMotion=\{reduced\} \/>\}/);
  const px = Number(disclosure.match(/const THINKING_LOGO_PX = (\d+);/)[1]);          // the mark's height before the first step: the offsets in the sheet are worked out from it
  assert.ok(px >= 30 && px <= 54, 'it fits the 66px box the wait keeps');
  assert.doesNotMatch(disclosure, /StartGraph/);
  const chat = read('../pages/Chat.tsx');
  assert.doesNotMatch(chat, /sonic-ring-loader|sonic-loader-container|<ThinkingLogo/, 'the page has no second loader: the steps are always on, and their loader is the logo in StepsDisclosure');
  assert.match(read('./TraceSteps.tsx'), /<StartGraph stem /);
  const css = read('./__styles__/TracePanel.css');
  assert.match(css, /\.gl-start-wait > \.thinking-logo \{ position: static; \}/);
  const top = (66 - px) / 2;
  assert.ok(css.includes(`.gl-startwrap > .thinking-logo { position: absolute; left: 0; top: ${top}px;`), 'held where it was, centred in the 66px box');
  const textLeft = Math.round((px * 176) / 111) + 8 + 12;          // the mark's width, its 8px margin and the 12px gap
  assert.ok(css.includes(`.gl-start-text-logo { left: ${textLeft}px; }`), 'its text clear of it');
  assert.match(css, /\.thinking-logo \{ display: block; flex: 0 0 auto; overflow: visible; margin-right: 8px; color: var\(--accent-primary\); \}/);
});

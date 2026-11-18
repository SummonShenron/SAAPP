// The graph loader's rules, with no React in them so they can be tested (graphLoader.test.mjs). components/LoaderGraphs.tsx draws them.
//
// WHAT IT IS (2026-11-13, designed with Jack in loader-preview.html; the horizontal fork was replaced by this on 2026-11-14; shapes and pace added 2026-11-17). While a reply is being worked on, the conversation
// draws the steps as nodes on a vertical trunk. The loader is not a spinner but a graph, and it is ONE graph in two places:
//   - Before any step has arrived, a VERTICAL GRAPH runs down the left beside "Thinking...": a trunk with side routes in some stretches. The light takes a detour or goes straight down, and a different way next time round.
//   - When the first step lands, the light carries on down the way it was going to the last node, which becomes that step's real node; the graph keeps its shape and fades as a whole, and the space closes.
//   - Under the newest step the same kind of graph hangs in the gap between that step and the next one, its first node on the dashed stub that runs down from the step and its last node exactly where the next step's dot
//     will be. When the next step lands it does the same as the first: the light carries on to the last node, the graph fades, the space closes, and the new step's own graph begins.
// WHICH GRAPH: there are several shapes (GRAPH_SHAPES). The graph under a running step is that step's KIND of action (stepKinds.ts): each kind has its own shape (KIND_SHAPE), the same every time, so a run of web
// searches always looks like web searches. The graph before the first step, and under a step of the general kind, takes one of the other shapes at random, never the one drawn last. Either way every stretch
// starts on a random way (pickGraph). Every shape keeps the first node and
// the last node where the rest of the page expects them (FIRST_NODE_Y, LAST_NODE_Y), so the landing and the room it needs are the same for all of them.
// HOW FAST: a step can arrive sooner than a landing takes. The landing's whole timeline is divided by a PACE worked out from how soon this step followed the one before (paceFor), so the light still plays out, and a landing
// that is still running when the next step arrives is left to finish, not cut. Nothing here is a claim about what Sonic is doing: the graph only shows that work is going on and that a step has landed.

import type { StepKind } from './stepKinds';

/** A curved branch going down from (x1,y1) to (x2,y2), the way a flow chart draws a decision: it leaves and arrives vertically. */
export function downPath(x1: number, y1: number, x2: number, y2: number): string {
  const mid = (y1 + y2) / 2;
  return `M ${x1} ${y1} C ${x1} ${mid}, ${x2} ${mid}, ${x2} ${y2}`;
}

// ---- the shapes ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
export const START_BOX = { width: 44, height: 66 } as const;
/** Where every step dot is centred (the dot is 14px wide). */
export const TRUNK_X = 7;
/** The trunk's first and last node are the same in every shape: the first sits on the dashed stub, the last on the next step's dot. */
export const FIRST_NODE_Y = 6;
export const LAST_NODE_Y = 60;

type Side = readonly [number, number];
/** One way down a stretch: the side nodes it goes out through, in order (none: straight down the trunk). */
type WaySpec = readonly Side[];
interface Spec { id: string; trunkY: readonly number[]; stretches: readonly (readonly WaySpec[])[] }

export interface GraphNode { id: string; x: number; y: number; r: number }
export interface GraphEdge { id: string; d: string }
/** A shape, drawn: its nodes and edges, and per stretch the ways down it, each as the ids that light up in order (edge, node, edge, node ... and the next trunk node last). */
export interface Shape { id: string; nodes: GraphNode[]; edges: GraphEdge[]; routes: string[][][] }

/** Turns a spec into what is drawn. Trunk nodes are `t<n>`, side nodes `s<stretch>.<way>.<n>`, edges `e<stretch>.<way>.<n>`. Pure. */
export function buildShape(spec: Spec): Shape {
  const nodes: GraphNode[] = spec.trunkY.map((y, n) => ({ id: `t${n}`, x: TRUNK_X, y, r: n === spec.trunkY.length - 1 ? 3.6 : 3.2 }));
  const edges: GraphEdge[] = [];
  const routes: string[][][] = [];
  spec.stretches.forEach((ways, k) => {
    routes.push(ways.map((sides, w) => {
      const points: Side[] = [[TRUNK_X, spec.trunkY[k]], ...sides, [TRUNK_X, spec.trunkY[k + 1]]];
      const ids: string[] = [];
      sides.forEach(([x, y], n) => {
        nodes.push({ id: `s${k}.${w}.${n}`, x, y, r: 2.6 });
      });
      for (let n = 0; n < points.length - 1; n++) {
        const [x1, y1] = points[n];
        const [x2, y2] = points[n + 1];
        const id = `e${k}.${w}.${n}`;
        edges.push({ id, d: sides.length === 0 ? `M ${x1} ${y1} L ${x2} ${y2}` : downPath(x1, y1, x2, y2) });
        ids.push(id, n < sides.length ? `s${k}.${w}.${n}` : `t${k + 1}`);
      }
      return ids;
    }));
  });
  return { id: spec.id, nodes, edges, routes };
}

// The shapes. Every one keeps the first node at FIRST_NODE_Y and the last at LAST_NODE_Y; its side nodes lie to the right of the trunk and between the two trunk nodes of their stretch.
const SPECS: readonly Spec[] = [
  { id: 'classic', trunkY: [6, 24, 42, 60], stretches: [[[[24, 15]], []], [[[24, 33]], []], [[[24, 51]], []]] },
  { id: 'wide', trunkY: [6, 33, 60], stretches: [[[[30, 19.5]], []], [[[30, 46.5]], []]] },
  { id: 'four', trunkY: [6, 19.5, 33, 46.5, 60], stretches: [[[[19, 12.75]], []], [[[28, 26.25]], []], [[[19, 39.75]], []], [[[28, 53.25]], []]] },
  { id: 'long', trunkY: [6, 33, 60], stretches: [[[], [[22, 14], [34, 24]]], [[], [[30, 46.5]], [[20, 40], [32, 51]]]] },
  { id: 'uneven', trunkY: [6, 15, 48, 60], stretches: [[[]], [[], [[26, 26]], [[18, 23], [32, 34], [20, 43]]], [[[24, 54]], []]] },
  { id: 'zigzag', trunkY: [6, 24, 42, 60], stretches: [[[[34, 15]], []], [[[16, 33]], []], [[[34, 51]], []]] },
  { id: 'ladder', trunkY: [6, 33, 60], stretches: [[[[16, 14], [28, 20], [16, 27]], []], [[[32, 46.5]], []]] },
  { id: 'fan', trunkY: [6, 33, 60], stretches: [[[], [[30, 19.5]], [[18, 14], [30, 25]]], [[[28, 40]], [], [[20, 45], [32, 53]]]] },
  // One for each kind of action (stepKinds.ts, KIND_SHAPE below): how that action steps out of the trunk and fans back in. A style for the kind, not a measurement: the number of branches says nothing about how many
  // sources or results there were.
  { id: 'comb', trunkY: [6, 60], stretches: [[[[26, 19]], [[26, 33]], [[26, 47]]]] },                                                                       // a search of the web: three branches out and back
  { id: 'stack', trunkY: [6, 19.5, 33, 46.5, 60], stretches: [[[[24, 12.75]], []], [[[24, 26.25]], []], [[[24, 39.75]], []], [[[24, 53.25]], []]] },        // a database: a column of layers beside the trunk
  { id: 'bump', trunkY: [6, 60], stretches: [[[[32, 33]], []]] },                                                                                           // conversation: one soft bump out and back
  { id: 'rail', trunkY: [6, 60], stretches: [[[], [[24, 20], [24, 33], [24, 46]]]] },                                                                       // reading: a rail of lines running alongside
  { id: 'scan', trunkY: [6, 33, 60], stretches: [[[[22, 14]], [[34, 20]], [[22, 26]]], [[[34, 41]], [[22, 47]], [[34, 53]]]] },                              // looking for something: probes at different depths
  { id: 'vee', trunkY: [6, 60], stretches: [[[], [[30, 20], [14, 34]]]] },                                                                                  // mail: out, then back in at a slant
  { id: 'grid', trunkY: [6, 24, 42, 60], stretches: [[[], [[20, 15]], [[32, 15]]], [[], [[20, 33]], [[32, 33]]], [[], [[20, 51]], [[32, 51]]]] },           // a calendar: a grid of days
  { id: 'route', trunkY: [6, 60], stretches: [[[], [[34, 18], [26, 33], [34, 48]]]] },                                                                      // places: a winding road out and back
  { id: 'burst', trunkY: [6, 60], stretches: [[[[16, 20]], [[34, 33]], [[16, 46]]]] },
  { id: 'needle', trunkY: [6, 60], stretches: [[[], [[14, 16], [14, 46]]]] },                                                                                // injecting: a thin needle running close beside the trunk                                                                      // thinking: rays in and out
];
export const GRAPH_SHAPES: readonly Shape[] = SPECS.map(buildShape);

/** The shape each kind of action has (the general kind has none: it takes one of the shapes below at random). The same shape every time for a kind, so a run of web searches always looks like web searches. */
export const KIND_SHAPE: Readonly<Partial<Record<StepKind, string>>> = {
  code: 'ladder', web: 'comb', database: 'stack', chat: 'bump', read: 'rail', search: 'scan', mail: 'vee', calendar: 'grid', map: 'route', think: 'burst', browser: 'classic', inject: 'needle', add: 'wide',          // adding: two arms out, like a plus
};
/** The shapes that belong to no kind: what a step with the general kind (and the graph before the first step) draws, at random. */
export const GENERIC_SHAPES: readonly Shape[] = GRAPH_SHAPES.filter(shape => !Object.values(KIND_SHAPE).includes(shape.id));

// ---- which graph, and which way it starts ----------------------------------------------------------------------------------------------------------------------------------------------------------------
/** A shape and, per stretch, the way it starts on (an index into that stretch's ways). The second pass takes the next way of each stretch, so a stretch with two ways shows both. */
export interface Pick { shape: Shape; starts: number[] }
export const PASSES = 2;

/**
 * The graph for a step of `kind` and a random way to start each stretch. A kind that has a shape (KIND_SHAPE) always gets that shape; the general kind, or none, gets one of GENERIC_SHAPES at random (not `avoid`,
 * the one drawn last, when there is another). `rand` returns a number in [0, 1): injected so it can be tested. Pure.
 */
export function pickGraph(rand: () => number, avoid: string | null = null, kind?: StepKind): Pick {
  const index = (n: number) => Math.min(n - 1, Math.max(0, Math.floor(rand() * n)));
  const own = kind ? GRAPH_SHAPES.find(s => s.id === KIND_SHAPE[kind]) : undefined;
  const candidates = GENERIC_SHAPES.filter(s => s.id !== avoid);
  const pool = candidates.length > 0 ? candidates : GENERIC_SHAPES;
  const shape = own ?? pool[index(pool.length)];
  return { shape, starts: shape.routes.map(ways => index(ways.length)) };
}
let lastShapeId: string | null = null;
/** The pick a new loader makes: its kind's own shape, or for the general kind a random one that is never the one the previous such loader had. The only place that uses chance and remembers anything (decoration; nothing depends on it). */
export function randomPick(kind?: StepKind): Pick {
  const pick = pickGraph(Math.random, lastShapeId, kind);
  if (!(kind && KIND_SHAPE[kind])) lastShapeId = pick.shape.id;
  return pick;
}

/** A stream of numbers in [0, 1) that is the same every time for the same seed (a hash of the string feeding a small generator). Pure. */
export function seededRand(seed: string): () => number {
  let h = 1779033703 ^ seed.length;
  for (let i = 0; i < seed.length; i++) {
    h = Math.imul(h ^ seed.charCodeAt(i), 3432918353);
    h = (h << 13) | (h >>> 19);
  }
  let a = (Math.imul(h ^ (h >>> 16), 2246822507) ^ Math.imul(h ^ (h >>> 13), 3266489909)) >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
/**
 * The graph for one step, worked out from the step's own id: the same step always gets the same shape and the same starting ways, so the graph a step had while the turn ran is the one it has in the list kept under
 * the reply, however often that is opened. Its kind's own shape, or for the general kind one of GENERIC_SHAPES. Pure.
 */
export function pickFor(seed: string, kind?: StepKind): Pick {
  return pickGraph(seededRand(seed), null, kind);
}

/** Which way of a stretch a pass takes. Pure. */
export function wayIndex(pick: Pick, seg: number, pass: number): number {
  return (pick.starts[seg] + pass) % pick.shape.routes[seg].length;
}

// ---- the beats ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
export interface StartFrame { pass: number; seg: number; lit: number; fade: boolean }
/** Every beat of the loop: each stretch in turn, one element lit per beat and a hold on the new trunk node, then everything fades before the other way round. */
export function buildStartFrames(pick: Pick): StartFrame[] {
  const out: StartFrame[] = [];
  const segs = pick.shape.routes.length;
  for (let pass = 0; pass < PASSES; pass++) {
    for (let seg = 0; seg < segs; seg++) {
      const length = pick.shape.routes[seg][wayIndex(pick, seg, pass)].length;
      for (let s = 1; s <= length; s++) out.push({ pass, seg, lit: s, fade: false });
      out.push({ pass, seg, lit: length, fade: false });                                          // hold on the new trunk node
    }
    out.push({ pass, seg: segs - 1, lit: pick.shape.routes[segs - 1][wayIndex(pick, segs - 1, pass)].length, fade: true });          // then everything fades
  }
  return out;
}
/** The beat the reduced-motion graph holds on: a few beats in (part of the way lit, nothing faded), whatever the shape. */
export function staticFrameIndex(frames: readonly StartFrame[]): number {
  return Math.min(4, frames.length - 1);
}

/** Everything lit at a beat: the first node, the whole way already travelled, and the part of the current stretch reached. Pure. */
export function startLit(pick: Pick, frame: StartFrame): Set<string> {
  const on = new Set<string>(['t0']);
  for (let seg = 0; seg < frame.seg; seg++) pick.shape.routes[seg][wayIndex(pick, seg, frame.pass)].forEach(id => on.add(id));
  pick.shape.routes[frame.seg][wayIndex(pick, frame.seg, frame.pass)].slice(0, frame.lit).forEach(id => on.add(id));
  return on;
}
/** The whole way a pass takes, first node to last, in lighting order. When a step lands the light carries on along this, detours included, so the graph never straightens. */
export function startWay(pick: Pick, pass: number): string[] {
  const way = ['t0'];
  pick.shape.routes.forEach((ways, seg) => way.push(...ways[wayIndex(pick, seg, pass)]));
  return way;
}

// ---- where the graph sits ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------
// A step's row has its dot centred 10px below the row's top (the 20px dot sits at the top, half of 20px). The graph's last node is at y = 60 and must be exactly on the dot of the step that lands.
/** Before the first step: the room above the list (which has a 4px top margin) that puts the last node on the first step's dot. */
export const START_SPACE_PX = LAST_NODE_Y - 4 - 10;
/** Under a step: the room its row keeps below its text while it is the newest (the next row starts after it), so the box starts there and its last node meets the next row's dot. */
export const GAP_SPACE_PX = LAST_NODE_Y - 10;
/** The box hangs this far below its row's bottom edge: the last node is 10px below the edge (the next dot), and the box goes on for the rest of its height below that node. */
export const GAP_OVERHANG_PX = START_BOX.height - LAST_NODE_Y + 10;
/** The dashed stub from the step's dot stops at the graph's first node, this far above the row's bottom edge. */
export const GAP_STUB_END_PX = GAP_SPACE_PX - FIRST_NODE_Y;

// ---- a graph that stays (the steps in graph mode) -------------------------------------------------------------------------------------------------------------------------------------------------------
// In graph mode the graph under a step is not removed once it has landed: it stays between that step and the next, the way the light took lit and the other routes dim, so the steps of a reply make one graph. The newest
// one keeps its full size; one with two or more steps after it shrinks to COMPACT_SCALE around its first node, so a long turn does not grow without end. Shrinking keeps the shape (both axes by the same factor) and
// keeps the last node exactly on the next step's dot: the room under the row is what the scaled graph needs. P(s) = FIRST_NODE_Y + (LAST_NODE_Y - FIRST_NODE_Y) * s - 10, which is GAP_SPACE_PX at s = 1.
export const COMPACT_SCALE = 5 / 9;
/** A kept graph is full size while it is one of the newest two (one step after it), compact from the second step after it on. */
export const COMPACT_AFTER = 2;
/** The room under a row (in px) that a graph at `scale` needs so its last node meets the next row's dot. Pure. */
export function gapSpaceFor(scale: number): number {
  return Math.round(FIRST_NODE_Y + (LAST_NODE_Y - FIRST_NODE_Y) * scale - 10);
}
export const COMPACT_SPACE_PX = gapSpaceFor(COMPACT_SCALE);                     // 26
/** The compact box hangs this far below its row's bottom edge, and its dashed stub stops this far above it (the same relations as the full size: GAP_OVERHANG_PX, GAP_STUB_END_PX). */
export const COMPACT_OVERHANG_PX = START_BOX.height - COMPACT_SPACE_PX;         // 40
export const COMPACT_STUB_END_PX = COMPACT_SPACE_PX - FIRST_NODE_Y;             // 20
/** The scale a kept graph is drawn at, from how many steps came after the step it hangs under. Pure. */
export function keptScale(stepsAfter: number): number {
  return stepsAfter >= COMPACT_AFTER ? COMPACT_SCALE : 1;
}
/** How far the step text is pushed right in graph mode: the graph's own width and the gap the "Thinking..." text already keeps, so the text never meets a shape. */
export const GRAPH_TEXT_INDENT_PX = START_BOX.width + 12;

// ---- which arrival is this ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------
export interface Arrivals {
  /** The step whose graph lands in the first new one (the newest before this render), or null when there is none to land. */
  holderId: string | null;
  /** The steps that are new, in order. More than one when steps came in a burst. */
  newIds: string[];
}
/**
 * What has arrived since the last render. `previousLastId` is the id of the newest step before it; `steps` is the list now (only the last few are drawn while a turn runs, and a person can open the older ones, so this
 * goes by where the previous newest is, never by a count). The graph under the previous newest lands in the first new step, however many came. Nothing is new when the newest is the same; when the previous newest is no longer
 * drawn every step counts as new and there is no graph to land. Pure.
 */
export function arrivalsOf(previousLastId: string | null, steps: ReadonlyArray<{ id: string }>): Arrivals {
  if (steps.length === 0) return { holderId: null, newIds: [] };
  const at = previousLastId === null ? -1 : steps.findIndex(s => s.id === previousLastId);
  const newIds = steps.slice(at + 1).map(s => s.id);
  return { holderId: at >= 0 && newIds.length > 0 ? previousLastId : null, newIds };
}
/** The first step has landed: the list was empty and now is not. Pure. */
export function firstStepLanded(previousCount: number, count: number): boolean {
  return previousCount === 0 && count >= 1;
}

// ---- timings, in milliseconds from the moment a step arrives (at pace 1) --------------------------------------------------------------------------------------------------------------------------------
export const TICK_MS = 260;
export const LAND_LEAVE_AT = 1100;        // the graph starts to fade (the step's node has popped in)
export const LAND_END_AT = 1500;          // the graph is removed, the space closes, the step's own graph begins
/** The new step's node and title fade in this long after it lands (about when the light reaches its node). */
export const ARRIVE_DELAY_MS = 720;
/** How long that fade-in takes, how long a lit node or edge takes to come up, and how long the graph takes to fade out. */
export const ARRIVE_ANIM_MS = 350;
export const LIGHT_MS = 250;
export const LEAVE_FADE_MS = 300;
/** Steps that came in a burst fade in this far apart. */
export const ARRIVE_STAGGER_MS = 120;
/** The light moves on this many milliseconds per element at most; a long way goes faster so the light has reached the last node by the time the step's node pops in (ARRIVE_DELAY_MS). */
export const LANDING_STAGGER_MS = 55;

/** The clock the pace is worked out from: milliseconds on a steady timer. Read only when a step arrives (inside the render that first has it), never on an ordinary render; kept here so that is the one place it is read. */
export function nowMs(): number {
  return performance.now();
}
/** The fastest a landing may play. Above this the light is too quick to see. */
export const MAX_PACE = 3;
/**
 * How fast a landing should play, from how long ago the step before this one arrived (null: nothing before it counts). A landing takes LAND_END_AT, so a step that followed the last one sooner than that lands faster by
 * the same ratio, up to MAX_PACE, and the light still plays out before the next step is due. 1 is the normal speed; never slower. Pure.
 */
export function paceFor(gapMs: number | null): number {
  if (gapMs === null || !Number.isFinite(gapMs) || gapMs >= LAND_END_AT) return 1;
  return Math.min(MAX_PACE, Math.max(1, LAND_END_AT / Math.max(gapMs, 1)));
}
/** A duration at a pace, in whole milliseconds. Pure. */
export function scaled(ms: number, pace: number): number {
  return Math.round(ms / Math.max(pace, 1));
}
/** The gap between one element lighting and the next while a landing carries the light down a way of `wayLength` elements, at a pace. Pure. */
export function landingStagger(wayLength: number, pace: number): number {
  const fit = wayLength > 1 ? ARRIVE_DELAY_MS / (wayLength - 1) : LANDING_STAGGER_MS;
  return Math.min(LANDING_STAGGER_MS, fit) / Math.max(pace, 1);
}
/** `record` without `key` (a new object; the same one when there was nothing to remove). Pure. */
export function without<T>(record: Readonly<Record<string, T>>, key: string): Record<string, T> {
  if (!(key in record)) return record as Record<string, T>;
  const { [key]: _gone, ...rest } = record;
  void _gone;
  return rest;
}

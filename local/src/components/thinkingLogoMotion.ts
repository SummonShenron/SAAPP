// The Sonic mark, animated as the "thinking" loader (2026-11-18). One cycle of TEN EQUAL STAGES (STAGE_MS each), then it starts again:
//   1. the logo holds still (no bounce) and the runner goes over its crest, pulsing each dot it reaches;
//   2. the six dots MORPH into a double helix (two strands that twist round each other, three dots on each, the one in the approved preview HelixLogo.tsx) and the runner goes down one strand, across the
//      gap at the bottom and up the other (the two strands are separate, as in the preview: they are never joined at either end);
//   3. the helix morphs into a circle, round which the dots go (two turns);
//   4. the circle unrolls into a straight line, which is held;
//   5. the line bends into a PIPE (a plumbing pipe: up a leg, over an arch, down the middle, round a U-bend and up the other leg), and the runner goes along it;
//   6. the pipe becomes an infinity loop (a lying figure eight), and the dots circulate round it once;
//   7. the dots become a STAR graph (the big dot is the hub, with a spoke to each of the other five); the hub pulses as the stage begins and a pulse shoots out from it along all five spokes at once, reaching the five points together as the star settles;
//   8. then a TREE (the big dot is the root, with two children and three leaves);
//   9. then a LADDER (two rows of three, joined along the rows and by rungs);
//  10. the dots fly back into the logo.
// THE DASHED LINE RUNS THROUGH THE WHOLE CYCLE: in every stage a RUNNER, a bright dot, goes from dot to dot with a flowing dashed line behind it that joins the last two dots it has touched, along whatever the
// dots are on at that moment (the logo's own lines, the circle, the straight line, the pipe, the infinity loop, the links of a star, a tree or a ladder). A dot PULSES (stretches, squashes, settles, and sends out a ring) only
// when the runner reaches it: there is no other pulse. In the logo and in the three graph shapes the dots are joined by LINKS (the logo's own seven lines, a star's five spokes, a tree's five branches, a ladder's
// seven bars); between two shapes the old links fade away and the new ones fade in. This file is the pure maths: what the whole mark, each dot, the links, the runner and the trail are doing at a given moment.
// components/ThinkingLogo.tsx draws it. Everything starts and ends at rest, so the cycles join on.
import { LOGO_EDGES, LOGO_NODES } from './logoMarkData.ts';

/** The cycle is ten stages, each the SAME length: the logo with its runner, the double helix, the circle (the helix morphs into it and the dots go round), the line, the pipe, the infinity loop, the star, the tree, the ladder, and the return to the logo. Each stage's own coming-in is part of its time. */
export const STAGE_MS = 1600;
export const STAGE_COUNT = 10;
export const LOOP_MS = STAGE_MS * STAGE_COUNT;

// stage 1 used to be a bounce (0 to STAGE_MS), in ms. It no longer bounces: heightScale and liftAt hold the logo still, so these numbers only keep the shape of those functions
const BOUNCE = (ms: number) => Math.round((ms * STAGE_MS) / 2300);          // the bounce was timed for 2300 ms: these are the same moments, squeezed into one stage
export const CROUCH_END = BOUNCE(450);      // down into a crouch
export const LAUNCH_END = BOUNCE(700);      // springs off the ground, stretched tall
export const AIR_END = BOUNCE(1500);        // in the air
export const IMPACT_END = BOUNCE(1650);     // the squash on landing
export const SETTLE_END = STAGE_MS;         // wobbles back to its own shape, just as the stage ends

export const POP_MS = 650;          // each dot's pop after the runner reaches it
export const RING_MS = 650;         // and its ring, spreading and fading
export const TRAIL_FADE_MS = 300;   // a piece of trail fades out this long once the runner has touched the dot after its far end
export const TRAIL_HOLD_MS = 350;   // and the last piece stays this long after the runner has got to the last dot
export const TRAIL_FADE_IN_MS = 80; // and a piece fades in over this long as the runner sets off along it
/** How long the runner takes from the first dot of its run to the last: what is left of the stage once the last piece of trail has held and faded (and the last pop is over), so each run is over just as its stage ends. */
export const RUN_MS = STAGE_MS - TRAIL_HOLD_MS - TRAIL_FADE_MS;

// the stages with the dots apart
export const HELIX_END = STAGE_MS + 650;               // stage 2: the dots morph from the logo into the double helix in this long, and the helix is then held until the stage ends
export const MORPH_START = 2 * STAGE_MS;               // the helix morphs...
export const BREAK_END = MORPH_START + 550;            // ...into a circle (stage 3)
export const SPIN_END = 3 * STAGE_MS;                  // ...round which the dots go until the stage ends
export const UNROLL_END = SPIN_END + 650;              // stage 4: the circle unrolls into a straight line...
export const HOLD_END = 4 * STAGE_MS;                  // ...which is held until the stage ends
export const GRAPH_MOVE_MS = 650;                      // stages 5 and 7 to 9: the dots move to the next shape in this long, and the shape is then held until the stage ends
export const PIPE_END = HOLD_END + GRAPH_MOVE_MS;      // stage 5: the line bends into a pipe...
export const PIPE_HOLD_END = 5 * STAGE_MS;             // ...which is held until the stage ends
export const INFINITY_END = PIPE_HOLD_END + 650;       // stage 6: the pipe becomes an infinity loop...
export const FLOW_END = 6 * STAGE_MS;                  // ...round which the dots circulate once until the stage ends
export const STAR_END = FLOW_END + GRAPH_MOVE_MS;      // stage 7: the star (the loop's line fades, the star's spokes come in)...
export const TREE_START = 7 * STAGE_MS;                // stage 8: ...which becomes a tree...
export const TREE_END = TREE_START + GRAPH_MOVE_MS;
export const LADDER_START = 8 * STAGE_MS;              // stage 9: ...which becomes a ladder...
export const LADDER_END = LADDER_START + GRAPH_MOVE_MS;
export const RETURN_START = 9 * STAGE_MS;              // stage 10: ...whose dots fly back into the logo
export const RETURN_END = RETURN_START + 750;
export const SPIN_TURNS = 2;                           // how many times the dots go round the circle

export const CROUCH_SQUASH = 0.6;   // how short it gets in the crouch (the width grows by the same trade, see widthFor)
export const LAUNCH_STRETCH = 1.5;
export const LAND_SQUASH = 0.55;

// the mark's own drawing space (logoMarkData.ts LOGO_VIEWBOX): squashing is about the ground under it
export const MID_X = 88;
export const MID_Y = 55;
const GROUND_Y = 100;
const JUMP = 22;                    // how high it goes, in drawing units

// the shapes the dots take, all centred on the middle of the mark
export const CIRCLE_RADIUS = 38;
// the double helix (stage 2): the drawing of HelixLogo.tsx. TWO separate strands, never joined (each stops a little short of the top and the bottom, so the two ends are apart), three dots on each
export const HELIX_AMP = 16;        // how far each strand swings either side of the middle
export const HELIX_TURNS = 1.5;     // how many twists from top to bottom
export const HELIX_TOP = 14;
export const HELIX_BOTTOM = 97;
export const HELIX_MARGIN = 0.08;   // each strand starts and stops this far (of the height) in from the top and the bottom
/** Where the three dots of a strand sit down it (as a share of the helix's height), the first and last at the strand's own ends: as in the preview. */
export const HELIX_DOT_FRACS: readonly number[] = [0, 0.5, 1].map(k => HELIX_MARGIN + k * (1 - 2 * HELIX_MARGIN));
export const LINE_FROM_X = 14;      // the line runs from the logo's first dot's x...
export const LINE_TO_X = 162;       // ...to its last
export const INFINITY_WIDTH = 66;   // half the width of the infinity loop
export const INFINITY_HEIGHT = 27;  // and how far each lobe goes up and down
export const CURVE_SAMPLES = 48;    // the line through the dots is drawn as this many short pieces
// the pipe (a plumbing pipe, as an open line): a leg up from the foot, an arch over the top, a leg down the middle, a U-bend underneath, and a leg up to the tip. Both bends are half circles of the same radius.
export const PIPE_RADIUS = 19;
export const PIPE_LEFT_X = 50;      // the first leg
export const PIPE_MID_X = 88;       // the middle leg, down the middle of the mark (the arch and the U-bend each span half the pipe's width)
export const PIPE_RIGHT_X = 126;    // the last leg
export const PIPE_ARCH_Y = 30;      // where the arch's half circle starts to curve (its top is PIPE_RADIUS higher)
export const PIPE_BASE_Y = 76;      // where the U-bend's half circle starts to curve (its bottom is PIPE_RADIUS lower)
export const PIPE_FOOT_Y = 96;      // the pipe starts here, low on the left...
export const PIPE_TIP_Y = 16;       // ...and ends here, high on the right
/** Slot s of every shape holds dot SLOT_ORDER[s]: the dots in the order they sit round the logo (crest, the two on the right, trough, then the two on the left), so they fly out to the circle without crossing. */
export const SLOT_ORDER: readonly number[] = [2, 4, 5, 3, 0, 1];

/** The runner's ways through the dots, as dot numbers. Through the logo they follow the mark's own edges: over the crest (dot 2), and home again, right to left. On the helix, the circle, the line and the loop it goes along the shape, slot by slot. */
export const CREST_ROUTE: readonly number[] = [0, 1, 2, 4, 5];
export const HOME_ROUTE: readonly number[] = [5, 4, 2, 1, 0];
/** The runner's way through each graph shape, along its links and never to a dot it has been to: down one side of the tree and up the other, and along the ladder's top, down, and back along its bottom. The star has no way through: its run is a BURST (the hub, then all five points at once). */
export const STAR_BURST: readonly number[] = [2, 4, 5, 3, 0, 1];        // the hub first, then the five points as they sit round it, clockwise from the top: a burst, not a way through
export const TREE_ROUTE: readonly number[] = [0, 1, 2, 4, 5];
export const LADDER_ROUTE: readonly number[] = [0, 2, 4, 5, 3, 1];
/** One run for each stage, starting as the stage does. A run, its trail and its last pop are all over by the time its stage ends, so only one is ever going. */
export interface Run {
  start: number;
  dots: readonly number[];
  /** A burst: `dots[0]` is a hub and the rest are reached together, along a line of their own each, instead of one after another. The hub pulses at `start`, the points when the lines reach them (RUN_MS later). */
  burst?: boolean;
}
export const RUNS: ReadonlyArray<Run> = [CREST_ROUTE, SLOT_ORDER, SLOT_ORDER, SLOT_ORDER, SLOT_ORDER, SLOT_ORDER, STAR_BURST, TREE_ROUTE, LADDER_ROUTE, HOME_ROUTE].map((dots, stage): Run => (dots === STAR_BURST ? { start: stage * STAGE_MS, dots, burst: true } : { start: stage * STAGE_MS, dots }));
/** How many pieces of trail there can be: one for each step of the longest run. */
export const TRAIL_SLOTS = Math.max(...RUNS.map(run => run.dots.length)) - 1;
/** When the runner reaches each dot of a run: evenly, from the start of the stage to RUN_MS after it. */
export const runTimes = (run: Run): number[] => (run.burst ? run.dots.map((_, i) => run.start + (i === 0 ? 0 : RUN_MS)) : run.dots.map((_, i) => run.start + (RUN_MS * i) / (run.dots.length - 1)));

export type Point = readonly [number, number];
/** A link between two dots, by dot number. */
export type Link = readonly [number, number];
/** The most links any shape has (the logo's seven, and the ladder's), so a pose always has this many slots for them. */
export const LINK_SLOTS = 7;
export interface DotPose { x: number; y: number; scaleX: number; scaleY: number; ringScale: number; ringOpacity: number }
export interface RunnerPose { x: number; y: number; opacity: number }
/** One piece of the dashed trail: a line from one dot of the run towards the next, along whatever the dots are on, as far as the runner has got (or all the way), and how visible it is. */
export interface TrailPose { points: ReadonlyArray<Point>; opacity: number }
/** The line through the dots once the logo has broken apart: a circle, a straight line, an infinity loop, or the shape between two of them. */
export interface CurvePose { points: ReadonlyArray<Point>; opacity: number; /** The helix's second strand: a line of its own, there only while the dots are on the helix or leaving it. */ second?: { points: ReadonlyArray<Point>; opacity: number } }
/** How the whole mark is squashed, stretched and lifted: the numbers behind `transform` (the loader's hand-over to the first step's icon, thinkingLogoBecome.ts, blends them back to rest). */
export interface Body { lift: number; scaleX: number; scaleY: number }
export interface LogoPose {
  /** The SVG transform of the whole mark inside its drawing: squash, stretch and jump. */
  transform: string;
  body: Body;
  dots: DotPose[];
  /** The links that join the dots now (the logo's lines, or a star's, a tree's or a ladder's): LINK_SLOTS slots, null where a shape has fewer. They run between the dots, wherever the dots are. */
  links: ReadonlyArray<Link | null>;
  /** How visible the links are: they fade out as the dots move on to the next shape, and the next shape's fade in. */
  edgeOpacity: number;
  curve: CurvePose;
  runner: RunnerPose;
  trail: TrailPose[];
}

export const easeInOut = (k: number) => (k < 0.5 ? 2 * k * k : 1 - 2 * (1 - k) * (1 - k));
const easeOut = (k: number) => 1 - (1 - k) * (1 - k);
const easeIn = (k: number) => k * k;
/** A fly-out that overshoots its place and settles back. */
export const easeOutBack = (k: number) => 1 + 2.70158 * (k - 1) ** 3 + 1.70158 * (k - 1) ** 2;
export const clamp01 = (v: number) => Math.min(1, Math.max(0, v));
export const lerp = (a: number, b: number, k: number) => a + (b - a) * k;
const lerpPoint = (a: Point, b: Point, k: number): Point => [lerp(a[0], b[0], k), lerp(a[1], b[1], k)];
/** Squashing a body makes it wider and stretching makes it thinner, so it keeps about the same bulk (a little less than all of it, which reads as lively). */
export const widthFor = (scaleY: number) => scaleY ** -0.6;
const round = (v: number) => Math.round(v * 1000) / 1000 + 0;
const roundPoint = (p: Point): Point => [round(p[0]), round(p[1])];

/** How tall the whole mark is at `t` ms into a cycle (1 at rest). */
export function heightScale(t: number): number {
  if (t < SETTLE_END) return 1; // stage 1 holds still: no bounce, the comet pulse plays on the logo
  if (t < CROUCH_END) return 1 - (1 - CROUCH_SQUASH) * easeInOut(t / CROUCH_END);
  if (t < LAUNCH_END) return CROUCH_SQUASH + (LAUNCH_STRETCH - CROUCH_SQUASH) * easeOut((t - CROUCH_END) / (LAUNCH_END - CROUCH_END));
  if (t < AIR_END) return LAUNCH_STRETCH + (1.2 - LAUNCH_STRETCH) * easeOut((t - LAUNCH_END) / (AIR_END - LAUNCH_END));
  if (t < IMPACT_END) return 1.2 + (LAND_SQUASH - 1.2) * easeIn((t - AIR_END) / (IMPACT_END - AIR_END));
  if (t < SETTLE_END) {
    const k = (t - IMPACT_END) / (SETTLE_END - IMPACT_END);
    return 1 - (1 - LAND_SQUASH) * Math.exp(-5 * k) * Math.cos(k * 2 * Math.PI * 1.25);          // a damped wobble: starts at the squash, crosses its own shape, and has all but died away at the end
  }
  return 1;
}

/** How far it is off the ground at `t` (drawing units, up is negative). */
function liftAt(t: number): number {
  if (t < SETTLE_END || t >= AIR_END) return 0; // no jump during stage 1
  const k = (t - LAUNCH_END) / (AIR_END - LAUNCH_END);
  return -JUMP * 4 * k * (1 - k);
}

// ---- the shapes the logo breaks apart into ---------------------------------------------------------------------------------------------------------------------------------------------

type Shape = 'circle' | 'line' | 'infinity' | 'pipe';
/** A shape, and how far round it the dots have gone (turns for the circle, laps for the infinity loop; ignored for the line and the pipe, which are open and have no round to go). */
interface ShapeSpec { shape: Shape; shift: number }
const CIRCLE: ShapeSpec = { shape: 'circle', shift: 0 };
const LINE: ShapeSpec = { shape: 'line', shift: 0 };
/** Where on the infinity loop the dots start when the pipe becomes it, as a fraction of a lap. Any other start has dots from one end of the pipe meet dots from the other on the way (two of them land on the same spot for a moment): this one keeps every pair at least 8 units apart, going in and coming out. */
export const LOOP_PHASE = 0.79;
const LOOP: ShapeSpec = { shape: 'infinity', shift: LOOP_PHASE };
const PIPE: ShapeSpec = { shape: 'pipe', shift: 0 };

/** Where along a shape (0 to 1) slot `slot` of the six sits when the dots are evenly spaced on it (the infinity loop's dots are kept off its crossing in the middle, and the pipe's off its two ends so the line reaches past the first and last). */
const slotParam = (shape: Shape, slot: number) => (shape === 'circle' ? slot / 6 : shape === 'line' ? slot / 5 : (slot + 0.5) / 6);

// the pipe's five pieces, by length, so the line through the dots is drawn at an even pace along it
const PIPE_LEG_LEFT = PIPE_FOOT_Y - PIPE_ARCH_Y;
const PIPE_BEND = Math.PI * PIPE_RADIUS;
const PIPE_LEG_MID = PIPE_BASE_Y - PIPE_ARCH_Y;
const PIPE_LEG_RIGHT = PIPE_BASE_Y - PIPE_TIP_Y;
export const PIPE_LENGTH = PIPE_LEG_LEFT + PIPE_BEND + PIPE_LEG_MID + PIPE_BEND + PIPE_LEG_RIGHT;

/** A point of the pipe, `u` (0 to 1) of the way along it from its foot: up the first leg, over the arch, down the middle, under the U-bend and up to the tip. */
function pipePoint(u: number): Point {
  let s = clamp01(u) * PIPE_LENGTH;
  if (s <= PIPE_LEG_LEFT) return [PIPE_LEFT_X, PIPE_FOOT_Y - s];
  s -= PIPE_LEG_LEFT;
  if (s <= PIPE_BEND) {
    const phi = s / PIPE_RADIUS;
    return [(PIPE_LEFT_X + PIPE_MID_X) / 2 - PIPE_RADIUS * Math.cos(phi), PIPE_ARCH_Y - PIPE_RADIUS * Math.sin(phi)];
  }
  s -= PIPE_BEND;
  if (s <= PIPE_LEG_MID) return [PIPE_MID_X, PIPE_ARCH_Y + s];
  s -= PIPE_LEG_MID;
  if (s <= PIPE_BEND) {
    const phi = s / PIPE_RADIUS;
    return [(PIPE_MID_X + PIPE_RIGHT_X) / 2 - PIPE_RADIUS * Math.cos(phi), PIPE_BASE_Y + PIPE_RADIUS * Math.sin(phi)];
  }
  s -= PIPE_BEND;
  return [PIPE_RIGHT_X, PIPE_BASE_Y - s];
}

/** A point of a shape: the circle from its top, clockwise; the line from left to right; the infinity loop (a lying figure eight) from its middle, up and to the right first; the pipe from its foot. */
function pointOn(spec: ShapeSpec, u: number): Point {
  if (spec.shape === 'circle') {
    const angle = 2 * Math.PI * (u + spec.shift) - Math.PI / 2;
    return [MID_X + CIRCLE_RADIUS * Math.cos(angle), MID_Y + CIRCLE_RADIUS * Math.sin(angle)];
  }
  if (spec.shape === 'line') return [lerp(LINE_FROM_X, LINE_TO_X, u), MID_Y];
  if (spec.shape === 'pipe') return pipePoint(u);
  const theta = 2 * Math.PI * (u + spec.shift);
  return [MID_X + INFINITY_WIDTH * Math.sin(theta), MID_Y + INFINITY_HEIGHT * Math.sin(2 * theta)];
}

interface Shaped { dots: Point[]; curve: Point[]; u: number[] }

/** The six dots (by dot number), the line through them, and where along that line each dot is, when the shape is `k` of the way from `a` to `b`. The dots keep to the line as it changes. */
function between(a: ShapeSpec, b: ShapeSpec, k: number): Shaped {
  const dots: Point[] = [];
  const u: number[] = [];
  SLOT_ORDER.forEach((dot, slot) => {
    u[dot] = lerp(slotParam(a.shape, slot), slotParam(b.shape, slot), k);
    dots[dot] = lerpPoint(pointOn(a, u[dot]), pointOn(b, u[dot]), k);
  });
  const curve = Array.from({ length: CURVE_SAMPLES + 1 }, (_, i) => lerpPoint(pointOn(a, i / CURVE_SAMPLES), pointOn(b, i / CURVE_SAMPLES), k));
  return { dots, curve, u };
}

const LOGO_POINTS: Point[] = LOGO_NODES.map(node => [node[0], node[1]] as const);
const RESTING_CURVE: Point[] = between(CIRCLE, CIRCLE, 0).curve;
const LOGO_LINKS: Link[] = LOGO_EDGES.map(([from, to]) => [from, to] as const);

// ---- the double helix: two separate strands -----------------------------------------------------------------------------------------------------------------------------------------------

/** A point of strand `strand` (0, or 1 which is half a turn out of step) at `f` of the helix's height, 0 at the top and 1 at the bottom. */
export function helixPoint(strand: number, f: number): Point {
  return [MID_X + HELIX_AMP * Math.sin(2 * Math.PI * HELIX_TURNS * f + strand * Math.PI), HELIX_TOP + f * (HELIX_BOTTOM - HELIX_TOP)];
}
/** `k` (0 to 1) of the way down a strand, which runs from HELIX_MARGIN to 1 - HELIX_MARGIN of the height. */
const helixStrand = (strand: number, k: number): Point => helixPoint(strand, HELIX_MARGIN + k * (1 - 2 * HELIX_MARGIN));
const helixCurve = (strand: number): Point[] => Array.from({ length: CURVE_SAMPLES + 1 }, (_, i) => helixStrand(strand, i / CURVE_SAMPLES));
/** Where the six dots are on the helix, each dot's place along its own strand (0 top, 1 bottom) and which strand it is on. Slot s of SLOT_ORDER: the first strand's three going down, then the second strand's three coming back up. */
const HELIX_HELD = (() => {
  const dots: Point[] = [];
  const u: number[] = [];
  const side: number[] = [];
  SLOT_ORDER.forEach((dot, slot) => {
    const strand = slot < 3 ? 0 : 1;
    const k = slot < 3 ? slot / 2 : (5 - slot) / 2;
    dots[dot] = helixStrand(strand, k);
    u[dot] = k;
    side[dot] = strand;
  });
  return { dots, u, side, curve: helixCurve(0), second: helixCurve(1) };
})();

// ---- the graph shapes: where the six dots go, and which are joined ---------------------------------------------------------------------------------------------------------------------

interface Scene { dots: Point[]; links: Link[] }
const place = (entries: Array<[number, number, number]>): Point[] => { const out: Point[] = []; entries.forEach(([dot, x, y]) => { out[dot] = [x, y]; }); return out; };
/** The star: the big dot (2) in the middle as the hub, the other five round it (in the order they sit round the logo, from the top, clockwise), a spoke to each. */
export const STAR_RADIUS = 40;
const STAR: Scene = {
  dots: place([[2, MID_X, MID_Y], ...[4, 5, 3, 0, 1].map((dot, k): [number, number, number] => [dot, MID_X + STAR_RADIUS * Math.cos(((-90 + 72 * k) * Math.PI) / 180), MID_Y + STAR_RADIUS * Math.sin(((-90 + 72 * k) * Math.PI) / 180)])]),
  links: [[2, 4], [2, 5], [2, 3], [2, 0], [2, 1]],
};
/** The tree: the big dot (2) at the top as the root, two children (1 and 4), and three leaves, two under the left child and one under the right. */
const TREE: Scene = {
  dots: place([[2, MID_X, 16], [1, 54, 50], [4, 122, 50], [0, 34, 90], [3, 74, 90], [5, 122, 90]]),
  links: [[2, 1], [2, 4], [1, 0], [1, 3], [4, 5]],
};
/** The ladder: two rows of three, joined along each row and by a rung at each column. */
const LADDER: Scene = {
  dots: place([[0, 30, 36], [2, MID_X, 36], [4, 146, 36], [1, 30, 74], [3, MID_X, 74], [5, 146, 74]]),
  links: [[0, 2], [2, 4], [1, 3], [3, 5], [0, 1], [2, 3], [4, 5]],
};
export const SCENES = { star: STAR, tree: TREE, ladder: LADDER } as const;
export const LOGO_SCENE: Scene = { dots: LOGO_POINTS, links: LOGO_LINKS };

/** Between two shapes the old links fade out (while the dots start to move), and, once they are gone, the new links fade in: so the links on show are always those of one shape. `k` is how far through the move. */
function crossLinks(from: Link[], to: Link[], k: number): { links: Link[]; edgeOpacity: number } {
  return k < 0.4 ? { links: from, edgeOpacity: 1 - clamp01(k / 0.35) } : { links: to, edgeOpacity: clamp01((k - 0.5) / 0.5) };
}

/** `u` is where each dot is along `curve` (0 to 1), or null when the dots are not on it: at home in the logo, or flying out to the circle and back. */
interface Layout {
  dots: Point[]; curve: Point[]; u: number[] | null; links: Link[]; curveOpacity: number; edgeOpacity: number;
  /** The helix's second strand and how visible it is, and which strand each dot is on (`u` is then along its own strand): only while the dots are on the helix or leaving it. */
  second?: Point[]; secondOpacity?: number; side?: number[];
}

/** Where the six dots are, and what the links and the line through them are doing, `t` ms into the cycle. The logo itself in stage 1, then the helix, the circle and the rest, and the logo again once the dots are back. */
function layoutAt(t: number): Layout {
  if (t < STAGE_MS || t >= RETURN_END) return { dots: LOGO_POINTS, curve: RESTING_CURVE, u: null, links: LOGO_LINKS, curveOpacity: 0, edgeOpacity: 1 };
  const onShape = (shape: Shaped): Layout => ({ dots: shape.dots, curve: shape.curve, u: shape.u, links: LOGO_LINKS, curveOpacity: 1, edgeOpacity: 0 });
  if (t < MORPH_START) {
    // stage 2: the dots fly from the logo onto the two strands of the helix (overshooting a little, like the other shapes), the logo's lines fade at once and the strands fade in as they get there; then the helix is held
    const helix = HELIX_HELD;
    const held: Layout = { dots: helix.dots, curve: helix.curve, second: helix.second, secondOpacity: 1, side: helix.side, u: helix.u, links: LOGO_LINKS, curveOpacity: 1, edgeOpacity: 0 };
    if (t >= HELIX_END) return held;
    const k = (t - STAGE_MS) / (HELIX_END - STAGE_MS);
    const e = easeOutBack(k);
    const fade = clamp01((k - 0.4) / 0.6);
    return { dots: helix.dots.map((point, dot) => lerpPoint(LOGO_POINTS[dot], point, e)), curve: helix.curve, second: helix.second, secondOpacity: fade, u: null, links: LOGO_LINKS, curveOpacity: fade, edgeOpacity: 1 - clamp01(k / 0.35) };
  }
  if (t < BREAK_END) {
    // stage 3: the helix morphs into the circle: the dots go to their places on it, the first strand bends round into the ring and the second fades away as they leave it, so nothing pops
    const k = easeInOut((t - MORPH_START) / (BREAK_END - MORPH_START));
    const circle = between(CIRCLE, CIRCLE, 0);
    return {
      dots: HELIX_HELD.dots.map((point, dot) => lerpPoint(point, circle.dots[dot], k)),
      curve: HELIX_HELD.curve.map((point, i) => lerpPoint(point, circle.curve[i], k)),
      second: HELIX_HELD.second, secondOpacity: 1 - k, u: null, links: LOGO_LINKS, curveOpacity: 1, edgeOpacity: 0,
    };
  }
  if (t < SPIN_END) {
    const spin = SPIN_TURNS * easeInOut((t - BREAK_END) / (SPIN_END - BREAK_END));
    return onShape(between({ shape: 'circle', shift: spin }, { shape: 'circle', shift: spin }, 0));
  }
  if (t < UNROLL_END) return onShape(between({ shape: 'circle', shift: SPIN_TURNS }, LINE, easeInOut((t - SPIN_END) / (UNROLL_END - SPIN_END))));
  if (t < HOLD_END) return onShape(between(LINE, LINE, 0));
  // the line bends into the pipe (the dots keep to the line as it changes), which is held until its stage ends
  if (t < PIPE_END) return onShape(between(LINE, PIPE, easeInOut((t - HOLD_END) / GRAPH_MOVE_MS)));
  if (t < PIPE_HOLD_END) return onShape(between(PIPE, PIPE, 0));
  if (t < INFINITY_END) return onShape(between(PIPE, LOOP, easeInOut((t - PIPE_HOLD_END) / (INFINITY_END - PIPE_HOLD_END))));
  if (t < FLOW_END) {
    const lap = easeInOut((t - INFINITY_END) / (FLOW_END - INFINITY_END));   // one whole lap, so at FLOW_END the dots are exactly where they started on the loop
    return onShape(between({ shape: 'infinity', shift: LOOP_PHASE + lap }, { shape: 'infinity', shift: LOOP_PHASE + lap }, 0));
  }
  // the graph shapes: the dots move to the next shape (with a little overshoot), the old links fade out and the new ones in, and the shape is held until its stage ends
  const move = (from: Point[], to: Scene, since: number): Point[] => from.map((point, dot) => lerpPoint(point, to.dots[dot], easeOutBack(clamp01(since / GRAPH_MOVE_MS))));
  const held = (scene: Scene): Layout => ({ dots: scene.dots, curve: RESTING_CURVE, u: null, links: scene.links, curveOpacity: 0, edgeOpacity: 1 });
  if (t < STAR_END) {
    // the loop's line fades as its dots move, then the star's spokes come in
    const k = (t - FLOW_END) / GRAPH_MOVE_MS;
    const loop = between(LOOP, LOOP, 0);
    return { dots: move(loop.dots, STAR, t - FLOW_END), curve: loop.curve, u: null, links: STAR.links, curveOpacity: 1 - clamp01(k / 0.45), edgeOpacity: clamp01((k - 0.5) / 0.5) };
  }
  if (t < TREE_START) return held(STAR);
  if (t < TREE_END) return { dots: move(STAR.dots, TREE, t - TREE_START), curve: RESTING_CURVE, u: null, curveOpacity: 0, ...crossLinks(STAR.links, TREE.links, (t - TREE_START) / GRAPH_MOVE_MS) };
  if (t < LADDER_START) return held(TREE);
  if (t < LADDER_END) return { dots: move(TREE.dots, LADDER, t - LADDER_START), curve: RESTING_CURVE, u: null, curveOpacity: 0, ...crossLinks(TREE.links, LADDER.links, (t - LADDER_START) / GRAPH_MOVE_MS) };
  if (t < RETURN_START) return held(LADDER);
  // back into the logo: the dots fly home (overshooting), the ladder's links fade, and the logo's own lines come back
  const k = (t - RETURN_START) / (RETURN_END - RETURN_START);
  const e = easeOutBack(k);
  return { dots: LADDER.dots.map((point, dot) => lerpPoint(point, LOGO_POINTS[dot], e)), curve: RESTING_CURVE, u: null, curveOpacity: 0, ...crossLinks(LADDER.links, LOGO_LINKS, k) };
}

// ---- the runner and its trail -----------------------------------------------------------------------------------------------------------------------------------------------------

/** A point on a line through the dots (sampled at CURVE_SAMPLES + 1 places, 0 to 1), at `u`. */
function pointAlong(curve: ReadonlyArray<Point>, u: number): Point {
  const at = clamp01(u) * (curve.length - 1);
  const i = Math.min(Math.floor(at), curve.length - 2);
  return lerpPoint(curve[i], curve[i + 1], at - i);
}

/** The way from dot `a` towards dot `b`, as far as `k` (0 to 1) of it: along the line the dots are on when they are on one, and straight when they are not (the logo's own lines, or while they fly). */
function hopPath(layout: Layout, a: number, b: number, k: number): Point[] {
  const from = layout.dots[a];
  const to = layout.dots[b];
  if (!layout.u || k <= 0) return [from, lerpPoint(from, to, k)];
  if (layout.side && layout.side[a] !== layout.side[b]) return [from, lerpPoint(from, to, k)];       // from one strand of the helix to the other: straight across, there is no line between them
  const curve = layout.side && layout.side[a] === 1 && layout.second ? layout.second : layout.curve;
  const steps = Math.max(1, Math.ceil(8 * k));
  const path: Point[] = [from];
  for (let i = 1; i <= steps; i += 1) path.push(i === steps && k >= 1 ? to : pointAlong(curve, lerp(layout.u[a], layout.u[b], (k * i) / steps)));
  return path;
}

/** How far along step `segment` of a run the runner is at `t` (0 to 1). It eases a little as it reaches each dot, so the pop lands with it. */
function along(t: number, times: number[], segment: number): number {
  const k = clamp01((t - times[segment]) / (times[segment + 1] - times[segment]));
  return 0.5 * k + 0.5 * easeInOut(k);
}

/** The run that is going at `t`: each stage has its own, and it is over (trail and pops too) by the time the stage ends. */
const runAt = (t: number): Run => RUNS[Math.min(RUNS.length - 1, Math.max(0, Math.floor(t / STAGE_MS)))];

/** The runner at `t`: where it is, and how visible (it fades in as it sets off and out after the last dot). */
function runnerAt(t: number, run: Run, layout: Layout): RunnerPose {
  const times = runTimes(run);
  const end = times[times.length - 1];
  const first = layout.dots[run.dots[0]];
  if (run.burst) return { x: round(first[0]), y: round(first[1]), opacity: 0 };       // a burst is the lines shooting out from the hub: there is no single runner
  if (t < run.start || t > end + 250) return { x: round(first[0]), y: round(first[1]), opacity: 0 };
  if (t >= end) {
    const last = layout.dots[run.dots[run.dots.length - 1]];
    return { x: round(last[0]), y: round(last[1]), opacity: round(1 - (t - end) / 250) };
  }
  let segment = 0;
  while (times[segment + 1] <= t) segment += 1;
  const path = hopPath(layout, run.dots[segment], run.dots[segment + 1], along(t, times, segment));
  const here = path[path.length - 1];
  return { x: round(here[0]), y: round(here[1]), opacity: round(Math.min(1, (t - run.start) / 120)) };
}

/**
 * The dashed trail behind the runner: one piece for each step of the run. A piece grows from the dot the runner left towards the runner, and is whole once it has got to the next. It stays while those two dots are
 * among the last two the runner has touched, so there are at most two pieces on show (the whole one behind, and the one growing); when the runner touches the dot after it, the older piece fades. The last piece
 * stays a moment after the runner is done, and then fades too: just as the next stage's run begins.
 */
function trailAt(t: number, run: Run, layout: Layout): TrailPose[] {
  const times = runTimes(run);
  const end = times[times.length - 1];
  if (run.burst) {
    // a line shoots from the hub to each point, all together; they stay a moment and fade together once they have arrived
    return Array.from({ length: TRAIL_SLOTS }, (_, segment) => {
      if (segment >= run.dots.length - 1) return { points: [[0, 0], [0, 0]] as Point[], opacity: 0 };
      const path = hopPath(layout, run.dots[0], run.dots[segment + 1], along(t, [times[0], end], 0));
      return { points: path.map(roundPoint), opacity: round(Math.min(clamp01((t - times[0]) / TRAIL_FADE_IN_MS), 1 - clamp01((t - (end + TRAIL_HOLD_MS)) / TRAIL_FADE_MS))) };
    });
  }
  return Array.from({ length: TRAIL_SLOTS }, (_, segment) => {
    if (segment >= run.dots.length - 1) return { points: [[0, 0], [0, 0]] as Point[], opacity: 0 };
    const from = layout.dots[run.dots[segment]];
    if (t < times[segment]) return { points: [roundPoint(from), roundPoint(from)], opacity: 0 };
    const path = hopPath(layout, run.dots[segment], run.dots[segment + 1], along(t, times, segment));
    const fadeFrom = segment + 2 < times.length ? times[segment + 2] : end + TRAIL_HOLD_MS;
    return { points: path.map(roundPoint), opacity: round(Math.min(clamp01((t - times[segment]) / TRAIL_FADE_IN_MS), 1 - clamp01((t - fadeFrom) / TRAIL_FADE_MS))) };
  });
}

// ---- pulses -----------------------------------------------------------------------------------------------------------------------------------------------------------------------------

/** A pulse: a dot stretches, squashes and settles, and sends out a ring, because the runner has reached it. There is no other kind. */
export interface PulseEvent { dot: number; time: number }

/** Every pulse of a cycle, in time order: one at each dot the runner reaches, in every run. */
export const PULSES: ReadonlyArray<PulseEvent> = RUNS.flatMap(run => runTimes(run).map((time, i) => ({ dot: run.dots[i], time }))).sort((a, b) => a.time - b.time);

/** One dot's pop and ring, `since` ms after the runner reached it: it stretches tall, squashes, and settles; a ring spreads from it and fades. Nothing before the runner arrives or once it has settled. */
function popAt(since: number): Pick<DotPose, 'scaleX' | 'scaleY' | 'ringScale' | 'ringOpacity'> {
  const rest = { scaleX: 1, scaleY: 1, ringScale: 1, ringOpacity: 0 };
  if (since < 0 || since >= Math.max(POP_MS, RING_MS)) return rest;
  const u = Math.min(since / POP_MS, 1);
  const spring = Math.sin(2 * Math.PI * 1.5 * u) * Math.exp(-2.4 * u);
  const ring = since / RING_MS;
  return { scaleX: round(1 - 0.5 * spring), scaleY: round(1 + 0.9 * spring), ringScale: round(1 + 2.4 * easeOut(Math.min(ring, 1))), ringOpacity: round(0.7 * (1 - Math.min(ring, 1)) * clamp01(since / 60)) };          // the ring comes in over its first 60 ms, so a cycle can start at rest with the first dot's pulse
}

/** The SVG transform for a body: squashed and stretched about the ground under the middle of the mark, and lifted off it. */
export const bodyTransform = (body: Body): string => `translate(${MID_X} ${round(GROUND_Y + body.lift)}) scale(${round(body.scaleX)} ${round(body.scaleY)}) translate(${-MID_X} ${-GROUND_Y})`;

/** Where everything is `t` ms into a cycle (`t` from 0 to LOOP_MS). At 0 and at LOOP_MS it is the plain mark, at rest. */
export function logoPose(t: number): LogoPose {
  const at = Math.min(Math.max(t, 0), LOOP_MS);
  const scaleY = heightScale(at);
  const body: Body = { lift: liftAt(at), scaleX: widthFor(scaleY), scaleY };
  const transform = bodyTransform(body);
  const layout = layoutAt(at);
  const run = runAt(at);
  const dots = layout.dots.map((point, dot) => {
    const reached = PULSES.find(pulse => pulse.dot === dot && at - pulse.time >= 0 && at - pulse.time < Math.max(POP_MS, RING_MS));
    return { x: round(point[0]), y: round(point[1]), ...popAt(reached ? at - reached.time : -1) };
  });
  return {
    transform,
    body,
    dots,
    links: Array.from({ length: LINK_SLOTS }, (_, slot) => layout.links[slot] ?? null),
    edgeOpacity: round(layout.edgeOpacity),
    curve: { points: layout.curve.map(roundPoint), opacity: round(layout.curveOpacity), ...(layout.second ? { second: { points: layout.second.map(roundPoint), opacity: round(layout.secondOpacity ?? 0) } } : {}) },
    runner: runnerAt(at, run, layout),
    trail: trailAt(at, run, layout),
  };
}

/** The pose `elapsed` ms after the loader appeared: the cycles follow one another. */
export function poseAt(elapsed: number): LogoPose {
  const time = Math.max(elapsed, 0);
  return logoPose(time - Math.floor(time / LOOP_MS) * LOOP_MS);
}

/** The still drawing for reduced motion: the plain mark. */
export const RESTING_POSE: LogoPose = logoPose(0);

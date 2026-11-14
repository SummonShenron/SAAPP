// The graph loader's rules, with no React in them so they can be tested (graphLoader.test.mjs). components/LoaderGraphs.tsx draws them.
//
// WHAT IT IS (2026-11-13, designed with Jack in loader-preview.html; the horizontal fork was replaced by this on 2026-11-14). While a reply is being worked on, the conversation draws the steps as nodes on a
// vertical trunk. The loader is not a spinner but a graph, and it is ONE graph in two places:
//   - Before any step has arrived, a VERTICAL GRAPH runs down the left beside "Thinking...": a trunk with a side node in each of three stretches. The light takes the side detour or goes straight down, and the
//     other way next time round.
//   - When the first step lands, the light carries on down the way it was going to the last node, which becomes that step's real node; the graph keeps its shape and fades as a whole, and the space closes.
//   - Under the newest step the same graph hangs in the gap between that step and the next one, its first node on the dashed stub that runs down from the step and its last node exactly where the next step's dot
//     will be. When the next step lands it does the same as the first: the light carries on to the last node, the graph fades, the space closes, and the new step's own graph begins.
// Nothing here is a claim about what Sonic is doing: the graph only shows that work is going on and that a step has landed.

/** A curved branch going down from (x1,y1) to (x2,y2), the way a flow chart draws a decision: it leaves and arrives vertically. */
export function downPath(x1: number, y1: number, x2: number, y2: number): string {
  const mid = (y1 + y2) / 2;
  return `M ${x1} ${y1} C ${x1} ${mid}, ${x2} ${mid}, ${x2} ${y2}`;
}

// ---- the vertical graph ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
/** The trunk's nodes (the last is where the next step's own node will be) and one side node per stretch, in an 44 x 66 box. */
export const START_BOX = { width: 44, height: 66 } as const;
export const START_TRUNK: readonly [number, number][] = [[7, 6], [7, 24], [7, 42], [7, 60]];
export const START_SIDES: readonly [number, number][] = [[24, 15], [24, 33], [24, 51]];
/** Per stretch, the two ways down in the order they light: out through the side node (edge, node, edge, node), or straight down the trunk (edge, node). */
export const START_ROUTES: readonly (readonly (readonly string[])[])[] = [0, 1, 2].map(k => [[`ts${k}`, `s${k}`, `st${k}`, `t${k + 1}`], [`tt${k}`, `t${k + 1}`]]);
export const START_PASSES: readonly (readonly number[])[] = [[0, 1, 0], [1, 0, 1]];
export interface StartFrame { pass: number; seg: number; lit: number; fade: boolean }
export function buildStartFrames(): StartFrame[] {
  const out: StartFrame[] = [];
  START_PASSES.forEach((routes, pass) => {
    [0, 1, 2].forEach(seg => {
      const length = START_ROUTES[seg][routes[seg]].length;
      for (let s = 1; s <= length; s++) out.push({ pass, seg, lit: s, fade: false });
      out.push({ pass, seg, lit: length, fade: false });                                          // hold on the new trunk node
    });
    out.push({ pass, seg: 2, lit: START_ROUTES[2][routes[2]].length, fade: true });              // then everything fades before the other way round
  });
  return out;
}
export const START_FRAMES: readonly StartFrame[] = buildStartFrames();
/** The beat the reduced-motion graph holds on. */
export const STATIC_START_FRAME = 4;

/** Everything lit at a beat: the first node, the whole way already travelled, and the part of the current stretch reached. Pure. */
export function startLit(frame: StartFrame): Set<string> {
  const routes = START_PASSES[frame.pass];
  const on = new Set<string>(['t0']);
  for (let seg = 0; seg < frame.seg; seg++) START_ROUTES[seg][routes[seg]].forEach(id => on.add(id));
  START_ROUTES[frame.seg][routes[frame.seg]].slice(0, frame.lit).forEach(id => on.add(id));
  return on;
}
/** The whole way a pass takes, first node to last, in lighting order. When a step lands the light carries on along this, detours included, so the graph never straightens. */
export function startWay(pass: number): string[] {
  const routes = START_PASSES[pass];
  const way = ['t0'];
  [0, 1, 2].forEach(seg => way.push(...START_ROUTES[seg][routes[seg]]));
  return way;
}
/** Where on the path each element lights up while a step lands: one after another, this many milliseconds apart. */
export const LANDING_STAGGER_MS = 55;

// ---- where the graph sits ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
// A step's row has its dot centred 10px below the row's top (3px + half of 14px). The graph's last node is at y = 60 and must be exactly on the dot of the step that lands.
/** Before the first step: the room above the list (which has a 4px top margin) that puts the last node on the first step's dot. */
export const START_SPACE_PX = START_TRUNK[3][1] - 4 - 10;
/** Under a step: the room its row keeps below its text while it is the newest (the next row starts after it), so the box starts there and its last node meets the next row's dot. */
export const GAP_SPACE_PX = START_TRUNK[3][1] - 10;
/** The box hangs this far below its row's bottom edge: the last node is 10px below the edge (the next dot), and the box goes on for the rest of its height below that node. */
export const GAP_OVERHANG_PX = START_BOX.height - START_TRUNK[3][1] + 10;
/** The dashed stub from the step's dot stops at the graph's first node, this far above the row's bottom edge. */
export const GAP_STUB_END_PX = GAP_SPACE_PX - START_TRUNK[0][1];

// ---- which arrival is this ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------
export type Arrival = { kind: 'merge'; mergeId: string } | null;
/**
 * A new newest step has arrived while the previous newest was showing its graph: that graph lands in it. `previousLastId` is the id of the newest step before this render; `steps` is the list now (only
 * the last few are drawn while a turn runs, so this goes by ids, never by a count). Nothing lands when the list started empty (the first step lands the start graph instead, in StepsDisclosure), when the
 * previous newest is not the one just before the new one (steps came in a burst), or when nothing changed. Pure.
 */
export function arrivalOf(previousLastId: string | null, steps: ReadonlyArray<{ id: string }>): Arrival {
  if (previousLastId === null || steps.length < 2) return null;
  const last = steps[steps.length - 1].id;
  if (last === previousLastId) return null;
  return steps[steps.length - 2].id === previousLastId ? { kind: 'merge', mergeId: previousLastId } : null;
}
/** The first step has landed: the list was empty and now is not. Pure. */
export function firstStepLanded(previousCount: number, count: number): boolean {
  return previousCount === 0 && count >= 1;
}

// ---- timings, in milliseconds from the moment a step arrives -----------------------------------------------------------------------------------------------------------------------------------------
export const TICK_MS = 260;
export const LAND_LEAVE_AT = 1100;        // the graph starts to fade (the step's node has popped in)
export const LAND_END_AT = 1500;          // the graph is removed, the space closes, the step's own graph begins
/** The new step's node and title fade in this long after it lands (about when the light reaches its node). */
export const ARRIVE_DELAY_MS = 720;

// The graph loader's rules, with no React in them so they can be tested (graphLoader.test.mjs). components/GraphLoader.tsx draws them.
//
// WHAT IT IS (2026-11-13, designed with Jack in loader-preview.html). While a reply is being worked on, the conversation draws the steps as nodes on a vertical trunk. The loader is not a spinner but a graph:
//   - Before any step has arrived, a VERTICAL START GRAPH runs down the left: a trunk with a side node in each of three stretches. The light takes the side detour or goes straight down, and the other way next time round.
//   - When the first step lands, the light carries on down the way it was going to the last node, which becomes that step's real node; the graph keeps its shape and fades as a whole, and the space closes.
//   - Under the newest step hangs a PAGED FORK: three pages (a plain Y, a diamond whose branches rejoin, an uneven pair), two routes through each. A route lights up, the view follows its end onto the next page, and
//     the second time round the other routes are taken (3 pages x 2 routes = 6 animations).
//   - When the next step lands, the fork is held for a moment, its chosen route lights to the end, the way back is drawn from that right-hand end and curves into the new step's node, which pops in; the fork fades
//     and the space it held closes. Then the new step's own fork begins.
// Nothing here is a claim about what Sonic is doing: the graph only shows that work is going on and that a step has landed.

/** One drawn node: x, y and radius in the page's own units. */
export type Dot = [number, number, number];
export interface ForkPage {
  nodes: Record<string, Dot>;
  edges: Record<string, [string, string]>;          // id -> from, to
  routes: string[][];                               // the two ways through: edge, node, edge, node ... in the order they light up
  root: string;
}

export const FORK_PAGES: readonly ForkPage[] = [
  {
    root: 'R',
    nodes: { R: [5, 16, 3.6], T: [22, 8, 2.8], B: [22, 24, 2.8], TL: [44, 6, 2.6], BL: [44, 26, 2.6] },
    edges: { RT: ['R', 'T'], RB: ['R', 'B'], TTL: ['T', 'TL'], BBL: ['B', 'BL'] },
    routes: [['RT', 'T', 'TTL', 'TL'], ['RB', 'B', 'BBL', 'BL']],
  },
  {
    root: 'R',
    nodes: { R: [5, 16, 3.6], T: [21, 6, 2.8], B: [21, 26, 2.8], M: [44, 16, 2.8] },
    edges: { RT: ['R', 'T'], RB: ['R', 'B'], TM: ['T', 'M'], BM: ['B', 'M'] },
    routes: [['RT', 'T', 'TM', 'M'], ['RB', 'B', 'BM', 'M']],
  },
  {
    root: 'R',
    nodes: { R: [5, 16, 3.6], T: [26, 8, 2.8], TL: [46, 8, 2.6], B1: [16, 25, 2.5], B2: [30, 25, 2.5], BL: [46, 25, 2.6] },
    edges: { RT: ['R', 'T'], TTL: ['T', 'TL'], RB1: ['R', 'B1'], B1B2: ['B1', 'B2'], B2BL: ['B2', 'BL'] },
    routes: [['RT', 'T', 'TTL', 'TL'], ['RB1', 'B1', 'B1B2', 'B2', 'B2BL', 'BL']],
  },
];
/** The route taken on each page: first time round, then the other way. */
export const FORK_PASSES: readonly (readonly number[])[] = [[0, 1, 0], [1, 0, 1]];

/** One beat of the fork's loop. lit: how many elements of the route are lit; pan: 0 none, 1 the next page is placed and about to move in, 2 it is moving. */
export interface ForkFrame { pass: number; page: number; lit: number; pan: 0 | 1 | 2 }
/** Every beat of the loop: each page lights its route element by element, holds on the chosen end for two beats, then the view follows that end onto the next page. */
export function buildForkFrames(): ForkFrame[] {
  const out: ForkFrame[] = [];
  FORK_PASSES.forEach((routes, pass) => FORK_PAGES.forEach((page, p) => {
    const length = page.routes[routes[p]].length;
    for (let s = 1; s <= length; s++) out.push({ pass, page: p, lit: s, pan: 0 });
    out.push({ pass, page: p, lit: length, pan: 0 }, { pass, page: p, lit: length, pan: 0 });
    out.push({ pass, page: p, lit: length, pan: 1 }, { pass, page: p, lit: length, pan: 2 });
  }));
  return out;
}
export const FORK_FRAMES: readonly ForkFrame[] = buildForkFrames();
/** The beat the reduced-motion fork holds on: the first page, its first route lit to the end. */
export const STATIC_FORK_FRAME = FORK_PAGES[0].routes[0].length - 1;

/** What is on screen at a fork beat. Pure. */
export interface ForkView {
  page: ForkPage; pageIndex: number; route: number; routeIds: readonly string[]; lit: ReadonlySet<string>;
  leaf: [number, number]; root: [number, number];
  /** What it takes to bring the chosen end to where the next page's root is (the view follows the path). */
  move: [number, number];
  next: { page: ForkPage; pageIndex: number; pass: number; route: number };
}
export function forkView(frame: ForkFrame): ForkView {
  const route = FORK_PASSES[frame.pass][frame.page];
  const page = FORK_PAGES[frame.page];
  const routeIds = page.routes[route];
  const leafId = routeIds[routeIds.length - 1];
  const [lx, ly] = page.nodes[leafId];
  const [rx, ry] = page.nodes[page.root];
  const nextIndex = (frame.page + 1) % FORK_PAGES.length;
  const nextPass = frame.page === FORK_PAGES.length - 1 ? (frame.pass + 1) % FORK_PASSES.length : frame.pass;
  return {
    page, pageIndex: frame.page, route, routeIds, lit: new Set(routeIds.slice(0, frame.lit)),
    leaf: [lx, ly], root: [rx, ry], move: [rx - lx, ry - ly],
    next: { page: FORK_PAGES[nextIndex], pageIndex: nextIndex, pass: nextPass, route: FORK_PASSES[nextPass][nextIndex] },
  };
}

/** A curved branch from (x1,y1) to (x2,y2), the way a flow chart draws a decision. */
export function branchPath(x1: number, y1: number, x2: number, y2: number): string {
  const mid = (x1 + x2) / 2;
  return `M ${x1} ${y1} C ${mid} ${y1}, ${mid} ${y2}, ${x2} ${y2}`;
}
/** The same going down: the curve leaves and arrives vertically (the start graph). */
export function downPath(x1: number, y1: number, x2: number, y2: number): string {
  const mid = (y1 + y2) / 2;
  return `M ${x1} ${y1} C ${x1} ${mid}, ${x2} ${mid}, ${x2} ${y2}`;
}

// ---- the merge: the fork's chosen end swings back into the new step's node --------------------------------------------------------------------------------------------------------------------------
/** The fork is drawn in a 60 x 46 box; the new step's node is below the pages, centred at y = MERGE_NODE_Y (the dot is about 6 units across). */
export const FORK_BOX = { width: 60, height: 46 } as const;
export const MERGE_NODE_Y = 40;
/** The way back runs along this line, in the gap above the new step: the step's title sits level with its node, so a line at that height would strike through it. */
export const MERGE_LAND_Y = 29;
/** Where the way back meets the node's rim: from above and to the right, inside the left gutter, so it hooks into the node and never crosses the title. */
export const MERGE_RIM: [number, number] = [10.4, MERGE_NODE_Y - 4.6];
/** The way back for a frame: from the chosen route's right-hand end (always the far end, wherever the light had got to), round, along the low line and into the node's rim. */
export function mergePath(view: Pick<ForkView, 'leaf'>): string {
  const [lx, ly] = view.leaf;
  const [rx, ry] = MERGE_RIM;
  return `M ${lx} ${ly} C ${lx + 10} ${ly}, ${lx + 10} ${MERGE_LAND_Y}, ${lx - 2} ${MERGE_LAND_Y} L 17 ${MERGE_LAND_Y} C 12 ${MERGE_LAND_Y}, ${rx + 0.4} ${ry - 3.5}, ${rx} ${ry}`;
}

// ---- the vertical start graph ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------
/** The trunk's nodes (the last is where the first step's own node will be) and one side node per stretch, in an 44 x 66 box. */
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
/** The beat the reduced-motion start graph holds on. */
export const STATIC_START_FRAME = 4;

/** Everything lit at a start beat: the first node, the whole way already travelled, and the part of the current stretch reached. Pure. */
export function startLit(frame: StartFrame): Set<string> {
  const routes = START_PASSES[frame.pass];
  const on = new Set<string>(['t0']);
  for (let seg = 0; seg < frame.seg; seg++) START_ROUTES[seg][routes[seg]].forEach(id => on.add(id));
  START_ROUTES[frame.seg][routes[frame.seg]].slice(0, frame.lit).forEach(id => on.add(id));
  return on;
}
/** The whole way a pass takes, first node to last, in lighting order. When the first step lands the light carries on along this, detours included, so the graph never straightens. */
export function startWay(pass: number): string[] {
  const routes = START_PASSES[pass];
  const way = ['t0'];
  [0, 1, 2].forEach(seg => way.push(...START_ROUTES[seg][routes[seg]]));
  return way;
}
/** Where on the path each element lights up while the first step lands: one after another, this many milliseconds apart. */
export const LANDING_STAGGER_MS = 55;

// ---- which arrival is this ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------
export type Arrival = { kind: 'merge'; mergeId: string } | null;
/**
 * A new newest step has arrived while the previous newest was showing its fork: that fork merges into it. `previousLastId` is the id of the newest step before this render; `steps` is the list now (only
 * the last few are drawn while a turn runs, so this goes by ids, never by a count). Nothing merges when the list started empty (the start graph lands instead, in StepsDisclosure), when the previous newest
 * is not the one just before the new one (steps came in a burst), or when nothing changed. Pure.
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
export const MERGE_DRAW_AT = 220;          // the way back starts being drawn (after the chosen route has lit to its end)
export const MERGE_LAND_AT = 700;          // it has landed
export const MERGE_LEAVE_AT = 1000;        // the held fork starts to fade
export const MERGE_END_AT = 1300;          // the fork is removed, the space closes, the new step's own fork begins
export const START_LEAVE_AT = 1100;        // the start graph starts to fade (the first step's node has popped in)
export const START_END_AT = 1500;          // the start graph is removed, the space closes, the first step's fork begins
/** The new step's node and title fade in this long after it lands (about when the way back reaches it). */
export const ARRIVE_DELAY_MS = 720;
/** The space under the start graph, so that its last node sits exactly where the first step's dot is: the list has a 4px top margin and the dot's centre is 10px below its row's top, and the last node is at y = 60. */
export const START_SPACE_PX = START_TRUNK[3][1] - 4 - 10;

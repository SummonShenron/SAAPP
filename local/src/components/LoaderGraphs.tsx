import React, { useEffect, useState } from 'react';
import {
  FORK_BOX, FORK_FRAMES, LANDING_STAGGER_MS, MERGE_DRAW_AT, MERGE_LAND_AT, MERGE_LEAVE_AT, START_BOX, START_FRAMES, START_LEAVE_AT, START_SIDES, START_TRUNK, STATIC_FORK_FRAME, STATIC_START_FRAME, TICK_MS,
  branchPath, downPath, forkView, mergePath, startLit, startWay,
  type ForkFrame, type ForkPage,
} from './graphLoader';
import './__styles__/TracePanel.css';

// The graph loader's two drawings (the rules are in graphLoader.ts, the look in __styles__/TracePanel.css): the VERTICAL START GRAPH shown before any step has arrived, and the PAGED FORK that hangs under the
// newest step of a running turn. Both are decoration for sighted people only (aria-hidden): the words beside them ("Thinking...", the step's own title) say what is going on.

/** A counter that goes up every `ms` while `active`. */
function useTick(ms: number, active: boolean): number {
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (!active) return undefined;
    const timer = window.setInterval(() => setTick(t => t + 1), ms);
    return () => window.clearInterval(timer);
  }, [ms, active]);
  return tick;
}

/** One page of the fork drawn: only its own route lights (up to what has been reached); the rest is dim. `fade` takes the dimmed parts away while the view moves on. */
function PageGroup({ page, routeIds, lit, fade, dx, dy }: { page: ForkPage; routeIds: readonly string[]; lit: ReadonlySet<string>; fade: boolean; dx: number; dy: number }) {
  const inRoute = new Set(routeIds);
  return (
    <g className="gl-page" style={{ transform: `translate(${dx}px, ${dy}px)` }}>
      {Object.entries(page.edges).map(([id, [from, to]]) => {
        const a = page.nodes[from];
        const b = page.nodes[to];
        return <path key={id} className={`gl-link${lit.has(id) ? ' gl-on' : ''}${fade && !inRoute.has(id) ? ' gl-gone' : ''}`} d={branchPath(a[0], a[1], b[0], b[1])} />;
      })}
      {Object.entries(page.nodes).map(([id, [x, y, r]]) => (
        <circle key={id} className={`gl-fnode${id === page.root || lit.has(id) ? ' gl-on' : ''}${fade && id !== page.root && !inRoute.has(id) ? ' gl-gone' : ''}`} cx={x} cy={y} r={r} />
      ))}
    </g>
  );
}

/**
 * The fork under the newest step: three pages with two routes through each (graphLoader.ts), looping. With `merging` (the next step has just landed) it freezes where it is, lights its chosen route to the end at
 * once, draws the way back from that right-hand end into the new step's node, and then fades. `hold` keeps the merge on screen (the dev preview only).
 */
export const PagedFork: React.FC<{ merging?: boolean; reducedMotion?: boolean; hold?: boolean }> = ({ merging = false, reducedMotion = false, hold = false }) => {
  const tick = useTick(TICK_MS, !reducedMotion);
  const liveIndex = reducedMotion ? STATIC_FORK_FRAME : tick % FORK_FRAMES.length;
  // The beat the merge froze on, and how far it has got: 0 not yet drawn, 1 drawing, 2 landed, 3 fading out. It freezes in the very render in which `merging` turns on (and lets go when it turns off), so there is no frame
  // that draws the merge without the freeze; the effect below only runs the clock.
  const [held, setHeld] = useState<{ index: number; stage: 0 | 1 | 2 | 3 } | null>(() => (merging ? { index: liveIndex, stage: 0 } : null));
  const [wasMerging, setWasMerging] = useState(merging);
  if (merging !== wasMerging) {
    setWasMerging(merging);
    setHeld(merging ? { index: liveIndex, stage: 0 } : null);
  }
  useEffect(() => {
    if (!merging) return undefined;
    const timers = [
      window.setTimeout(() => setHeld(h => (h ? { ...h, stage: 1 } : h)), MERGE_DRAW_AT),
      window.setTimeout(() => setHeld(h => (h ? { ...h, stage: 2 } : h)), MERGE_LAND_AT),
      ...(hold ? [] : [window.setTimeout(() => setHeld(h => (h ? { ...h, stage: 3 } : h)), MERGE_LEAVE_AT)]),
    ];
    return () => timers.forEach(window.clearTimeout);
  }, [merging, hold]);
  const base = FORK_FRAMES[held ? held.index : liveIndex];
  const frame: ForkFrame = held ? { ...base, pan: 0, lit: forkView(base).routeIds.length } : base;          // a merge lights the chosen route to its end at once
  const view = forkView(frame);
  const panning = frame.pan > 0;
  const moving = frame.pan === 2;
  const stage = held ? held.stage : 0;
  const nextLit: ReadonlySet<string> = new Set();
  return (
    <svg className={`gl-svg gl-fork-svg${held && stage === 3 ? ' gl-leaving' : ''}`} width={70} height={54} viewBox={`0 0 ${FORK_BOX.width} ${FORK_BOX.height}`} aria-hidden="true" focusable="false">
      <PageGroup key={`p${frame.pass}-${frame.page}`} page={view.page} routeIds={view.routeIds} lit={view.lit} fade={moving || (!!held && stage >= 1)} dx={moving ? view.move[0] : 0} dy={moving ? view.move[1] : 0} />
      {panning && <PageGroup key={`p${view.next.pass}-${view.next.pageIndex}`} page={view.next.page} routeIds={view.next.page.routes[view.next.route]} lit={nextLit} fade={false} dx={moving ? 0 : -view.move[0]} dy={moving ? 0 : -view.move[1]} />}
      {held && <path className={`gl-wrap${stage >= 1 ? ' gl-drawn' : ''}`} pathLength={1} d={mergePath(view)} />}
    </svg>
  );
};

/**
 * The vertical start graph: a trunk down the left with a side node in each of three stretches, the light taking the detour or going straight down (graphLoader.ts). With `landing` (the first step has just
 * landed) it freezes, carries the light on down the way it was going, detours included, to the last node (which the step's own node then covers), and fades as a whole: nothing is straightened or removed.
 */
export const StartGraph: React.FC<{ landing?: boolean; reducedMotion?: boolean; hold?: boolean }> = ({ landing = false, reducedMotion = false, hold = false }) => {
  const tick = useTick(TICK_MS, !reducedMotion);
  const liveIndex = reducedMotion ? STATIC_START_FRAME : tick % START_FRAMES.length;
  // Frozen in the very render in which `landing` turns on, so the light carries on from exactly where it was (and the same instance stays on screen: StepsDisclosure keeps it in one place); the effect only runs the clock.
  const [froze, setFroze] = useState<{ index: number; leaving: boolean } | null>(() => (landing ? { index: liveIndex, leaving: false } : null));
  const [wasLanding, setWasLanding] = useState(landing);
  if (landing !== wasLanding) {
    setWasLanding(landing);
    setFroze(landing ? { index: liveIndex, leaving: false } : null);
  }
  useEffect(() => {
    if (!landing || hold) return undefined;
    const timer = window.setTimeout(() => setFroze(f => (f ? { ...f, leaving: true } : f)), START_LEAVE_AT);
    return () => window.clearTimeout(timer);
  }, [landing, hold]);
  const frame = START_FRAMES[froze ? froze.index : liveIndex];
  const way = landing ? startWay(frame.pass) : [];
  const lit = landing ? new Set(way) : startLit(frame);
  const fade = !landing && frame.fade;
  const delay = (id: string) => (landing && way.includes(id) ? { transitionDelay: `${way.indexOf(id) * LANDING_STAGGER_MS}ms` } : undefined);
  const cls = (base: string, id: string) => `${base}${lit.has(id) ? ' gl-on' : ''}${fade ? ' gl-gone' : ''}`;
  const edges: Array<[string, string]> = [];
  [0, 1, 2].forEach(k => {
    edges.push([`tt${k}`, `M ${START_TRUNK[k][0]} ${START_TRUNK[k][1]} L ${START_TRUNK[k + 1][0]} ${START_TRUNK[k + 1][1]}`]);
    edges.push([`ts${k}`, downPath(START_TRUNK[k][0], START_TRUNK[k][1], START_SIDES[k][0], START_SIDES[k][1])]);
    edges.push([`st${k}`, downPath(START_SIDES[k][0], START_SIDES[k][1], START_TRUNK[k + 1][0], START_TRUNK[k + 1][1])]);
  });
  return (
    <svg className={`gl-svg gl-start${froze && froze.leaving ? ' gl-leaving' : ''}`} width={START_BOX.width} height={START_BOX.height} viewBox={`0 0 ${START_BOX.width} ${START_BOX.height}`} aria-hidden="true" focusable="false">
      {edges.map(([id, d]) => <path key={id} className={cls('gl-link', id)} style={delay(id)} d={d} />)}
      {START_TRUNK.map(([x, y], n) => <circle key={`t${n}`} className={cls('gl-fnode', `t${n}`)} style={delay(`t${n}`)} cx={x} cy={y} r={n === 3 ? 3.6 : 3.2} />)}
      {START_SIDES.map(([x, y], k) => <circle key={`s${k}`} className={cls('gl-fnode', `s${k}`)} style={delay(`s${k}`)} cx={x} cy={y} r={2.6} />)}
    </svg>
  );
};

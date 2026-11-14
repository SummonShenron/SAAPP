import React, { useEffect, useState } from 'react';
import {
  LANDING_STAGGER_MS, LAND_LEAVE_AT, START_BOX, START_FRAMES, START_SIDES, START_TRUNK, STATIC_START_FRAME, TICK_MS,
  downPath, startLit, startWay,
} from './graphLoader';
import './__styles__/TracePanel.css';

// The graph loader's drawing (the rules are in graphLoader.ts, the look in __styles__/TracePanel.css): ONE vertical graph, shown before any step has arrived and again under the newest step of a running turn,
// in the gap before the next one. It is decoration for sighted people only (aria-hidden): the words beside it ("Thinking...", the step's own title) say what is going on.

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

/**
 * The vertical graph: a trunk down the left with a side node in each of three stretches, the light taking the detour or going straight down (graphLoader.ts). With `landing` (the next step has just landed) it
 * freezes, carries the light on down the way it was going, detours included, to the last node (which the step's own node then covers), and fades as a whole: nothing is straightened or removed.
 * `hold` keeps the landing on screen instead of letting it finish (the dev preview only). With `stem` (the graph under a step) its first node is not drawn: a line from the step's own node (TracePanel.css) runs
 * down to where the first split begins, so the graph grows out of that node instead of starting from a small node of its own.
 */
export const StartGraph: React.FC<{ landing?: boolean; reducedMotion?: boolean; hold?: boolean; stem?: boolean }> = ({ landing = false, reducedMotion = false, hold = false, stem = false }) => {
  const tick = useTick(TICK_MS, !reducedMotion);
  const liveIndex = reducedMotion ? STATIC_START_FRAME : tick % START_FRAMES.length;
  // Frozen in the very render in which `landing` turns on, so the light carries on from exactly where it was (and the same instance stays on screen: its parent keeps it in one place); the effect only runs the clock.
  const [froze, setFroze] = useState<{ index: number; leaving: boolean } | null>(() => (landing ? { index: liveIndex, leaving: false } : null));
  const [wasLanding, setWasLanding] = useState(landing);
  if (landing !== wasLanding) {
    setWasLanding(landing);
    setFroze(landing ? { index: liveIndex, leaving: false } : null);
  }
  useEffect(() => {
    if (!landing || hold) return undefined;
    const timer = window.setTimeout(() => setFroze(f => (f ? { ...f, leaving: true } : f)), LAND_LEAVE_AT);
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
      {START_TRUNK.map(([x, y], n) => (stem && n === 0 ? null : <circle key={`t${n}`} className={cls('gl-fnode', `t${n}`)} style={delay(`t${n}`)} cx={x} cy={y} r={n === 3 ? 3.6 : 3.2} />))}
      {START_SIDES.map(([x, y], k) => <circle key={`s${k}`} className={cls('gl-fnode', `s${k}`)} style={delay(`s${k}`)} cx={x} cy={y} r={2.6} />)}
    </svg>
  );
};

import React, { useEffect, useMemo, useState } from 'react';
import {
  FIRST_NODE_Y, LEAVE_FADE_MS, LIGHT_MS, LAND_LEAVE_AT, START_BOX, TICK_MS, TRUNK_X,
  buildStartFrames, landingStagger, pickFor, randomPick, scaled, staticFrameIndex, startLit, startWay,
} from './graphLoader';
import type { StepKind } from './stepKinds';
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
 * The vertical graph: a trunk down the left with side routes in its stretches, the light taking a detour or going straight down (graphLoader.ts). Under a running step it has the shape of that step's `kind` of action
 * (the same every time); before the first step, or for the general kind, it is one of the other shapes at random. Which way each stretch starts is random too. It is picked when the graph first appears and kept for as
 * long as it is on screen. With `landing` (the next step has just landed) it freezes, carries the light on down the way it was going, detours included, to the last node (which the
 * step's own node then covers), and fades as a whole: nothing is straightened or removed. `pace` (1 or more) plays that landing faster, for a step that came soon after the one before it.
 * `keep` (the steps in graph mode) leaves the graph on screen as it landed, the way the light took lit; `scale` draws it smaller. With a `seed` (the step's id) the shape and starting ways come from it, so the same step always
 * has the same graph; `settled` is the graph in the list kept under a finished reply: landed from the start, lit at once, nothing moving. `grayed` turns the way it took from the accent to gray (a kept graph once it has landed). `hold` keeps the landing on screen instead of letting it finish (the dev preview only). With `stem` (the graph under a step) its first node is not drawn: a line from the step's own node (TracePanel.css) runs
 * down to where the first split begins, so the graph grows out of that node instead of starting from a small node of its own.
 */
export const StartGraph: React.FC<{ landing?: boolean; reducedMotion?: boolean; hold?: boolean; stem?: boolean; pace?: number; kind?: StepKind; keep?: boolean; scale?: number; seed?: string; settled?: boolean; grayed?: boolean }> = ({ landing = false, reducedMotion = false, hold = false, stem = false, pace = 1, kind, keep = false, scale = 1, seed, settled = false, grayed = false }) => {
  const [pick] = useState(() => (seed ? pickFor(seed, kind) : randomPick(kind)));
  const frames = useMemo(() => buildStartFrames(pick), [pick]);
  const tick = useTick(TICK_MS, !reducedMotion && !settled && !landing);          // a graph that has landed, or is drawn finished, has nothing left to count
  const liveIndex = reducedMotion ? staticFrameIndex(frames) : tick % frames.length;
  // Frozen in the very render in which `landing` turns on, so the light carries on from exactly where it was (and the same instance stays on screen: its parent keeps it in one place); the effect only runs the clock.
  const [froze, setFroze] = useState<{ index: number; leaving: boolean } | null>(() => (landing ? { index: liveIndex, leaving: false } : null));
  const [wasLanding, setWasLanding] = useState(landing);
  if (landing !== wasLanding) {
    setWasLanding(landing);
    setFroze(landing ? { index: liveIndex, leaving: false } : null);
  }
  useEffect(() => {
    if (!landing || hold || keep) return undefined;          // a kept graph (the steps in graph mode) stays where it landed: nothing fades it
    const timer = window.setTimeout(() => setFroze(f => (f ? { ...f, leaving: true } : f)), scaled(LAND_LEAVE_AT, pace));
    return () => window.clearTimeout(timer);
  }, [landing, hold, keep, pace]);
  const frame = frames[froze ? froze.index : liveIndex];
  const way = landing ? startWay(pick, frame.pass) : [];
  const lit = landing ? new Set(way) : startLit(pick, frame);
  const fade = !landing && frame.fade;
  const stagger = landingStagger(way.length, pace);
  const delay = (id: string) => (landing && way.includes(id) ? { transitionDelay: `${Math.round(way.indexOf(id) * stagger)}ms`, transitionDuration: `${scaled(LIGHT_MS, pace)}ms` } : undefined);
  const cls = (base: string, id: string) => `${base}${lit.has(id) ? ' gl-on' : ''}${fade ? ' gl-gone' : ''}`;
  const leaving = !!froze && froze.leaving;
  // The last trunk node is where the next step's own dot is. A graph that is kept once it has landed does not draw it: it would be painted over that dot and hide its check. (While it runs, it is the end of the graph.)
  const lastTrunkId = `t${pick.shape.routes.length}`;
  return (
    <svg
      className={`gl-svg gl-start${leaving ? ' gl-leaving' : ''}${settled ? ' gl-settled' : ''}${grayed ? ' gl-grayed' : ''}`}
      style={leaving ? { transitionDuration: `${scaled(LEAVE_FADE_MS, pace)}ms` } : undefined}
      width={START_BOX.width}
      height={START_BOX.height}
      viewBox={`0 0 ${START_BOX.width} ${START_BOX.height}`}
      aria-hidden="true"
      focusable="false"
    >
      {/* A kept graph with steps after it shrinks (graphLoader.ts keptScale): the same shape, both axes by one factor, about its first node, so the stub from the step's dot still meets it. */}
      <g className="gl-shape" style={{ transform: `translate(${TRUNK_X}px, ${FIRST_NODE_Y}px) scale(${scale}) translate(${-TRUNK_X}px, ${-FIRST_NODE_Y}px)` }}>
        {pick.shape.edges.map(({ id, d }) => <path key={id} className={cls('gl-link', id)} style={delay(id)} d={d} />)}
        {pick.shape.nodes.map(({ id, x, y, r }) => ((stem && id === 't0') || (keep && landing && id === lastTrunkId) ? null : <circle key={id} className={cls('gl-fnode', id)} style={delay(id)} cx={x} cy={y} r={r} />))}
      </g>
    </svg>
  );
};

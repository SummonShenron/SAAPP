import React, { useEffect, useState } from 'react';
import TraceSteps, { type TraceStep } from './TraceSteps';
import { StartGraph } from './LoaderGraphs';
import { useReducedMotion } from './useReducedMotion';
import { START_END_AT, START_SPACE_PX, firstStepLanded } from './graphLoader';
import { stepsAreOpen, stepsLabel } from './traceFacts';
import './__styles__/TracePanel.css';

interface Props {
  steps: TraceStep[];
  /** True while the turn is running: before the first step the vertical start graph, then the steps as they come with a fork under the newest. */
  running: boolean;
  /** Keeps the ids of two lists that show the same steps (this one and the panel's) apart. */
  idPrefix: string;
  /** The newest reply: its steps stay open after the turn ends, until the next request starts (then they fold, unless the person opened or closed them). */
  latest?: boolean;
  /** Draw as if the person had asked for reduced motion (the dev preview and tests; the app leaves it to the system setting). */
  reducedMotion?: boolean;
  /** Keep a landing or a merge on screen instead of letting it finish (the dev preview only). */
  hold?: boolean;
}

/** While a turn runs, only the newest steps are drawn; the rest are one press away. */
export const LIVE_STEPS_SHOWN = 5;
const WAITING_TEXT = 'Thinking...';

/**
 * The steps of one turn. While the turn runs there is no line to open: the steps are right there as nodes, each keeping its own words (its title and what it is doing underneath), and the loader is a graph
 * (components/graphLoader.ts): before the first step a vertical graph runs down the left beside "Thinking...", the first step lands into its last node, and under the newest step a fork explores until the next
 * step lands and the fork merges into it. Anything a step measured or read opens under that step.
 * When the turn ends they settle into a "7 steps" line above the reply and stay there: open for the newest reply until the next request starts, then folded, opening to the same steps the trace panel shows
 * (components/TraceSteps.tsx). Kept in the page for as long as it is open; the read excerpts are never stored, so a reloaded conversation has none of this.
 */
const StepsDisclosure: React.FC<Props> = ({ steps, running, idPrefix, latest = false, reducedMotion, hold = false }) => {
  const [chosen, setChosen] = useState<boolean | null>(null);        // what the person pressed; null until they do
  const open = stepsAreOpen(chosen, latest);
  const [showAll, setShowAll] = useState(false);
  const bodyId = `${idPrefix}-steps`;
  const reduced = useReducedMotion(reducedMotion);

  // The first step has landed: the start graph runs on into its node. Worked out DURING the render that first has a step (React lets a component set its own state while rendering and renders again before anything
  // is drawn), so there is no frame with the step and no landing. Reduced motion skips it: the step simply appears.
  const [seen, setSeen] = useState(steps.length);
  const [landing, setLanding] = useState(false);
  if (steps.length !== seen) {
    setSeen(steps.length);
    if (running && !reduced && firstStepLanded(seen, steps.length)) setLanding(true);
    else if (steps.length === 0) setLanding(false);
  }
  useEffect(() => {
    if (!landing || hold) return undefined;
    const timer = window.setTimeout(() => setLanding(false), START_END_AT);
    return () => window.clearTimeout(timer);
  }, [landing, hold]);

  if (running) {
    const waiting = steps.length === 0;
    const all = steps;
    const hidden = showAll ? 0 : Math.max(0, all.length - LIVE_STEPS_SHOWN);
    // One wrapper in the same place whether the turn is waiting for its first step, landing it, or past it, so the start graph is ONE instance all through (a different tree would restart it, and the light
    // would not carry on from where it was). Its text and graph change place by class only.
    return (
      <div className={`steps-inline steps-inline-live${waiting ? ' steps-inline-waiting' : ''}`} id={bodyId} role={waiting ? 'status' : undefined}>
        {!waiting && hidden > 0 && (
          <button type="button" className="steps-toggle steps-earlier" onClick={() => setShowAll(true)}>
            <span className="steps-toggle-label">{stepsLabel(hidden)} before this</span>
            <span className="steps-toggle-chevron" aria-hidden="true">▸</span>
          </button>
        )}
        <div className={`gl-startwrap${waiting ? ' gl-start-wait' : landing ? '' : ' gl-start-settled'}`} style={landing ? { paddingTop: START_SPACE_PX } : undefined}>
          {(waiting || landing) && <StartGraph landing={landing} reducedMotion={reduced} hold={hold} />}
          {(waiting || landing) && <span className={`agent-status-text${landing ? ' gl-start-text gl-start-text-out' : ''}`}><span className="steps-first-title">{WAITING_TEXT}</span></span>}
          {!waiting && <TraceSteps steps={hidden > 0 ? all.slice(hidden) : all} running live landing={landing} reducedMotion={reduced} hold={hold} idPrefix={idPrefix} />}
        </div>
      </div>
    );
  }

  return (
    <div className="steps-inline">
      <button type="button" className="steps-toggle" data-help="msg.steps" aria-expanded={open} aria-controls={bodyId} onClick={() => setChosen(!open)}>
        <span className="steps-toggle-label">{stepsLabel(steps.length)}</span>
        <span className="steps-toggle-chevron" aria-hidden="true">▸</span>
      </button>
      {open && (
        <div className="steps-inline-body" id={bodyId}>
          <TraceSteps steps={steps} running={false} idPrefix={idPrefix} />
        </div>
      )}
    </div>
  );
};

export default StepsDisclosure;

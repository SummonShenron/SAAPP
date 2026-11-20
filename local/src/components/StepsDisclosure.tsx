import React, { useEffect, useState } from 'react';
import TraceSteps, { type TraceStep } from './TraceSteps';
import ThinkingLogo from './ThinkingLogo';
import { useReducedMotion } from './useReducedMotion';
import { useStepsGraph } from './useStepsGraph';
import { ARRIVE_DELAY_MS, LAND_END_AT, START_SPACE_PX, firstStepLanded, nowMs, paceFor, scaled } from './graphLoader';
import { stepsAreOpen, stepsLabel } from './traceFacts';
import { defaultStepsOpen, readStepsPreference, writeStepsPreference } from './stepsPreference';
import StepIcon from './StepIcon';
import { KIND_NAME, kindSummary } from './stepKinds';
import './__styles__/TracePanel.css';

interface Props {
  steps: TraceStep[];
  /** True while the turn is running: before the first step the vertical start graph, then the steps as they come with the same graph under the newest. */
  running: boolean;
  /** Keeps the ids of two lists that show the same steps (this one and the panel's) apart. */
  idPrefix: string;
  /** The newest reply: its steps stay open after the turn ends, until the next request starts (then they fold, unless the person opened or closed them). */
  latest?: boolean;
  /** Draw as if the person had asked for reduced motion (the dev preview and tests; the app leaves it to the system setting). */
  reducedMotion?: boolean;
  /** Keep a landing on screen instead of letting it finish (the dev preview only). */
  hold?: boolean;
}

/** While a turn runs, only the newest steps are drawn; the rest are one press away. */
export const LIVE_STEPS_SHOWN = 5;
const WAITING_TEXT = 'Thinking...';
/** The animated logo's height before the first step: it fits the 66px box the wait keeps (TracePanel.css), with room to crouch and spring. */
const THINKING_LOGO_PX = 36;

/**
 * The line that decides what a turn's steps show: the number of steps, an icon for each step (they come in one by one as the steps are taken, and the newest keeps moving while it works), and the little arrow.
 * The arrow is the only switch: open, the steps are drawn as nodes under it; closed, there is just this line.
 */
const StepsLine: React.FC<{ steps: TraceStep[]; open: boolean; controls: string; onToggle: () => void; live?: boolean }> = ({ steps, open, controls, onToggle, live = false }) => {
  const kinds = kindSummary(steps);
  const shown = steps.filter(step => !step.turnEnd);          // the same list kindSummary draws an icon for, so icon i is step i
  return (
    <button type="button" className="steps-toggle" data-help="msg.steps" aria-expanded={open} aria-controls={controls} onClick={onToggle}>
      <span className="steps-toggle-label">{stepsLabel(steps.length)}</span>
      {/* what the steps did, at a glance: the kind of each, in order (decoration: the count says it in words) */}
      {kinds.kinds.length > 0 && (
        <span className={`steps-kinds${live ? ' steps-kinds-live' : ''}`} aria-hidden="true">
          {kinds.kinds.map((kind, i) => <span key={i} className="steps-kind" title={KIND_NAME[kind]}><StepIcon kind={kind} still={!(live && shown[i]?.status === 'active')} /></span>)}
          {kinds.more > 0 && <span className="steps-kinds-more">+{kinds.more}</span>}
        </span>
      )}
      <span className="steps-toggle-chevron" aria-hidden="true">▸</span>
    </button>
  );
};

/**
 * The steps of one turn. Before the first step there is the animated logo beside "Thinking...". From the first step on there is the steps line (StepsLine) above the reply, while the turn runs as well as after it, and
 * its arrow alone decides what you see: open (the newest reply's steps start open), the steps are drawn as nodes, each keeping its own words (its title and what it is doing underneath), with a graph that
 * runs in the gap until the next step lands (components/graphLoader.ts); closed, the line (its icons coming in as the steps are taken) with the animated logo under it the whole time the turn runs. What you chose carries on when the turn ends, when the nodes settle
 * into the same list the trace panel shows (components/TraceSteps.tsx), open for the newest reply until the next request starts, then folded. Kept in the page for as long as it is open; the read excerpts are never
 * stored, so a reloaded conversation has none of this.
 */
const StepsDisclosure: React.FC<Props> = ({ steps, running, idPrefix, latest = false, reducedMotion, hold = false }) => {
  const [chosen, setChosen] = useState<boolean | null>(null);        // what the person pressed on THIS turn's line; null until they do
  // What they last chose on the current turn's line (stepsPreference.ts) is how every later turn starts: collapse the steps once and the next messages stay collapsed. A running turn is the newest reply, so its steps start open
  // like that reply's, unless they were collapsed.
  const [preference] = useState(readStepsPreference);
  const open = stepsAreOpen(chosen, defaultStepsOpen(running || latest, preference));
  // Pressing the line decides this turn, and, if it is the current turn's (the running one, or the newest reply), the turns after it too.
  const toggle = () => {
    setChosen(!open);
    if (running || latest) writeStepsPreference(!open);
  };
  const [showAll, setShowAll] = useState(false);
  const bodyId = `${idPrefix}-steps`;
  const reduced = useReducedMotion(reducedMotion);
  // Graph mode (stepsStyle.ts): the graph under each step stays and the text sits to the right of it. It needs the motion the landing has, so reduced motion keeps the plain line.
  const graph = useStepsGraph() && !reduced;

  // The first step has landed: the start graph runs on into its node. Worked out DURING the render that first has a step (React lets a component set its own state while rendering and renders again before anything
  // is drawn), so there is no frame with the step and no landing. Reduced motion skips it: the step simply appears.
  const [seen, setSeen] = useState(steps.length);
  const [landing, setLanding] = useState(false);
  // A first step that comes soon after the graph appeared lands faster (graphLoader.ts paceFor), so the light still plays out.
  const [shownAt, setShownAt] = useState(() => nowMs());
  const [pace, setPace] = useState(1);
  if (steps.length !== seen) {
    setSeen(steps.length);
    if (running && !reduced && firstStepLanded(seen, steps.length)) {
      setLanding(true);
      setPace(paceFor(nowMs() - shownAt));
    } else if (steps.length === 0) {
      setLanding(false);
      setShownAt(nowMs());          // a new wait begins: the graph is drawn afresh
    }
  }
  useEffect(() => {
    if (!landing || hold) return undefined;
    const timer = window.setTimeout(() => setLanding(false), scaled(LAND_END_AT, pace));
    return () => window.clearTimeout(timer);
  }, [landing, hold, pace]);

  if (running) {
    const waiting = steps.length === 0;
    const all = steps;
    const hidden = showAll ? 0 : Math.max(0, all.length - LIVE_STEPS_SHOWN);
    // One wrapper in the same place whether the turn is waiting for its first step, landing it, or past it, so the logo is ONE instance all through (a different tree would restart it). With the steps closed there
    // are no nodes: the line, and under it the logo going the whole time the turn runs, with the step Sonic is on beside it.
    const body = waiting || open;
    const current = [...steps].reverse().find(step => step.status === 'active') ?? steps[steps.length - 1];
    // The first step of a turn is nearly always the "think" step, whose icon is a sparkle: then the loader does not fade, it turns INTO the sparkle, which travels to the step's dot and arrives as the dot comes in
    // (thinkingLogoBecome.ts). A first step with any other icon keeps the plain fade.
    const becomesSparkle = steps[0]?.kind === 'think';
    return (
      <div className={`steps-inline steps-inline-live${graph ? ' steps-graph' : ''}${waiting ? ' steps-inline-waiting' : ''}`} id={bodyId} role={waiting ? 'status' : undefined}>
        {!waiting && <StepsLine steps={steps} open={open} controls={bodyId} onToggle={toggle} live />}
        {!waiting && open && hidden > 0 && (
          <button type="button" className="steps-toggle steps-earlier" onClick={() => setShowAll(true)}>
            <span className="steps-toggle-label">{stepsLabel(hidden)} before this</span>
            <span className="steps-toggle-chevron" aria-hidden="true">▸</span>
          </button>
        )}
        {!waiting && !open && (
          <div className="gl-startwrap gl-start-wait" role="status">
            <ThinkingLogo size={THINKING_LOGO_PX} reducedMotion={reduced} />
            <span className="agent-status-text"><span className="steps-first-title">{current?.title || WAITING_TEXT}</span></span>
          </div>
        )}
        {body && (
          <div className={`gl-startwrap${waiting ? ' gl-start-wait' : landing ? '' : ' gl-start-settled'}`} style={landing ? { paddingTop: START_SPACE_PX } : undefined}>
            {(waiting || landing) && <ThinkingLogo size={THINKING_LOGO_PX} leaving={landing && !becomesSparkle} becomeMs={landing && becomesSparkle ? scaled(ARRIVE_DELAY_MS, pace) : undefined} reducedMotion={reduced} />}
            {(waiting || landing) && <span className={`agent-status-text${landing ? ' gl-start-text gl-start-text-logo gl-start-text-out' : ''}`}><span className="steps-first-title">{WAITING_TEXT}</span></span>}
            {!waiting && <TraceSteps steps={hidden > 0 ? all.slice(hidden) : all} running live landing={landing} landingPace={pace} graph={graph} reducedMotion={reduced} hold={hold} idPrefix={idPrefix} />}
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="steps-inline">
      <StepsLine steps={steps} open={open} controls={bodyId} onToggle={toggle} />
      {open && (
        <div className={`steps-inline-body${graph ? ' steps-inline-live steps-inline-settled steps-graph' : ''}`} id={bodyId}>
          <TraceSteps steps={steps} running={false} settled={graph} idPrefix={idPrefix} />
        </div>
      )}
    </div>
  );
};

export default StepsDisclosure;

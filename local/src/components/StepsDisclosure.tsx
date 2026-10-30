import React, { useState } from 'react';
import TraceSteps, { type TraceStep } from './TraceSteps';
import { stepWords, stepsAreOpen, stepsLabel } from './traceFacts';
import './__styles__/TracePanel.css';

interface Props {
  steps: TraceStep[];
  /** True while the turn is running: the steps are shown as they come, the newest one is the loader, and the last node's dashed stub flows on. The first step is the full-size ring; nodes begin at the second. */
  running: boolean;
  /** Keeps the ids of two lists that show the same steps (this one and the panel's) apart. */
  idPrefix: string;
  /** The newest reply: its steps stay open after the turn ends, until the next request starts (then they fold, unless the person opened or closed them). */
  latest?: boolean;
}

/** While a turn runs, only the newest steps are drawn; the rest are one press away. */
export const LIVE_STEPS_SHOWN = 5;
const WAITING_TEXT = 'Thinking...';

/**
 * The steps of one turn. While the turn runs there is no line to open: the steps are right there as nodes, each keeping its own words (its title and what it is doing underneath),
 * and the newest node is the loader, the Sonic ring in the dot's place, so the animation is simply the step Sonic is on. Anything a step measured or read opens under that step.
 * When the turn ends they settle into a "7 steps" line above the reply and stay there: open for the newest reply until the next request starts, then folded, opening to the same steps the
 * trace panel shows (components/TraceSteps.tsx). Kept in the page for as long as it is open; the read excerpts are never stored, so a reloaded conversation has none of this.
 */
const StepsDisclosure: React.FC<Props> = ({ steps, running, idPrefix, latest = false }) => {
  const [chosen, setChosen] = useState<boolean | null>(null);        // what the person pressed; null until they do
  const open = stepsAreOpen(chosen, latest);
  const [showAll, setShowAll] = useState(false);
  const bodyId = `${idPrefix}-steps`;

  if (running) {
    if (steps.length === 0 || (steps.length === 1 && steps[0].status !== 'complete')) {
      // The first step is the full-size ring and its words, the same as the one-line loader, so the start of a turn looks the same however steps are shown (and "Thinking..." before the first step has
      // arrived). The ring shrinks to the small one on its node only when the second step arrives and the first becomes a finished node.
      const first = steps.length === 1 ? stepWords(steps[0]) : { title: WAITING_TEXT, detail: '' };
      return (
        <div className="steps-inline steps-inline-live steps-inline-waiting" id={bodyId} role="status">
          <div className="sonic-loader-container">
            <div className="sonic-ring-loader" aria-hidden="true" />
            <span className="agent-status-text">
              <span className="steps-first-title">{first.title}</span>
              {first.detail && <span className="steps-first-detail">{first.detail}</span>}
            </span>
          </div>
        </div>
      );
    }
    const all = steps;
    const hidden = showAll ? 0 : Math.max(0, all.length - LIVE_STEPS_SHOWN);
    return (
      <div className="steps-inline steps-inline-live" id={bodyId}>
        {hidden > 0 && (
          <button type="button" className="steps-toggle steps-earlier" onClick={() => setShowAll(true)}>
            <span className="steps-toggle-label">{stepsLabel(hidden)} before this</span>
            <span className="steps-toggle-chevron" aria-hidden="true">▸</span>
          </button>
        )}
        <TraceSteps steps={hidden > 0 ? all.slice(hidden) : all} running spinner idPrefix={idPrefix} />
      </div>
    );
  }

  return (
    <div className="steps-inline">
      <button type="button" className="steps-toggle" aria-expanded={open} aria-controls={bodyId} onClick={() => setChosen(!open)}>
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

import React, { useEffect, useRef } from 'react';
import Icon from './icons';
import TraceSteps from './TraceSteps';
import './__styles__/TracePanel.css';

export type { TraceStep } from './TraceSteps';
import type { TraceStep } from './TraceSteps';

/**
 * The execution trace as a panel: now only the embedded widget's drawer (the chat page draws the same steps in the conversation, on a phone as on a desktop). It says what Sonic
 * is doing, step by step, while a request runs, and keeps the last request's steps after it. It follows
 * the newest step as they arrive. Each step is a button: press it (or Enter / Space) to see what the step measured. The steps themselves are drawn by
 * TraceSteps, which the steps kept under each reply share.
 */
const TracePanel: React.FC<{ steps: TraceStep[]; running: boolean; onClose: () => void }> = ({ steps, running, onClose }) => {
  const body = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (body.current) body.current.scrollTop = body.current.scrollHeight;
  }, [steps.length]);

  const status = steps.length === 0 ? 'Waiting for the first execution step…' : running ? 'Tracing request…' : 'Last request';

  return (
    <>
      <div className="trace-panel-head">
        <div>
          <h2 className="trace-panel-title">How it works</h2>
          <div className="trace-panel-sub">Live execution trace</div>
        </div>
        <button type="button" className="trace-panel-close" onClick={onClose} aria-label="Hide execution trace" title="Hide execution trace">
          <Icon name="close" size={18} />
        </button>
      </div>
      <div className="trace-panel-body" ref={body} aria-live="polite">
        <div className="trace-status">{status}</div>
        {steps.length === 0 && <div className="trace-empty">Start a request and the route it takes will appear here.</div>}
        <TraceSteps key={steps.length === 0 ? 'none' : 'steps'} steps={steps} running={running} />
      </div>
    </>
  );
};

export default TracePanel;

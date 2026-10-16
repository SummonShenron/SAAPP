import React, { useEffect, useRef } from 'react';
import Icon from './icons';
import './__styles__/TracePanel.css';

export interface TraceStep {
  id: string;
  title: string;
  detail: string;
  status: 'active' | 'complete';
  batchIndex?: number;
  batchSize?: number;
}

/**
 * The execution trace, as the panel the chat page shows: a blade down the right side on a wide screen, a bottom sheet on a phone (the page decides which;
 * the content is the same). It says what Sonic is doing, step by step, while a request runs, and keeps the last request's steps after it. It follows
 * the newest step as they arrive.
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
        {/* The steps are nodes on one path: each is a dot on a line, the line behind it is drawn in the accent (the way already travelled), and while a
            request runs a dashed stub flows on from the last node toward whatever comes next. */}
        {steps.length > 0 && (
          <ol className="trace-path">
            {steps.map((step, i) => {
              const done = step.status === 'complete';
              const last = i === steps.length - 1;
              return (
                <li
                  key={step.id}
                  className={`trace-node ${done ? 'trace-node-done' : 'trace-node-active'}${last ? ' trace-node-last' : ''}${last && running ? ' trace-node-running' : ''}`}
                >
                  <span className="trace-node-dot" aria-hidden="true">
                    {done ? <Icon name="check" size={11} /> : <span className="trace-node-core" />}
                  </span>
                  <div className="trace-node-text">
                    <div className="trace-node-title">
                      {step.title}
                      {step.batchSize && step.batchSize > 1 && (
                        <span className="trace-batch">batched {step.batchIndex}/{step.batchSize}</span>
                      )}
                    </div>
                    {step.detail && <div className="trace-node-detail">{step.detail}</div>}
                  </div>
                </li>
              );
            })}
          </ol>
        )}
      </div>
    </>
  );
};

export default TracePanel;

import React, { useEffect, useRef, useState } from 'react';
import Icon from './icons';
import { searchSummary, splitAroundTerm, toggleExpanded, type SearchExcerpt, type TraceExcerpt } from './traceFacts';
import './__styles__/TracePanel.css';

export interface TraceStep {
  id: string;
  /** The server's id for this step; the facts event that follows it carries the same one. */
  stepId?: string;
  title: string;
  detail: string;
  status: 'active' | 'complete';
  batchIndex?: number;
  batchSize?: number;
  /** Measured values about what the step did (never content), shown when the step is expanded. */
  facts?: string[];
  /** A capped, redacted excerpt of what a read step read (the live trace only: it is not stored). Shown as plain text, never HTML. */
  excerpt?: TraceExcerpt;
}

/**
 * The execution trace, as the panel the chat page shows: a blade down the right side on a wide screen, a bottom sheet on a phone (the page decides which;
 * the content is the same). It says what Sonic is doing, step by step, while a request runs, and keeps the last request's steps after it. It follows
 * the newest step as they arrive. Each step is a button: press it (or Enter / Space) to see what the step measured.
 */
/** What a search found, as plain text: a summary line, then each file with its matching lines and line numbers, the searched text marked. Nothing here is ever treated as HTML. */
function SearchResults({ excerpt }: { excerpt: SearchExcerpt }) {
  return (
    <div className="trace-excerpt" aria-label="What this step found">
      <div className="trace-excerpt-summary">{searchSummary(excerpt)}</div>
      <div className="trace-search-list">
        {excerpt.files.map(file => (
          <div className="trace-search-file" key={file.path}>
            <div className="trace-excerpt-path">{file.path}</div>
            {file.matches.map(match => (
              <div className="trace-search-line" key={match.line}>
                <span className="trace-search-number">{match.line}</span>
                <span className="trace-search-text">
                  {splitAroundTerm(match.text, excerpt.term).map((part, index) => (part.hit ? <mark key={index} className="trace-search-hit">{part.text}</mark> : <span key={index}>{part.text}</span>))}
                </span>
              </div>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

const TracePanel: React.FC<{ steps: TraceStep[]; running: boolean; onClose: () => void }> = ({ steps, running, onClose }) => {
  const body = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState<ReadonlySet<string>>(new Set());
  useEffect(() => {
    if (body.current) body.current.scrollTop = body.current.scrollHeight;
  }, [steps.length]);
  // A new request starts a fresh list of steps; the old expansions mean nothing then.
  useEffect(() => {
    if (steps.length === 0) setOpen(new Set());
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
              const hasFacts = !!step.facts && step.facts.length > 0;
              const excerpt = step.excerpt;
              const hasExcerpt = !!excerpt && (excerpt.kind === 'search' ? excerpt.files.length > 0 : excerpt.text.length > 0);
              const hasDetails = hasFacts || hasExcerpt;
              const expanded = hasDetails && open.has(step.id);
              const panelId = `trace-facts-${step.id}`;
              // A step with no friendly title ("Processing step: ...") shows what it is doing as the title instead, with no separate detail line.
              const untitled = step.title.startsWith('Processing step:') && !!step.detail;
              const title = untitled ? step.detail : step.title;
              const main = (
                <span className="trace-node-main">
                  <span className="trace-node-title">
                    {title}
                    {step.batchSize && step.batchSize > 1 && (
                      <span className="trace-batch">batched {step.batchIndex}/{step.batchSize}</span>
                    )}
                  </span>
                  {step.detail && !untitled && <span className="trace-node-detail">{step.detail}</span>}
                </span>
              );
              return (
                <li
                  key={step.id}
                  className={`trace-node ${done ? 'trace-node-done' : 'trace-node-active'}${last ? ' trace-node-last' : ''}${last && running ? ' trace-node-running' : ''}${expanded ? ' trace-node-open' : ''}`}
                >
                  <span className="trace-node-dot" aria-hidden="true">
                    {done ? <Icon name="check" size={11} /> : <span className="trace-node-core" />}
                  </span>
                  <div className="trace-node-text">
                    {hasDetails ? (
                      <button
                        type="button"
                        className="trace-node-toggle"
                        aria-expanded={expanded}
                        aria-controls={panelId}
                        onClick={() => setOpen(prev => toggleExpanded(prev, step.id))}
                      >
                        {main}
                        <span className="trace-node-chevron" aria-hidden="true">▸</span>
                      </button>
                    ) : (
                      <div className="trace-node-plain">{main}</div>
                    )}
                    {hasDetails && expanded && (
                      <div className="trace-node-facts" id={panelId}>
                        {hasFacts && (
                        <ul className="trace-facts-list">
                          {step.facts!.map((fact, index) => (
                            <li key={index}>{fact}</li>
                          ))}
                        </ul>
                        )}
                        {hasExcerpt && excerpt && excerpt.kind === 'read' && (
                          <div className="trace-excerpt" aria-label="What this step read">
                            {excerpt.path && <div className="trace-excerpt-path">{excerpt.path}</div>}
                            <pre className="trace-excerpt-text">{excerpt.text}</pre>
                            {excerpt.truncated && <div className="trace-excerpt-note">Only the start is shown.</div>}
                          </div>
                        )}
                        {hasExcerpt && excerpt && excerpt.kind === 'search' && <SearchResults excerpt={excerpt} />}
                      </div>
                    )}
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

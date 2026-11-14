import React, { useEffect, useState } from 'react';
import Icon from './icons';
import { PagedFork } from './LoaderGraphs';
import { useReducedMotion } from './useReducedMotion';
import { MERGE_END_AT, arrivalOf } from './graphLoader';
import { endingWords, finalStepLabel, searchSummary, splitAroundTerm, stepWords, toggleExpanded, type LastStepStatus, type SearchExcerpt, type TraceExcerpt, type TurnEnd } from './traceFacts';
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
  /** Set on the steps of the last tool turn this request can take, with what the work looked like by then (the header above them says it). */
  lastStep?: LastStepStatus;
  /** Set on the step that says how a turn that used tools ended: it chose to answer, it ran out of steps while still finding things, it ran out going in circles, or it repeated itself. */
  turnEnd?: TurnEnd;
  /** Measured values about what the step did (never content), shown when the step is expanded. */
  facts?: string[];
  /** A capped, redacted excerpt of what a read step read (the live trace only: it is not stored). Shown as plain text, never HTML. */
  excerpt?: TraceExcerpt;
}

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

/**
 * The steps of one request, as nodes on one path: each is a dot on a line, the line behind it is drawn in the accent (the way already travelled), and while a request runs a
 * dashed stub flows on from the last node toward whatever comes next. Each step with something to show is a button: press it (or Enter / Space) to see what the step measured and read.
 * Shared by the trace panel (the live request) and the steps kept under each reply (components/StepsDisclosure.tsx), so both show exactly the same thing.
 *
 * With `live` (the steps in the conversation while a turn runs) the newest step is also the loader: a fork hangs under it (components/LoaderGraphs.tsx, rules in graphLoader.ts), and when the next step lands the
 * fork is held, merges into it and fades, and the space it held closes. `landing`: this is the first step, and the vertical start graph above it is running on into its node.
 */
const TraceSteps: React.FC<{ steps: TraceStep[]; running: boolean; idPrefix?: string; live?: boolean; landing?: boolean; reducedMotion?: boolean; hold?: boolean }> = ({ steps, running, idPrefix = 'trace', live = false, landing = false, reducedMotion, hold = false }) => {
  // Which steps are open. A new request starts a fresh list, so a caller that reuses this for the next request gives it a new key (the old expansions mean nothing then).
  const [open, setOpen] = useState<ReadonlySet<string>>(new Set());
  const reduced = useReducedMotion(reducedMotion);
  // Which step's fork is merging into the one that has just landed. Worked out DURING the render that first has the new step (React lets a component set its own state while rendering and renders again before
  // anything is drawn): set in an effect it would draw one frame with the new step and no merge, which destroyed the fork and rebuilt it a moment later (a flicker the mock-up caught). It goes by ids, not by a
  // count, because only the newest few steps are drawn while a turn runs.
  const lastId = steps.length > 0 ? steps[steps.length - 1].id : null;
  const [lastSeen, setLastSeen] = useState<string | null>(lastId);
  const [merging, setMerging] = useState<string | null>(null);
  if (lastId !== lastSeen) {
    setLastSeen(lastId);
    const arrival = live && running && !reduced ? arrivalOf(lastSeen, steps) : null;
    if (arrival) setMerging(arrival.mergeId);
  }
  useEffect(() => {
    if (merging === null || hold) return undefined;
    const timer = window.setTimeout(() => setMerging(null), MERGE_END_AT);
    return () => window.clearTimeout(timer);
  }, [merging, hold]);

  if (steps.length === 0) return null;
  return (
    <ol className="trace-path">
      {steps.map((step, i) => {
        const done = step.status === 'complete';
        const last = i === steps.length - 1;
        const hasFacts = !!step.facts && step.facts.length > 0;
        const excerpt = step.excerpt;
        const hasExcerpt = !!excerpt && (excerpt.kind === 'search' ? excerpt.files.length > 0 : excerpt.kind === 'list' ? excerpt.lines.length > 0 : excerpt.kind === 'count' ? excerpt.count > 0 : excerpt.text.length > 0);
        const hasDetails = hasFacts || hasExcerpt;
        const expanded = hasDetails && open.has(step.id);
        const panelId = `${idPrefix}-facts-${step.id}`;
        const words = step.turnEnd ? endingWords(step.detail) : stepWords(step);
        const finalLabel = step.lastStep && !(i > 0 && steps[i - 1].lastStep) ? finalStepLabel(step.lastStep) : '';       // one header above the first step of the last turn
        const main = (
          <span className="trace-node-main">
            <span className="trace-node-title">
              {words.title}
              {step.batchSize && step.batchSize > 1 && (
                <span className="trace-batch">batched {step.batchIndex}/{step.batchSize}</span>
              )}
            </span>
            {words.detail && <span className="trace-node-detail">{words.detail}</span>}
          </span>
        );
        // In the steps under a running reply the newest node IS the loader: a fork hangs under it until the next step lands (the trace panel keeps its pulsing dot). The first step waits for the start graph to finish landing.
        const loaderNode = live && running && last && !done;
        const forking = live && ((loaderNode && merging === null && !landing) || merging === step.id);
        const arriving = live && ((merging !== null && last) || (landing && i === 0));
        return (
          <li
            key={step.id}
            className={`trace-node ${done ? 'trace-node-done' : 'trace-node-active'}${last ? ' trace-node-last' : ''}${step.turnEnd ? ` trace-node-end trace-end-${step.turnEnd}` : ''}${(last && running) || merging === step.id ? ' trace-node-running' : ''}${expanded ? ' trace-node-open' : ''}${forking ? ' trace-node-forking' : ''}${merging === step.id ? ' trace-node-merging' : ''}${arriving ? ' trace-node-arriving' : ''}`}
          >
            <span className="trace-node-dot" aria-hidden="true">
              {done ? <Icon name="check" size={11} /> : <span className="trace-node-core" />}
            </span>
            <div className="trace-node-text">
              {finalLabel && <div className="trace-final-header" data-status={step.lastStep}>{finalLabel}</div>}
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
                  {hasExcerpt && excerpt && excerpt.kind === 'list' && (
                    <div className="trace-excerpt" aria-label="What this step found">
                      <div className="trace-excerpt-summary">{excerpt.total_lines} {excerpt.total_lines === 1 ? 'result' : 'results'}{excerpt.truncated ? ' (showing the first ones)' : ''}</div>
                      <div className="trace-search-list">
                        {excerpt.lines.map((line, index) => <div className="trace-search-line" key={index}><span className="trace-search-text">{line}</span></div>)}
                      </div>
                    </div>
                  )}
                  {hasExcerpt && excerpt && excerpt.kind === 'count' && (
                    <div className="trace-excerpt" aria-label="What this step found">
                      <div className="trace-excerpt-summary">{excerpt.count} remembered {excerpt.count === 1 ? 'item' : 'items'} found</div>
                    </div>
                  )}
                </div>
              )}
            </div>
            {forking && <span className="gl-below" aria-hidden="true"><PagedFork merging={merging === step.id} reducedMotion={reduced} hold={hold} /></span>}
          </li>
        );
      })}
    </ol>
  );
};

export default TraceSteps;

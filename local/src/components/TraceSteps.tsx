import React, { useEffect, useRef, useState } from 'react';
import Icon from './icons';
import { StartGraph } from './LoaderGraphs';
import StepIcon from './StepIcon';
import type { StepKind } from './stepKinds';
import { useReducedMotion } from './useReducedMotion';
import { ARRIVE_ANIM_MS, ARRIVE_DELAY_MS, ARRIVE_STAGGER_MS, LAND_END_AT, arrivalsOf, keptScale, nowMs, paceFor, scaled, without } from './graphLoader';
import { endingWords, finalStepLabel, searchSummary, splitAroundTerm, stepFailed, stepWords, toggleExpanded, type LastStepStatus, type SearchExcerpt, type TraceExcerpt, type TurnEnd } from './traceFacts';
import './__styles__/TracePanel.css';

export interface TraceStep {
  id: string;
  /** The server's id for this step; the facts event that follows it carries the same one. */
  stepId?: string;
  /** What kind of step it is, from the tool and node the server named (stepKinds.ts): which little icon its dot is while it runs. A step with none (or one the page does not know) gets the general icon. */
  kind?: StepKind;
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
 * With `live` (the steps in the conversation while a turn runs) the newest step is also the loader: a vertical graph hangs under it, in the gap before the next step (components/LoaderGraphs.tsx, rules in
 * graphLoader.ts), and when the next step lands the light carries on to the graph's last node, which is that step's dot; the graph fades and the space it held closes. `landing`: this is the first step, and the
 * vertical start graph above it is running on into its node.
 */
/** A step that has just arrived: how fast its fade-in plays (graphLoader.ts paceFor) and its place among the steps that came with it. */
interface Arrive { pace: number; order: number }

const TraceSteps: React.FC<{ steps: TraceStep[]; running: boolean; idPrefix?: string; live?: boolean; landing?: boolean; landingPace?: number; graph?: boolean; settled?: boolean; reducedMotion?: boolean; hold?: boolean }> = ({ steps, running, idPrefix = 'trace', live = false, landing = false, landingPace = 1, graph = false, settled = false, reducedMotion, hold = false }) => {
  // Which steps are open. A new request starts a fresh list, so a caller that reuses this for the next request gives it a new key (the old expansions mean nothing then).
  const [open, setOpen] = useState<ReadonlySet<string>>(new Set());
  const reduced = useReducedMotion(reducedMotion);
  // Which steps have just arrived (`arrivals`) and which step's graph is landing in them (`landings`, by the id of the step that held the graph, with the pace it lands at). Worked out DURING the render that first has the new
  // step (React lets a component set its own state while rendering and renders again before anything is drawn): set in an effect it would draw one frame with the new step and no landing, which destroyed the graph and
  // rebuilt it a moment later (a flicker the mock-up caught). It goes by ids, not by a count, because only the newest few steps are drawn while a turn runs.
  // Several can be going at once: a step that arrives while the one before it is still landing does NOT cut that landing short. Each runs to its own end, and is played faster the sooner the step followed the last one.
  const lastId = steps.length > 0 ? steps[steps.length - 1].id : null;
  const [lastSeen, setLastSeen] = useState<string | null>(lastId);
  const [lastArrivalAt, setLastArrivalAt] = useState<number | null>(() => (landing ? nowMs() : null));
  const [landings, setLandings] = useState<Record<string, number>>({});
  const [grayed, setGrayed] = useState<Record<string, true>>({});          // graph mode: the kept graphs that have finished landing and have turned gray
  const [arrivals, setArrivals] = useState<Record<string, Arrive>>(() => (landing ? Object.fromEntries(steps.map((s, order) => [s.id, { pace: landingPace, order }])) : {}));
  if (lastId !== lastSeen) {
    setLastSeen(lastId);
    if (live && running && !reduced) {
      const { holderId, newIds } = arrivalsOf(lastSeen, steps);
      if (newIds.length > 0) {
        const now = nowMs();
        const pace = paceFor(lastArrivalAt === null ? null : now - lastArrivalAt);
        setLastArrivalAt(now);
        if (holderId !== null) setLandings(prev => ({ ...prev, [holderId]: pace }));
        setArrivals(prev => ({ ...prev, ...Object.fromEntries(newIds.map((id, order) => [id, { pace, order }])) }));
      }
    }
  }
  // Each one is cleared by its own timer, set once; they are only cleared together when this goes away.
  const timers = useRef<Map<string, number>>(new Map());
  useEffect(() => {
    if (hold) return;
    const once = (key: string, ms: number, clear: () => void) => {
      if (timers.current.has(key)) return;
      timers.current.set(key, window.setTimeout(() => { timers.current.delete(key); clear(); }, ms));
    };
    // In graph mode a landing is never cleared: the graph stays where it landed (and the row keeps its room) for as long as this list is on screen.
    if (!graph) Object.entries(landings).forEach(([id, pace]) => once(`l:${id}`, scaled(LAND_END_AT, pace), () => setLandings(prev => without(prev, id))));
    else Object.entries(landings).forEach(([id, pace]) => { if (!grayed[id]) once(`g:${id}`, scaled(LAND_END_AT, pace), () => setGrayed(prev => (prev[id] ? prev : { ...prev, [id]: true }))); });          // then it turns gray, like a finished step's icon
    Object.entries(arrivals).forEach(([id, a]) => once(`a:${id}`, scaled(LAND_END_AT, a.pace) + a.order * scaled(ARRIVE_STAGGER_MS, a.pace), () => setArrivals(prev => without(prev, id))));
  }, [landings, arrivals, grayed, hold, graph]);
  useEffect(() => {
    const held = timers.current;
    return () => { held.forEach(timer => window.clearTimeout(timer)); held.clear(); };
  }, []);

  if (steps.length === 0) return null;
  return (
    <ol className="trace-path">
      {steps.map((step, i) => {
        const done = step.status === 'complete';
        // A tool step whose result was an error says so on the row itself (its facts, behind the arrow, give the reason), so a step that did not work no longer looks like one that did.
        const failed = done && stepFailed(step.facts);
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
              {failed && <span className="trace-failed-chip">Failed</span>}
              {step.batchSize && step.batchSize > 1 && (
                <span className="trace-batch">batched {step.batchIndex}/{step.batchSize}</span>
              )}
            </span>
            {words.detail && <span className="trace-node-detail">{words.detail}</span>}
          </span>
        );
        // In the steps under a running reply the newest node IS the loader: the graph hangs under it until the next step lands (the trace panel keeps its pulsing dot). The first step waits for the start graph to finish landing.
        const loaderNode = live && running && last && !done;
        const kept = settled && !last;                           // a finished reply's list (graph mode): every step but the last has its graph under it, as it ended up
        const merging = kept || step.id in landings;             // the graph under this step is landing in the step after it (or has landed)
        const arrive = live ? arrivals[step.id] : undefined;     // this step has just arrived: its dot and words fade in
        const forking = kept || (live && ((loaderNode && !merging && !arrive) || merging));
        const arriving = arrive !== undefined;
        // In graph mode the graph under a step stays once it has landed; with two or more steps after it, it shrinks (same shape) and its row's room shrinks with it (graphLoader.ts keptScale).
        const keptSize = (graph || settled) && merging ? keptScale(steps.length - 1 - i) : 1;
        const compact = keptSize < 1;
        const gray = kept || (graph && grayed[step.id] === true);          // a finished reply's graphs are gray from the start; a running turn's turn gray once they have landed
        // How fast this step's fade-in plays and when it starts: the landing's own timeline at its pace, a little later for each of the steps that came in the same burst.
        const arriveStyle = arrive ? ({ '--arrive-delay': `${scaled(ARRIVE_DELAY_MS, arrive.pace) + arrive.order * scaled(ARRIVE_STAGGER_MS, arrive.pace)}ms`, '--arrive-dur': `${scaled(ARRIVE_ANIM_MS, arrive.pace)}ms` } as React.CSSProperties) : undefined;
        return (
          <li
            key={step.id}
            style={arriveStyle}
            className={`trace-node ${done ? 'trace-node-done' : 'trace-node-active'}${failed ? ' trace-node-failed' : ''}${last ? ' trace-node-last' : ''}${step.turnEnd ? ` trace-node-end trace-end-${step.turnEnd}` : ''}${(last && running) || merging ? ' trace-node-running' : ''}${expanded ? ' trace-node-open' : ''}${forking ? ' trace-node-forking' : ''}${merging ? ' trace-node-merging' : ''}${compact ? ' trace-node-compact' : ''}${gray ? ' trace-node-grayed' : ''}${arriving ? ' trace-node-arriving' : ''}`}
          >
            <span className={`trace-node-dot${failed ? '' : ' trace-node-dot-icon'}`} aria-hidden="true">
              {/* A step's dot is a little icon of what it is doing (stepKinds.ts, StepIcon.tsx): in the accent and moving while it runs, the same icon in gray and standing once it is done. A step whose tool failed is a cross. */}
              {failed ? <Icon name="close" size={12} /> : <StepIcon kind={step.kind} still={done} />}
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
            {forking && <span className="gl-below" aria-hidden="true"><StartGraph stem landing={merging} pace={landings[step.id] ?? 1} kind={step.kind} seed={step.id} keep={graph || settled} settled={settled} grayed={gray} scale={keptSize} reducedMotion={reduced} hold={hold} /></span>}
          </li>
        );
      })}
    </ol>
  );
};

export default TraceSteps;

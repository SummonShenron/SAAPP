import React, { useEffect, useRef, useState } from 'react';
import Icon from './icons';
import type { RingView } from './budgetText';
import { Gauge, RepoFields, ScopeField, type RepoControls, type ScopeControls } from './SessionColumn';
import { LEVEL_COLOR, contextGauge, gaugeSummary, type ContextUse } from './sessionColumnLogic';
import './__styles__/SessionRail.css';

interface Props {
  /** The usage ring's view (components/budgetText.ts); null when there is no limit to show. */
  usage: RingView | null;
  /** How full the model's context window got on the last turn; null before the first turn. */
  context: ContextUse | null;
  /** The scope Sonic answers from; null when there are none to pick. */
  scope: ScopeControls | null;
  /** The target GitHub repo; null where it is not offered. */
  repo: RepoControls | null;
}

type Panel = 'repo' | 'gauges' | 'scope';

const MINI = 14;
const MINI_STROKE = 3;
const MINI_RADIUS = (MINI - MINI_STROKE) / 2;
const MINI_LENGTH = 2 * Math.PI * MINI_RADIUS;

/** A tiny ring: the share filled is how full the gauge is, in the colour of its level (the same colours as the column's gauges). */
function MiniRing({ fraction, level }: { fraction: number; level: string }) {
  return (
    <svg width={MINI} height={MINI} viewBox={`0 0 ${MINI} ${MINI}`} aria-hidden="true">
      <circle cx={MINI / 2} cy={MINI / 2} r={MINI_RADIUS} fill="none" stroke="var(--border-color)" strokeWidth={MINI_STROKE} className="rail-ring-track" />
      <circle
        cx={MINI / 2} cy={MINI / 2} r={MINI_RADIUS} fill="none" stroke={LEVEL_COLOR[level] ?? LEVEL_COLOR.ok} strokeWidth={MINI_STROKE} strokeLinecap="round"
        strokeDasharray={`${Math.min(1, Math.max(0, fraction)) * MINI_LENGTH} ${MINI_LENGTH}`} transform={`rotate(-90 ${MINI / 2} ${MINI / 2})`}
      />
    </svg>
  );
}

/**
 * The session column, shrunk to icons for a phone: a GitHub mark (the target repo), two small rings (the context and the usage gauges) and a lock (the scope), in a narrow strip at the right
 * edge. Pressing one opens a small card beside it with the same controls the column has (components/SessionColumn.tsx); pressing outside it, or Escape, closes it. The page's top-bar button hides
 * the whole strip. Nothing here is state of its own beyond which card is open.
 */
const SessionRail: React.FC<Props> = ({ usage, context, scope, repo }) => {
  const [open, setOpen] = useState<Panel | null>(null);
  const rail = useRef<HTMLElement>(null);
  const contextView = contextGauge(context);
  const hasGauges = !!(contextView || usage);

  useEffect(() => {
    if (!open) return undefined;
    const outside = (event: PointerEvent) => { if (rail.current && !rail.current.contains(event.target as Node)) setOpen(null); };
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') setOpen(null); };
    document.addEventListener('pointerdown', outside);
    document.addEventListener('keydown', escape);
    return () => { document.removeEventListener('pointerdown', outside); document.removeEventListener('keydown', escape); };
  }, [open]);

  if (!repo && !hasGauges && !scope) return null;
  const toggle = (panel: Panel) => setOpen(current => (current === panel ? null : panel));
  const scopeName = scope ? (scope.choices.find(choice => choice.value === scope.value)?.label ?? scope.value) : '';
  const summary = gaugeSummary(contextView, usage);

  return (
    <aside className="session-rail" aria-label="Session" ref={rail}>
      {repo && (
        <div className="rail-item">
          <button
            type="button" className={`rail-btn${open === 'repo' ? ' is-open' : ''}`} aria-expanded={open === 'repo'}
            aria-label={repo.value ? `GitHub: target repo ${repo.value}` : 'GitHub: no target repo set'} title={repo.value ? `Target repo: ${repo.value}` : 'Set a target GitHub repo'}
            onClick={() => toggle('repo')}
          >
            <Icon name="github" size={18} />
            {repo.value && <span className="rail-dot" aria-hidden="true" />}
          </button>
          {open === 'repo' && <div className="rail-pop" role="dialog" aria-label="GitHub"><RepoFields repo={repo} /></div>}
        </div>
      )}
      {hasGauges && (
        <div className="rail-item">
          <button
            type="button" className={`rail-btn rail-rings${open === 'gauges' ? ' is-open' : ''}`} aria-expanded={open === 'gauges'}
            aria-label={`Gauges: ${summary}`} title={summary} onClick={() => toggle('gauges')}
          >
            {contextView && <MiniRing fraction={contextView.fraction} level={contextView.level} />}
            {usage && <MiniRing fraction={usage.fraction} level={usage.level} />}
          </button>
          {open === 'gauges' && (
            <div className="rail-pop" role="dialog" aria-label="Gauges">
              {contextView && <Gauge name="Context" fraction={contextView.fraction} level={contextView.level} text={contextView.text} />}
              {usage && <Gauge name="Usage" fraction={usage.fraction} level={usage.level} text={usage.text} />}
            </div>
          )}
        </div>
      )}
      {scope && (
        <div className="rail-item">
          <button
            type="button" className={`rail-btn${open === 'scope' ? ' is-open' : ''}`} aria-expanded={open === 'scope'}
            aria-label={`Scope: ${scopeName}`} title={`Scope: ${scopeName}`} onClick={() => toggle('scope')}
          >
            <Icon name="lock" size={17} />
          </button>
          {open === 'scope' && <div className="rail-pop" role="dialog" aria-label="Scope"><ScopeField scope={scope} /></div>}
        </div>
      )}
    </aside>
  );
};

export default SessionRail;

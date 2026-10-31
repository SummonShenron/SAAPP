import React, { useEffect, useRef, useState } from 'react';
import Icon from './icons';
import type { RingView } from './budgetText';
import { ConnectionPanel, Gauge, RepoFields, ScopeField, type ConnectionProps, type RepoControls, type ScopeControls } from './SessionColumn';
import { ChipChoices, glyph, type ChatSettings, type ChipControl } from './ComposerChips';
import { chipLabel, isChanged, type ChipKey } from './composerChipsLogic';
import { LEVEL_COLOR, contextGauge, folderLine, gaugeSummary, runnerLine, type ContextUse } from './sessionColumnLogic';
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
  /** The runner on the person's computer and the folder: the column's Connection card, as the first button of the strip (a phone has the same account-level runner the desktop does). */
  connection?: ConnectionProps | null;
  /** The chat settings, one button each down the strip below the others (they are under the message box only where this strip is not showing). */
  settings?: ChatSettings | null;
}

type Panel = 'connection' | 'repo' | 'gauges' | 'scope' | ChipKey | 'help';

const CHIP_ORDER: ChipKey[] = ['model', 'mode', 'thinking', 'answers'];

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
const SessionRail: React.FC<Props> = ({ usage, context, scope, repo, settings, connection }) => {
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

  if (!repo && !hasGauges && !scope && !settings && !connection) return null;
  const toggle = (panel: Panel) => setOpen(current => (current === panel ? null : panel));
  const scopeName = scope ? (scope.choices.find(choice => choice.value === scope.value)?.label ?? scope.value) : '';
  const summary = gaugeSummary(contextView, usage);

  // The Connection button's dot: amber when the runner is not answering or the folder needs attention, green when either is connected and none is not, none when there is nothing connected.
  const connectionTone = (() => {
    if (!connection) return null;
    const tones = [connection.runnerEnabled ? runnerLine(connection.runners).tone : 'off', folderLine(connection.folder.status, connection.folder.name, connection.folder.fileCount).tone];
    return tones.includes('warn') ? 'warn' : tones.includes('ok') ? 'ok' : null;
  })();
  const controlOf = (key: ChipKey): ChipControl | null => (settings ? settings[key] : null);

  return (
    <aside className="session-rail" aria-label="Session" ref={rail}>
      {connection && (
        <div className="rail-item">
          <button
            type="button" className={`rail-btn${open === 'connection' ? ' is-open' : ''}`} aria-expanded={open === 'connection'}
            aria-label="Connection: your computer and folder" title="Your computer and folder" onClick={() => toggle('connection')}
          >
            <Icon name="monitor" size={18} />
            {connectionTone && <span className={`rail-dot rail-dot-${connectionTone}`} aria-hidden="true" />}
          </button>
          {open === 'connection' && <div className="rail-pop" role="dialog" aria-label="Connection"><ConnectionPanel {...connection} /></div>}
        </div>
      )}
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
      {settings && (
        <>
          <span className="rail-sep" aria-hidden="true" />
          {CHIP_ORDER.map(key => {
            const control = controlOf(key);
            if (!control) return null;
            const label = chipLabel(key, control.value);
            return (
              <div className="rail-item" key={key}>
                <button
                  type="button" className={`rail-btn${isChanged(key, control.value) ? ' is-changed' : ''}${open === key ? ' is-open' : ''}`} aria-haspopup="menu" aria-expanded={open === key}
                  aria-label={label} title={settings.disabled ? `${label} (locked while Sonic is working)` : label} disabled={settings.disabled} onClick={() => toggle(key)}
                >
                  {glyph(control.value)}
                </button>
                {open === key && <ChipChoices chipKey={key} control={control} onDone={() => setOpen(null)} className="rail-pop rail-choices" />}
              </div>
            );
          })}
          <div className="rail-item">
            <button type="button" className="rail-btn" aria-label="Export this conversation" title="Export this conversation" disabled={settings.exportDisabled} onClick={settings.onExport}>
              <Icon name="download" size={18} />
            </button>
          </div>
          <div className="rail-item">
            <button type="button" className={`rail-btn${open === 'help' ? ' is-open' : ''}`} aria-haspopup="dialog" aria-expanded={open === 'help'} aria-label="Help" title="Help" onClick={() => toggle('help')}>
              <Icon name="help" size={18} />
            </button>
            {open === 'help' && <div className="rail-pop rail-help" role="dialog" aria-label="Help">{settings.help}</div>}
          </div>
        </>
      )}
    </aside>
  );
};

export default SessionRail;

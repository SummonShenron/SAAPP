import React, { useEffect, useRef, useState } from 'react';
import Icon from './icons';
import type { RingView } from './budgetText';
import Ring from './Ring';
import { ConnectionPanel, Gauge, RepoFields, ScopeField, type ConnectionProps, type RepoControls, type ScopeControls } from './SessionColumn';
import { ChipChoices, ModelChoices, glyph, modelChip, type ChatSettings, type ChipControl } from './ComposerChips';
import { chipLabel, isChanged, type ChipKey } from './composerChipsLogic';
import { contextGauge, folderLine, gaugeSummary, runnerLine, type ContextUse } from './sessionColumnLogic';
import CompactionMeter from './CompactionMeter';
import { gaugesSummary, isOverloaded, meterCaption, meterName, meterTitle, type CompactionView } from './compactionMeterLogic';
import './__styles__/SessionRail.css';

interface Props {
  /** The usage ring's view (components/budgetText.ts); null when there is no limit to show. */
  usage: RingView | null;
  /** How full the model's context window got on the last turn; null before the first turn. Shown as the compaction meter's tooltip: the ring it used to be is gone. */
  context: ContextUse | null;
  /** The memory compaction meter's reading (notes waiting to be folded together, and the number at which they are); null before the first reading. */
  compaction: CompactionView | null;
  /** Switches the one ring between the memory reading and this conversation's; only given when both exist. */
  onSwitchMeter?: (() => void) | null;
  /** "Fold now" beside the Chat reading of the ring (see SessionColumn). */
  onFold?: (() => void) | null;
  folding?: boolean;
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

/** A tiny ring: the share filled is how full the gauge is, in the colour of its level (the same colours as the column's gauges). */
function MiniRing({ fraction, level }: { fraction: number; level: string }) {
  return <Ring fraction={fraction} level={level} size={14} stroke={3} percent={false} trackClass="rail-ring-track" />;
}

/**
 * The session column, shrunk to icons for a phone: a GitHub mark (the target repo), two small rings (the memory compaction meter and the usage gauge) and a lock (the scope), in a narrow strip at the right
 * edge. Pressing one opens a small card beside it with the same controls the column has (components/SessionColumn.tsx); pressing outside it, or Escape, closes it. The page's top-bar button hides
 * the whole strip. Nothing here is state of its own beyond which card is open.
 */
const SessionRail: React.FC<Props> = ({ usage, context, compaction, onSwitchMeter, onFold, folding, scope, repo, settings, connection }) => {
  const [open, setOpen] = useState<Panel | null>(null);
  const rail = useRef<HTMLElement>(null);
  const contextView = contextGauge(context);
  const hasGauges = !!(compaction || usage);

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
  const summary = gaugesSummary(compaction, usage);

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
            aria-label={`Gauges: ${summary}`} title={compaction ? meterTitle(compaction, contextView?.text) : summary} onClick={() => toggle('gauges')}
          >
            {compaction && <CompactionMeter view={compaction} contextText={contextView?.text} mini />}
            {usage && <MiniRing fraction={usage.fraction} level={usage.level} />}
          </button>
          {open === 'gauges' && (
            <div className="rail-pop" role="dialog" aria-label="Gauges">
              {compaction && (
                <div className="sc-gauge">
                  <CompactionMeter view={compaction} contextText={contextView?.text} />
                  <div className="sc-gauge-body">
                    {onSwitchMeter
                      ? <button type="button" className="sc-gauge-name sc-gauge-switch" onClick={onSwitchMeter} title="Switch between Sonic's memory and this conversation">{meterName(compaction)} ⇄</button>
                      : <span className="sc-gauge-name">{meterName(compaction)}</span>}
                    <span className={`sc-gauge-text${isOverloaded(compaction) ? ' ring-caption-overloaded' : ''}`}>{meterCaption(compaction)}</span>
                    {onFold && compaction.foldable && <button type="button" className="sc-gauge-fold" disabled={folding} onClick={onFold}>{folding ? 'Folding…' : 'Fold now'}</button>}
                  </div>
                </div>
              )}
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
            if (key === 'model') {
              // The model chip stands for the Base model and the Code model together, and opens both columns beside its button.
              const state = modelChip(settings.baseModel, settings.model);
              if (!state) return null;
              return (
                <div className="rail-item" key={key}>
                  <button
                    type="button" className={`rail-btn${state.changed ? ' is-changed' : ''}${open === key ? ' is-open' : ''}`} aria-haspopup="menu" aria-expanded={open === key}
                    aria-label={state.label} title={settings.disabled ? `${state.label} (locked while Sonic is working)` : state.label} disabled={settings.disabled} onClick={() => toggle(key)}
                  >
                    {glyph(state.value)}
                  </button>
                  {open === key && <ModelChoices base={settings.baseModel} code={settings.model} onDone={() => setOpen(null)} className="rail-pop rail-choices" />}
                </div>
              );
            }
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

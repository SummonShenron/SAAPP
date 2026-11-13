import React, { useEffect, useState } from 'react';
import { api, type CodePlan, type MemorySources, type RunnerInfo } from '../api';
import type { RingView } from './budgetText';
import Ring from './Ring';
import {
  APPROVAL_OPTIONS, GIT_OPTIONS, RUNNER_INSTALL_CMD, RUNNER_INSTALL_POWERSHELL, contextGauge, folderLight, folderLine, folderCard, gaugeSummary, memoryLine, parseCollapsed, planProgress, runnerLine, runnerProjectHint, runnerProjectOffer, serializeCollapsed, toggleCollapsed,
  type ContextUse, type Line, type Option, type RunnerProjectOffer, type RunnerStartPhase, type ScopeChoice, RUNNER_NUDGE_TEXT, RUNNER_NUDGE_TITLE } from './sessionColumnLogic';
import CompactionMeter from './CompactionMeter';
import { gaugesSummary, isOverloaded, meterCaption, meterName, type CompactionView } from './compactionMeterLogic';
import './__styles__/SessionColumn.css';

/** What the person can do with the connected folder (moved here from the small folder button beside the message box, where the options had to be cycled one at a time). `options` only while it is connected. */
export interface FolderControls {
  busy: boolean;
  primary: { label: string; run: () => void } | null;
  options: {
    approval: { value: string; onChange: (value: string) => void };
    git: { value: string; onChange: (value: string) => void };
    actions: { label: string; run: () => void }[];
  } | null;
}

/** The target GitHub repo, as it was in the banner above the message box. */
export interface RepoControls { value: string; input: string; saving: boolean; onInput: (value: string) => void; onSave: () => void; onClear: () => void; }

/** The scope Sonic answers from, as a dropdown. */
export interface ScopeControls { value: string; choices: ScopeChoice[]; busy: boolean; onChange: (value: string) => void; }

interface Props {
  /** The connected folder, from the page's workspace. */
  folder: { status: string; name: string | null; fileCount: number | null; branch: string | null };
  /** False for a guest, who has no runner to ask about. */
  runnerEnabled: boolean;
  runners: RunnerInfo[] | null;
  /** The "Start runner" button on a runner that is not answering; null where the link it opens cannot exist (not Windows). `asked` after it was pressed. */
  runnerStart: { phase: RunnerStartPhase; onStart: () => void } | null;
  /** The way to the runner's setup (see ConnectionProps.runnerSetup); the nudge shows at the top of the column while it is due. */
  runnerSetup?: RunnerSetup | null;
  /** See ConnectionProps.onRunnerAsked. */
  onRunnerAsked?: () => void;
  /** The usage ring's view (components/budgetText.ts); null when there is no limit to show. */
  usage: RingView | null;
  /** How full the model's context window got on the last turn, as the server reported it (counts only); null before the first turn. Shown as the compaction meter's tooltip: the ring it used to be is gone. */
  context: ContextUse | null;
  /** The memory compaction meter's reading (notes waiting to be folded together, and the number at which they are); null before the first reading. */
  compaction: CompactionView | null;
  /** Switches the one ring between the memory reading and this conversation's; only given when both exist. */
  onSwitchMeter?: (() => void) | null;
  /** "Fold now" beside the Chat reading of the ring: folds this conversation's older messages into its summary; `folding` while it runs. Only given for a conversation that has something to fold. */
  onFold?: (() => void) | null;
  folding?: boolean;
  /** The approved plan, while there is one. */
  plan: CodePlan | null;
  /** The scope Sonic answers from, as a dropdown right in the panel (it used to open the settings menu's picker down by the message box). null when there are none to pick. */
  scope: ScopeControls | null;
  /** What the last reply drew on from memory. */
  memory: MemorySources | null;
  /** The folder's actions and options; null where a folder cannot be connected (a guest). */
  folderControls: FolderControls | null;
  /** The target repo; null where it is not offered. */
  repo: RepoControls | null;
  /** The chat settings (model, mode, thinking, answers, export, help: components/ComposerChips.tsx in its panel layout), drawn as a card below "This conversation". They sit under the message box
   *  when this column is not showing. */
  controls?: React.ReactNode;
}

const COLLAPSED_KEY = 'sessionColumnCollapsed';

/** One ring with its name and what it says. `level` picks the colour, the same way the usage ring's does. The ring itself is components/Ring.tsx, shared with the memory compaction meter. */
export function Gauge({ name, fraction, level, text }: { name: string; fraction: number; level: string; text: string }) {
  return (
    <div className="sc-gauge">
      <Ring fraction={fraction} level={level} label={`${name}: ${text}`} trackClass="sc-gauge-track" numberClass="sc-gauge-number" />
      <span className="sc-gauge-body"><span className="sc-gauge-name">{name}</span><span className="sc-gauge-text">{text}</span></span>
    </div>
  );
}

/** A choice from a short list, as a dropdown (one line instead of one per option), with what the chosen option means in a small line under it. */
function Options({ label, value, options, onChange, disabled }: { label: string; value: string; options: Option[]; onChange: (value: string) => void; disabled?: boolean }) {
  const current = options.find(option => option.value === value);
  return (
    <label className="sc-field">
      <span className="sc-scope-label">{label}</span>
      <select className="sc-select" value={value} disabled={disabled} onChange={event => onChange(event.target.value)}>
        {options.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
      </select>
      {current && <span className="sc-option-hint">{current.hint}</span>}
    </label>
  );
}

/** The target repo's field and its buttons: in the column's GitHub card and in the phone rail's GitHub popover. */
export function RepoFields({ repo }: { repo: RepoControls }) {
  return (
    <>
      <label className="sc-field">
        <span className="sc-scope-label">Target repo</span>
        <input
          type="text" className="sc-input" value={repo.input} disabled={repo.saving} placeholder="owner/repo (blank = auto-detect)"
          onChange={event => repo.onInput(event.target.value)} onKeyDown={event => { if (event.key === 'Enter') { event.preventDefault(); repo.onSave(); } }}
        />
      </label>
      <div className="sc-actions">
        <button type="button" className="sc-btn sc-btn-primary" onClick={repo.onSave} disabled={repo.saving || repo.input.trim() === repo.value}>Save</button>
        {repo.value && <button type="button" className="sc-btn" onClick={repo.onClear} disabled={repo.saving}>Clear</button>}
      </div>
    </>
  );
}

/** The scope's dropdown: in the column's "This conversation" card and in the phone rail's lock popover. */
export function ScopeField({ scope }: { scope: ScopeControls }) {
  return (
    <label className="sc-scope-row" title="What Sonic answers from">
      <span className="sc-scope-label">Scope</span>
      <select className="sc-select" value={scope.value} disabled={scope.busy} onChange={event => scope.onChange(event.target.value)} aria-label="Scope: what Sonic answers from">
        {scope.choices.map(choice => <option key={choice.value} value={choice.value}>{choice.label}</option>)}
      </select>
    </label>
  );
}

function Row({ line }: { line: Line }) {
  return (
    <div className="sc-row">
      <span className={`sc-dot sc-dot-${line.tone}`} aria-hidden="true" />
      <span className="sc-row-text">
        <span className="sc-row-title">{line.text}</span>
        {line.detail && <span className="sc-row-detail">{line.detail}</span>}
      </span>
    </div>
  );
}

// The tip each card opens in Help Mode (components/helpTips.ts).
const HELP_CARD: Record<string, string> = {
  connection: 'session.connection', github: 'session.github', gauges: 'session.gauges', plan: 'session.plan', conversation: 'session.conversation', controls: 'session.controls',
};

/**
 * One card of the column. Its title is a button that folds it to just the title; a folded card keeps a small `summary` in its header (status dots, a percentage) so folding it does not hide the
 * one thing worth seeing. `badge` is always shown (the plan's "1 of 3 steps").
 */
function Card({ id, label, title, badge, summary, className, collapsed, onToggle, children }: {
  id: string; label: string; title: string; badge?: React.ReactNode; summary?: React.ReactNode; className?: string; collapsed: boolean; onToggle: (id: string) => void; children: React.ReactNode;
}) {
  const bodyId = `sc-body-${id}`;
  return (
    <section className={`sc-card${collapsed ? ' sc-card-collapsed' : ''}${className ? ` ${className}` : ''}`} aria-label={label} data-help={HELP_CARD[id]}>
      <h2 className="sc-title">
        <button type="button" className="sc-title-btn" aria-expanded={!collapsed} aria-controls={bodyId} onClick={() => onToggle(id)}>
          <span className="sc-chevron" aria-hidden="true">▸</span>
          <span>{title}</span>
        </button>
        {badge}
        {collapsed && summary}
      </h2>
      {!collapsed && <div id={bodyId}>{children}</div>}
    </section>
  );
}

/** What the Connection shows and does: the runner on the person's computer and the folder (with its actions and options). The column's Connection card and the phone strip's Connection card both draw it. */
export interface ConnectionProps {
  folder: { status: string; name: string | null; fileCount: number | null; branch: string | null };
  /** False for a guest, who has no runner to ask about. */
  runnerEnabled: boolean;
  runners: RunnerInfo[] | null;
  /** The "Start runner" button on a runner that is not answering; null where the link it opens cannot exist (not Windows). */
  runnerStart: { phase: RunnerStartPhase; onStart: () => void } | null;
  /** The folder's actions and options; null where a folder cannot be connected (a guest, or a browser with no folder picker). */
  folderControls: FolderControls | null;
  /** The way to the runner's setup, which is in the Account settings: `visible` while the nudge is due (no runner set up and not dismissed), `onOpen` goes there, `onDismiss` is "Not now". null where there is no runner to set up (a guest). */
  runnerSetup?: RunnerSetup | null;
  /** Called after "Let the runner run checks here" asked a runner to add the open folder, so the page can ask which projects the runner has now every few seconds (hooks/useRunnerPing askSoon). */
  onRunnerAsked?: () => void;
}

export interface RunnerSetup { visible: boolean; onOpen: () => void; onDismiss: () => void; }

/** The nudge: said once, in the column and the phone strip, where no runner is set up. It explains what the runner is for and opens the Account settings; "Not now" keeps it away for a week. */
export function RunnerNudge({ setup, className }: { setup: RunnerSetup; className?: string }) {
  return (
    <div className={`sc-nudge${className ? ` ${className}` : ''}`} role="status" aria-label={RUNNER_NUDGE_TITLE}>
      <div className="sc-nudge-title">{RUNNER_NUDGE_TITLE}</div>
      <p className="sc-nudge-text">{RUNNER_NUDGE_TEXT}</p>
      <div className="sc-nudge-actions">
        <button type="button" className="sc-btn sc-btn-primary" onClick={setup.onOpen}>Open Account settings</button>
        <button type="button" className="sc-btn" onClick={setup.onDismiss}>Not now</button>
      </div>
    </div>
  );
}

/** The button that asks the connected runner to add the folder that is open (the one pick: the page sends only the folder's name, and a window on the person's computer confirms it). Drawn by ConnectionPanel when
 *  runnerProjectOffer has something to say; the pressed state is kept here until the runner lists the project or the ask ends another way. */
function RunnerProjectAsk({ offer, onAsked }: { offer: RunnerProjectOffer; onAsked?: () => void }) {
  const [sending, setSending] = useState(false);
  const [pressed, setPressed] = useState(false);
  const [failure, setFailure] = useState('');
  useEffect(() => {
    if (offer.kind !== 'offer' && offer.kind !== 'waiting') setPressed(false);      // the ask ended in a way that needs a new press
  }, [offer.kind]);
  const press = async () => {
    setSending(true);
    setFailure('');
    try {
      await api.addRunnerProject(offer.runnerId, offer.folderName);
      setPressed(true);
      onAsked?.();
    } catch (error) {
      setFailure(error instanceof Error && error.message ? error.message : 'Could not ask the runner.');
    } finally {
      setSending(false);
    }
  };
  const waiting = offer.kind === 'waiting' || (pressed && offer.kind === 'offer');
  const retry = offer.kind === 'declined' || offer.kind === 'expired' || offer.kind === 'error';
  return (
    <div className="sc-runner-start">
      {!waiting && offer.kind !== 'no_picker' && (
        <button type="button" className="sc-btn sc-btn-primary" onClick={() => { void press(); }} disabled={sending} aria-busy={sending}>
          {sending && <span className="sc-spinner" aria-hidden="true" />}
          {retry ? 'Ask again' : 'Let the runner run checks here'}
        </button>
      )}
      {waiting && <span className="sc-spinner" aria-hidden="true" />}
      <span className="sc-option-hint" role="status" aria-live="polite">{failure || runnerProjectHint(waiting ? { ...offer, kind: 'waiting' } : offer)}</span>
    </div>
  );
}

export function ConnectionPanel({ folder, runnerEnabled, runners, runnerStart, folderControls, runnerSetup, onRunnerAsked }: ConnectionProps) {
  // "2s ago" goes stale on its own: redraw the times every few seconds.
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 15_000);
    return () => window.clearInterval(timer);
  }, []);
  const runner = runnerEnabled ? runnerLine(runners, now) : null;
  const folderRow = folderLine(folder.status, folder.name, folder.fileCount);
  const projectOffer = runnerEnabled ? runnerProjectOffer(runners, folder.status, folder.name) : null;
  // With a folder open it is one card with two abilities (Files, Run); with none, the runner row and "No folder connected" stand alone as before.
  const card = folderCard(runners, folder.status, folder.name, folder.fileCount, now);
  return (
    <>
    {card ? <Row line={card.files} /> : null}
    {card ? (runnerEnabled ? <Row line={card.run} /> : null) : (runner && <Row line={runner} />)}
    {projectOffer && <RunnerProjectAsk offer={projectOffer} onAsked={onRunnerAsked} />}
    {runner && runners !== null && runners.length === 0 && runnerSetup && (
      <div className="sc-runner-start">
        <button type="button" className="sc-btn sc-btn-primary" onClick={runnerSetup.onOpen}>Set up the runner</button>
        <span className="sc-option-hint">It runs your tests and checks on your own computer, after you approve each one. The steps are in your Account settings.</span>
      </div>
    )}
    {runner && runner.tone === 'warn' && runnerStart && (
      <div className="sc-runner-start">
        <button type="button" className="sc-btn sc-btn-primary" onClick={runnerStart.onStart} disabled={runnerStart.phase === 'starting'} aria-busy={runnerStart.phase === 'starting'}>
          {runnerStart.phase === 'starting' && <span className="sc-spinner" aria-hidden="true" />}
          {runnerStart.phase === 'starting' ? 'Starting…' : runnerStart.phase === 'failed' ? 'Try again' : 'Start runner'}
        </button>
        <span className="sc-option-hint" role="status" aria-live="polite">
          {runnerStart.phase === 'starting' && 'Asked your computer to start it. Waiting for it to connect; this usually takes a few seconds.'}
          {runnerStart.phase === 'failed' && <>It did not connect. If nothing started on your computer, run this once in a terminal there, then try again.</>}
        </span>
        {runnerStart.phase === 'failed' && (
          <span className="sc-option-hint">
            PowerShell: <code>{RUNNER_INSTALL_POWERSHELL}</code>
            <br />
            Command Prompt: <code>{RUNNER_INSTALL_CMD}</code>
          </span>
        )}
      </div>
    )}
    {!card && <Row line={folderRow} />}
    {folder.branch && <div className="sc-branch">On branch <code>{folder.branch}</code></div>}
    {folderControls && (
      <div className="sc-folder-controls">
        {folderControls.primary && (
          <button type="button" className="sc-btn sc-btn-primary" onClick={folderControls.primary.run} disabled={folderControls.busy}>{folderControls.primary.label}</button>
        )}
        {folderControls.options && (
          <>
            <Options label="When Sonic edits your files" value={folderControls.options.approval.value} options={APPROVAL_OPTIONS} onChange={folderControls.options.approval.onChange} />
            <Options label="After Sonic commits" value={folderControls.options.git.value} options={GIT_OPTIONS} onChange={folderControls.options.git.onChange} />
            <div className="sc-actions">
              {folderControls.options.actions.map(action => (
                <button key={action.label} type="button" className="sc-btn" onClick={action.run} disabled={folderControls.busy}>{action.label}</button>
              ))}
            </div>
          </>
        )}
      </div>
    )}
    </>
  );
}

/**
 * The column beside a conversation: the state of this conversation and workspace, at a glance. Connection (the runner on the person's computer and the folder), the folder's and the repo's
 * controls, the gauges, the plan while there is one, and what this conversation is scoped to. It only shows what the page already knows; settings stay in the "..." menu (the scope row
 * opens that menu's own picker). A card with nothing to show is not drawn, and any card can be folded (remembered in this browser).
 */
const SessionColumn: React.FC<Props> = ({ folder, runnerEnabled, runners, runnerStart, runnerSetup, onRunnerAsked, usage, context, compaction, onSwitchMeter, onFold, folding, plan, scope, memory, folderControls, repo, controls }) => {
  // "2s ago" goes stale on its own: redraw the times every few seconds.
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 15_000);
    return () => window.clearInterval(timer);
  }, []);

  // Which cards are folded: remembered per browser, never needed for the column to work.
  const [folded, setFolded] = useState<ReadonlySet<string>>(() => {
    try { return parseCollapsed(localStorage.getItem(COLLAPSED_KEY)); } catch { return new Set<string>(); }
  });
  const toggle = (id: string) => {
    setFolded(prev => {
      const next = toggleCollapsed(prev, id);
      try { localStorage.setItem(COLLAPSED_KEY, serializeCollapsed(next)); } catch { /* a convenience only */ }
      return next;
    });
  };

  const runner = runnerEnabled ? runnerLine(runners, now) : null;
  const folderRow = folderLine(folder.status, folder.name, folder.fileCount);
  const folderLightKind = folderLight(folder.status);
  const contextView = contextGauge(context);
  const memoryText = memoryLine(memory);
  const progress = plan ? planProgress(plan) : null;
  return (
    <aside className="session-column" aria-label="Session">
      {runnerSetup?.visible && <RunnerNudge setup={runnerSetup} />}
      {/* One card for the connection: the runner on the person's computer and the folder they connected (the same folder for both), with the folder's actions and options below. Folded, its header keeps
          the runner's dot and the folder's light (the one the folder button beside the message box used to carry, green when connected). */}
      <Card
        id="connection" label="Connection" title="Connection" collapsed={folded.has('connection')} onToggle={toggle}
        summary={(
          <span className="sc-summary">
            {runner && <span className={`sc-dot sc-dot-${runner.tone}`} title={runner.text} />}
            {folderLightKind
              ? <span className={`sc-light sc-light-${folderLightKind}`} title={folderRow.text} />
              : <span className={`sc-dot sc-dot-${folderRow.tone}`} title={folderRow.text} />}
          </span>
        )}
      >
        <ConnectionPanel folder={folder} runnerEnabled={runnerEnabled} runners={runners} runnerStart={runnerStart} folderControls={folderControls} runnerSetup={runnerSetup} onRunnerAsked={onRunnerAsked} />
      </Card>

      {repo && (
        <Card id="github" label="GitHub" title="GitHub" collapsed={folded.has('github')} onToggle={toggle} summary={repo.value ? <span className="sc-summary">{repo.value}</span> : undefined}>
          <RepoFields repo={repo} />
        </Card>
      )}

      {(compaction || usage) && (
        <Card
          id="gauges" label="Gauges" title="Gauges" collapsed={folded.has('gauges')} onToggle={toggle}
          summary={<span className="sc-summary">{gaugesSummary(compaction, usage)}</span>}
        >
          {/* The memory compaction meter replaced the context ring (which never came close to filling: a reply is given only the last ten messages). The context figure is kept as its tooltip. */}
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
        </Card>
      )}

      {plan && progress && (
        <Card id="plan" label="Plan" title="Plan" className="sc-card-work" badge={<span className="sc-badge">{progress.label}</span>} collapsed={folded.has('plan')} onToggle={toggle}>
          <p className="sc-goal">{plan.goal}</p>
          <ol className="sc-steps">
            {plan.steps.map(step => (
              <li key={step.id} className={step.status === 'done' ? 'sc-step sc-step-done' : 'sc-step'}>
                <span className="sc-step-mark" aria-hidden="true">{step.status === 'done' ? '✓' : '○'}</span>
                <span>{step.text}</span>
              </li>
            ))}
          </ol>
        </Card>
      )}

      {(scope || memoryText) && (
        <Card id="conversation" label="This conversation" title="This conversation" collapsed={folded.has('conversation')} onToggle={toggle}>
          {scope && <ScopeField scope={scope} />}
          {memoryText && <div className="sc-memory"><span className="sc-scope-label">Memory used</span><span>{memoryText}</span></div>}
        </Card>
      )}

      {controls && (
        <Card id="controls" label="Chat settings" title="Chat settings" collapsed={folded.has('controls')} onToggle={toggle}>
          {controls}
        </Card>
      )}
    </aside>
  );
};

export default SessionColumn;

import React, { useEffect, useState } from 'react';
import type { CodePlan, MemorySources, RunnerInfo } from '../api';
import type { RingView } from './budgetText';
import {
  APPROVAL_OPTIONS, GIT_OPTIONS, LEVEL_COLOR, contextGauge, folderLight, folderLine, gaugeSummary, memoryLine, parseCollapsed, planProgress, runnerLine, serializeCollapsed, toggleCollapsed,
  type ContextUse, type Line, type Option, type ScopeChoice,
} from './sessionColumnLogic';
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
  /** The usage ring's view (components/budgetText.ts); null when there is no limit to show. */
  usage: RingView | null;
  /** How full the model's context window got on the last turn, as the server reported it (counts only); null before the first turn. */
  context: ContextUse | null;
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
}

const RING_SIZE = 44;
const RING_STROKE = 4;
const RING_RADIUS = (RING_SIZE - RING_STROKE) / 2;
const RING_LENGTH = 2 * Math.PI * RING_RADIUS;
const COLLAPSED_KEY = 'sessionColumnCollapsed';

/** One ring with its name and what it says. `level` picks the colour, the same way the usage ring's does. */
export function Gauge({ name, fraction, level, text }: { name: string; fraction: number; level: string; text: string }) {
  return (
    <div className="sc-gauge">
      <svg width={RING_SIZE} height={RING_SIZE} viewBox={`0 0 ${RING_SIZE} ${RING_SIZE}`} role="img" aria-label={`${name}: ${text}`}>
        <circle cx={RING_SIZE / 2} cy={RING_SIZE / 2} r={RING_RADIUS} fill="none" stroke="var(--border-color)" strokeWidth={RING_STROKE} className="sc-gauge-track" />
        <circle
          cx={RING_SIZE / 2} cy={RING_SIZE / 2} r={RING_RADIUS} fill="none" stroke={LEVEL_COLOR[level] ?? LEVEL_COLOR.ok} strokeWidth={RING_STROKE} strokeLinecap="round"
          strokeDasharray={`${Math.min(1, Math.max(0, fraction)) * RING_LENGTH} ${RING_LENGTH}`} transform={`rotate(-90 ${RING_SIZE / 2} ${RING_SIZE / 2})`}
        />
        <text x={RING_SIZE / 2} y={RING_SIZE / 2 + 4} textAnchor="middle" className="sc-gauge-number">{Math.round(fraction * 100)}%</text>
      </svg>
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

/**
 * One card of the column. Its title is a button that folds it to just the title; a folded card keeps a small `summary` in its header (status dots, a percentage) so folding it does not hide the
 * one thing worth seeing. `badge` is always shown (the plan's "1 of 3 steps").
 */
function Card({ id, label, title, badge, summary, className, collapsed, onToggle, children }: {
  id: string; label: string; title: string; badge?: React.ReactNode; summary?: React.ReactNode; className?: string; collapsed: boolean; onToggle: (id: string) => void; children: React.ReactNode;
}) {
  const bodyId = `sc-body-${id}`;
  return (
    <section className={`sc-card${collapsed ? ' sc-card-collapsed' : ''}${className ? ` ${className}` : ''}`} aria-label={label}>
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

/**
 * The column beside a conversation: the state of this conversation and workspace, at a glance. Connection (the runner on the person's computer and the folder), the folder's and the repo's
 * controls, the gauges, the plan while there is one, and what this conversation is scoped to. It only shows what the page already knows; settings stay in the "..." menu (the scope row
 * opens that menu's own picker). A card with nothing to show is not drawn, and any card can be folded (remembered in this browser).
 */
const SessionColumn: React.FC<Props> = ({ folder, runnerEnabled, runners, usage, context, plan, scope, memory, folderControls, repo }) => {
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
        {runner && <Row line={runner} />}
        <Row line={folderRow} />
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
      </Card>

      {repo && (
        <Card id="github" label="GitHub" title="GitHub" collapsed={folded.has('github')} onToggle={toggle} summary={repo.value ? <span className="sc-summary">{repo.value}</span> : undefined}>
          <RepoFields repo={repo} />
        </Card>
      )}

      {(contextView || usage) && (
        <Card
          id="gauges" label="Gauges" title="Gauges" collapsed={folded.has('gauges')} onToggle={toggle}
          summary={<span className="sc-summary">{gaugeSummary(contextView, usage)}</span>}
        >
          {contextView && <Gauge name="Context" fraction={contextView.fraction} level={contextView.level} text={contextView.text} />}
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
    </aside>
  );
};

export default SessionColumn;

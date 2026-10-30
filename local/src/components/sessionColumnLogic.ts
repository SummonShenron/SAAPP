// The words and states of the session column (components/SessionColumn.tsx), kept apart from React so they can be tested. Nothing here reads anything but what the page already has.
import type { CodePlan, MemorySources, RunnerInfo } from '../api';

export type Tone = 'ok' | 'warn' | 'off';
export interface Line { tone: Tone; text: string; detail: string; }

/** How long ago, in a few words: "just now", "12s ago", "3 min ago", "2 h ago". "" when there is no time or it is not a time. */
export function lastSeenText(iso: string | null | undefined, now: Date = new Date()): string {
  if (!iso) return '';
  const seen = new Date(iso);
  if (Number.isNaN(seen.getTime())) return '';
  const seconds = Math.max(0, Math.round((now.getTime() - seen.getTime()) / 1000));
  if (seconds < 5) return 'just now';
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)} h ago`;
  return `${Math.round(seconds / 86400)} d ago`;
}

/** The runner on the person's computer, as one line: connected (with its project and when it last answered), set up but not answering, or not set up. `null` runners means not asked yet. */
export function runnerLine(runners: RunnerInfo[] | null, now: Date = new Date()): Line {
  if (runners === null) return { tone: 'off', text: 'Checking the runner', detail: '' };
  if (runners.length === 0) return { tone: 'off', text: 'No runner set up', detail: 'Settings has the steps to run checks on your machine.' };
  const live = runners.find(r => r.connected);
  if (live) {
    const project = live.projects[0]?.name;
    const seen = lastSeenText(live.last_seen, now);
    return { tone: 'ok', text: 'Runner connected', detail: [project, seen].filter(Boolean).join(' · ') };
  }
  const seen = lastSeenText(runners[0].last_seen, now);
  return { tone: 'warn', text: 'Runner not answering', detail: seen ? `Last seen ${seen}` : 'Start it on your computer.' };
}

/** The link the "Start runner" button opens. The runner registers it with Windows when it installs its start-at-login entry (runner/src/saapp_runner/service.py), and what it runs is fixed: start the runner. */
export const RUNNER_START_LINK = 'saapp-runner://start';

/** How long the button waits for the runner to connect after a press before it says it did not (the page asks every few seconds for this long: hooks/useRunnerPing.ts). */
export const RUNNER_START_WAIT_MS = 45_000;

export type RunnerStartPhase = 'idle' | 'starting' | 'failed';

/**
 * Where the "Start runner" button is. `attempt` is the last press: `seen` is when the runner was last seen at that moment, and `failed` is set when the wait ran out. A runner that has reported since
 * the press (its last-seen time moved on) is back, so the button is idle again and the row itself says "Runner connected"; no press, or an old one, is idle.
 */
export function runnerStartPhase(attempt: { seen: string; failed: boolean } | null, lastSeen: string): RunnerStartPhase {
  if (!attempt || attempt.seen !== lastSeen) return 'idle';
  return attempt.failed ? 'failed' : 'starting';
}

/** Whether the "Start runner" button is offered: only where the runner can have registered the link, which is Windows (the runner's entries on macOS and Linux restart it by themselves). */
export function canStartRunnerFromLink(userAgent: string): boolean {
  return /Windows/i.test(userAgent || '') && !/Mobile|Android/i.test(userAgent || '');
}

/** The connected folder, as one line. */
export function folderLine(status: string, name: string | null, fileCount: number | null): Line {
  const where = name ?? '';
  switch (status) {
    case 'connected': return { tone: 'ok', text: 'Folder in sync', detail: [where, fileCount !== null ? `${fileCount} files` : ''].filter(Boolean).join(' · ') };
    case 'syncing': return { tone: 'warn', text: 'Syncing the folder', detail: where };
    case 'not-uploaded': return { tone: 'warn', text: 'Not on the server yet', detail: `${where}${where ? ' · ' : ''}it uploads when you send a message` };
    case 'needs-permission': return { tone: 'warn', text: 'Reconnect the folder', detail: where };
    case 'error': return { tone: 'warn', text: 'Folder sync failed', detail: where };
    default: return { tone: 'off', text: 'No folder connected', detail: '' };
  }
}

/** How far the approved plan is: "1 of 3 steps". */
export function planProgress(plan: CodePlan): { done: number; total: number; label: string } {
  const total = plan.steps.length;
  const done = plan.steps.filter(step => step.status === 'done').length;
  return { done, total, label: `${done} of ${total} ${total === 1 ? 'step' : 'steps'}` };
}

/** What the last reply drew on from memory, in a few words ("4 facts, 1 note"); "" when it used none. */
export function memoryLine(memory: MemorySources | null | undefined): string {
  if (!memory) return '';
  const facts = memory.facts?.length ?? 0;
  const notes = memory.notes?.length ?? 0;
  const parts = [facts ? `${facts} ${facts === 1 ? 'fact' : 'facts'}` : '', notes ? `${notes} ${notes === 1 ? 'note' : 'notes'}` : ''].filter(Boolean);
  return parts.join(', ');
}

/** 38,200 as "38k", 1,048,576 as "1.0M", 950 as "950": tokens in a few characters. */
export function formatTokens(count: number): string {
  if (!Number.isFinite(count) || count < 0) return '0';
  if (count >= 1_000_000) return `${(count / 1_000_000).toFixed(1)}M`;
  if (count >= 10_000) return `${Math.round(count / 1000)}k`;
  if (count >= 1000) return `${(count / 1000).toFixed(1)}k`;
  return String(Math.round(count));
}

/** What the server says about how full the model's context window got on the last turn (counts only: `context` event, backend/utils/context_window.py). */
export interface ContextUse { used: number; window: number; fraction: number; }
export type GaugeLevel = 'ok' | 'notice' | 'throttled';

/** The context ring: how full (as the fraction the server worked out, kept between 0 and 1), the colour level, and the words. null when the figures are not real numbers. */
export function contextGauge(use: ContextUse | null): { fraction: number; level: GaugeLevel; text: string } | null {
  if (!use || !Number.isFinite(use.used) || !Number.isFinite(use.window) || use.window <= 0 || use.used <= 0) return null;
  const fraction = Math.min(1, Math.max(0, use.used / use.window));
  const level: GaugeLevel = fraction >= 0.85 ? 'throttled' : fraction >= 0.6 ? 'notice' : 'ok';
  return { fraction, level, text: `Biggest request last turn: ${formatTokens(use.used)} of ${formatTokens(use.window)} tokens` };
}

export interface Option { value: string; label: string; hint: string; }

/** How edits Sonic proposes to the folder are approved (editApproval.ts ApprovalLevel), as a list to pick from instead of one item to cycle through. */
export const APPROVAL_OPTIONS: Option[] = [
  { value: 'ask', label: 'Ask me every time', hint: 'Every change waits for you to press Apply.' },
  { value: 'sonic', label: 'Automatic in .sonic/ only', hint: "Changes in .sonic/ (Sonic's notes and tests) are applied at once. Everything else still asks." },
  { value: 'session', label: 'Automatic this session', hint: 'Every change is applied at once until you close this tab. Each one is shown with Undo.' },
];

/** What happens in the person's own git after Sonic commits (localGit/commitResult.ts LocalGitMode). */
export const GIT_OPTIONS: Option[] = [
  { value: 'off', label: 'Leave it to me', hint: 'No button after a commit.' },
  { value: 'button', label: 'Offer a button', hint: 'A button after Sonic commits brings it into your own repository (experimental).' },
  { value: 'auto', label: 'Update automatically', hint: 'Your repository is brought up to date after each commit (experimental).' },
];

/** The one thing to press to bring the folder to a working state, by status; null when there is nothing to press (connected: the options below apply; syncing: wait). */
export function folderPrimaryLabel(status: string): string | null {
  switch (status) {
    case 'disconnected': return 'Connect a folder';
    case 'needs-permission': return 'Reconnect the folder';
    case 'not-uploaded': return 'Upload it now';
    case 'error': return 'Try again';
    default: return null;
  }
}

const MAX_COLLAPSED = 20;

/** Which cards are folded, read back from what was saved (a JSON list of card ids). Anything else, or nothing, means none are. Never throws. */
export function parseCollapsed(raw: string | null | undefined): Set<string> {
  try {
    const list = JSON.parse(raw ?? '[]');
    return new Set(Array.isArray(list) ? list.filter((id): id is string => typeof id === 'string' && id.length > 0 && id.length <= 40).slice(0, MAX_COLLAPSED) : []);
  } catch {
    return new Set();
  }
}

/** The folded cards with one card's state flipped. The set passed in is not changed. */
export function toggleCollapsed(current: ReadonlySet<string>, id: string): Set<string> {
  const next = new Set(current);
  if (next.has(id)) next.delete(id);
  else next.add(id);
  return next;
}

export function serializeCollapsed(collapsed: ReadonlySet<string>): string {
  return JSON.stringify([...collapsed].slice(0, MAX_COLLAPSED));
}

export type FolderLight = 'on' | 'syncing' | 'pending' | 'warn' | 'error';

/** The light the folder button beside the message box used to carry, now on the Connection card's header when it is folded: green when connected, pulsing while it syncs, hollow when the server has no copy yet,
 *  amber when it needs to be reconnected, red when the sync failed. null (no light) when no folder is connected. */
export function folderLight(status: string): FolderLight | null {
  switch (status) {
    case 'connected': return 'on';
    case 'syncing': return 'syncing';
    case 'not-uploaded': return 'pending';
    case 'needs-permission': return 'warn';
    case 'error': return 'error';
    default: return null;
  }
}

export interface ScopeChoice { value: string; label: string; }

/** What a scope reads as: "All" for the default (every knowledge base the person may use: app.py's target_scope), the knowledge base's name when it is one they can use, else the raw id. */
export function scopeLabel(selected: string, allowed: { id: string; display_name: string }[]): string {
  const known = allowed.find(item => item.id === selected);
  if (known) return known.display_name;
  return selected || 'All';
}

/** The scopes to pick from: "All" first (the way back to the default, which the old picker never offered), then each knowledge base the person may use. A guest is pinned to their one sandbox scope: no "All". */
export function scopeChoices(allowed: { id: string; display_name: string }[], guest: boolean): ScopeChoice[] {
  const named = allowed.map(item => ({ value: item.id, label: item.display_name }));
  return guest ? named : [{ value: 'All', label: 'All' }, ...named];
}

/** "Context 40% · Usage 12%": the gauges in a few words, for a folded card's header and for the rail's rings. "" when there is no gauge to show. */
export function gaugeSummary(context: { fraction: number } | null, usage: { fraction: number } | null): string {
  const percent = (fraction: number) => `${Math.round(Math.min(1, Math.max(0, fraction)) * 100)}%`;
  return [context && `Context ${percent(context.fraction)}`, usage && `Usage ${percent(usage.fraction)}`].filter(Boolean).join(' · ');
}

/** The colour of a gauge ring by its level, the same for the column's rings and the phone strip's small ones. */
export const LEVEL_COLOR: Record<string, string> = { ok: 'var(--usage-ok, #2f7de1)', notice: '#f59e0b', throttled: '#f97316', blocked: 'var(--accent-danger)' };

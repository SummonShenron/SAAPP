// Pure helpers for the session column Terminal card.
// No React, no fetch: these functions only shape data, so they can be unit tested.

export const POLL_INTERVAL_MS = 10000;

export const DEFAULT_TAIL_LINES = 200;

// Send to Sonic: how much of a command's output goes into the chat box (the newest lines, at most this many characters).
export const SEND_TAIL_LINES = 200;
export const SEND_MAX_CHARS = 4000;

// The localStorage key that remembers which connected project the terminal blade opened last.
export const TERMINAL_PICK_KEY = 'saapp.terminal.project';

// Reads the remembered project choice. Returns null when there is no storage, nothing stored, or the read fails.
export function readStoredPick(storage: Pick<Storage, 'getItem'> | null | undefined): string | null {
  try {
    const value = storage ? storage.getItem(TERMINAL_PICK_KEY) : null;
    return typeof value === 'string' && value !== '' ? value : null;
  } catch {
    return null;
  }
}

// Remembers the project choice. Silently does nothing when there is no storage or the write fails.
export function writeStoredPick(storage: Pick<Storage, 'setItem'> | null | undefined, value: string): void {
  try {
    if (storage) storage.setItem(TERMINAL_PICK_KEY, value);
  } catch {
    /* no storage: the choice simply is not remembered */
  }
}

// The text put in the chat box for one command: its command line, its exit code, and the end of its output, in a fenced block the person can edit before sending.
// Never sent by itself.
export function sendToSonicBlock(command: string, exitCode: number | null | undefined, output: string): string {
  const lines = output.replace(/\r\n/g, '\n').split('\n');
  let tail = lines.slice(-SEND_TAIL_LINES).join('\n').replace(/\n+$/, '');
  if (tail.length > SEND_MAX_CHARS) tail = tail.slice(tail.length - SEND_MAX_CHARS);
  // A fence longer than any run of backticks in the output, so the output cannot close the block early.
  const longest = Math.max(2, ...(tail.match(/`+/g) ?? []).map((run) => run.length));
  const fence = '`'.repeat(longest + 1);
  const code = typeof exitCode === 'number' ? String(exitCode) : 'none';
  return ['Command: ' + command, 'Exit code: ' + code, fence, tail, fence].join('\n');
}

// Statuses the runner reports for a command job, plus "not run" for jobs
// that were never approved or never started.
export type TerminalJobStatus =
  | 'running'
  | 'ok'
  | 'failed'
  | 'stopped'
  | 'timed_out'
  | 'waiting_approval'
  | 'not run';

export interface TerminalJobRow {
  id: string;
  command_shown: string;
  status: string;
  exit_code?: number | null;
  duration?: number | null;
  started_at?: string | null;
  truncated?: boolean;
}

export type StatusDot = 'running' | 'ok' | 'bad' | 'waiting' | 'neutral';

// The first block is what the server really stores for a command job (backend/utils/runner_jobs.py `_listed_status`); the rest are older names kept so nothing breaks.
const STATUS_LABELS: Record<string, string> = {
  awaiting_approval: 'Waiting for your OK',
  approved: 'Starting',
  running: 'Running',
  done: 'Finished',
  failed: 'Failed',
  cancelled: 'Stopped',
  'not run': 'Not run',
  ok: 'Finished',
  stopped: 'Stopped',
  timed_out: 'Timed out',
  waiting_approval: 'Waiting for approval',
};

const STATUS_DOTS: Record<string, StatusDot> = {
  awaiting_approval: 'waiting',
  approved: 'running',
  running: 'running',
  done: 'ok',
  failed: 'bad',
  cancelled: 'neutral',
  'not run': 'neutral',
  ok: 'ok',
  stopped: 'neutral',
  timed_out: 'bad',
  waiting_approval: 'waiting',
};

// Human label for a job status. Unknown statuses are shown as they came.
export function statusLabel(status: string): string {
  return STATUS_LABELS[status] ?? status;
}

// Colour bucket for the status dot.
export function statusDot(status: string): StatusDot {
  return STATUS_DOTS[status] ?? 'neutral';
}

// A job is active while it is running or waiting for approval.
export function isActiveStatus(status: string): boolean {
  return status === 'running' || status === 'approved' || status === 'awaiting_approval' || status === 'waiting_approval';
}

// What a row shows: a command that finished with a non-zero exit code is a failure, not "Finished", and one stopped by its time limit says so.
export function rowOutcome(row: { status: string; exit_code?: number | null; timed_out?: boolean }): { label: string; dot: StatusDot } {
  if (row.status === 'done') {
    if (row.timed_out) return { label: 'Timed out', dot: 'bad' };
    if (typeof row.exit_code === 'number' && row.exit_code !== 0) return { label: `Exited ${row.exit_code}`, dot: 'bad' };
  }
  return { label: statusLabel(row.status), dot: statusDot(row.status) };
}

// A row that is waiting for the person's OK gets a Run button.
export function canRun(row: { status: string }): boolean {
  return row.status === 'awaiting_approval';
}

// A row the person can stop: it is running, or approved and about to run.
export function canStop(row: { status: string }): boolean {
  return row.status === 'running' || row.status === 'approved';
}

// Which runner and project the card talks to: the connected runner's project named like the open folder; failing that, the only project of the only connected runner. null when there is no
// such runner (the card is then not drawn). The same rule the server uses for Sonic's own commands (runner_jobs.connected_runner).
export interface TerminalRunner { id: string; name?: string; connected: boolean; commands?: boolean; projects: { id: string; name: string }[]; }
export function terminalTarget(runners: ReadonlyArray<TerminalRunner> | null | undefined, folderName: string): { runnerId: string; projectId: string; projectName: string } | null {
  const live = (runners ?? []).filter((runner) => runner.connected && runner.commands !== false && runner.projects.length > 0);
  const wanted = folderName.trim().toLowerCase();
  if (wanted) {
    for (const runner of live) {
      const project = runner.projects.find((p) => p.name.toLowerCase() === wanted);
      if (project) return { runnerId: runner.id, projectId: project.id, projectName: project.name };
    }
  }
  if (live.length === 1 && live[0].projects.length === 1) return { runnerId: live[0].id, projectId: live[0].projects[0].id, projectName: live[0].projects[0].name };
  return null;
}

// Every project of every connected runner that can run commands, as choices for the terminal blade's header dropdown ("runner name: project"). Used when terminalTarget finds no single match.
export interface TerminalChoice { runnerId: string; projectId: string; projectName: string; label: string; }
export function terminalChoices(runners: ReadonlyArray<TerminalRunner> | null | undefined): TerminalChoice[] {
  const out: TerminalChoice[] = [];
  for (const runner of runners ?? []) {
    if (!runner.connected || runner.commands === false) continue;
    for (const project of runner.projects) {
      out.push({ runnerId: runner.id, projectId: project.id, projectName: project.name, label: `${runner.name || runner.id}: ${project.name}` });
    }
  }
  return out;
}

// Keep only the last `maxLines` lines of output. Returns the text unchanged
// when it is already short enough.
export function trimOutputTail(text: string, maxLines: number = DEFAULT_TAIL_LINES): string {
  if (!text) return '';
  const lines = text.split('\n');
  if (lines.length <= maxLines) return text;
  return lines.slice(lines.length - maxLines).join('\n');
}

// Duration in seconds to a short label such as "850 ms", "12 s" or "2m 05s".
export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds) || seconds < 0) {
    return '';
  }
  if (seconds < 1) return `${Math.round(seconds * 1000)} ms`;
  if (seconds < 60) return `${Math.round(seconds)} s`;
  const whole = Math.round(seconds);
  const minutes = Math.floor(whole / 60);
  const rest = whole % 60;
  return `${minutes}m ${String(rest).padStart(2, '0')}s`;
}

// Flip one row between expanded and folded. Returns a new Set; the input is not changed.
export function toggleExpanded(expandedIds: ReadonlySet<string>, jobId: string): Set<string> {
  const next = new Set(expandedIds);
  if (next.has(jobId)) {
    next.delete(jobId);
  } else {
    next.add(jobId);
  }
  return next;
}

// Poll only while the card is open and at least one listed job is active.
export function shouldPoll(open: boolean, jobs: ReadonlyArray<{ status: string }>): boolean {
  return open && jobs.some((job) => isActiveStatus(job.status));
}

// How often the open terminal asks for news: quickly while something is waiting or running, slowly otherwise (so a command Sonic runs while it is open still shows up).
export const LIVE_POLL_MS = 3000;
export const IDLE_POLL_MS = 10000;
export function pollDelay(jobs: ReadonlyArray<{ status: string }>): number {
  return jobs.some((job) => isActiveStatus(job.status)) ? LIVE_POLL_MS : IDLE_POLL_MS;
}

// The scrollback reads like a terminal: oldest at the top, the newest at the bottom next to the prompt. The server lists newest first.
export function scrollbackOrder<T>(jobs: ReadonlyArray<T>): T[] {
  return [...jobs].reverse();
}

// The commands for the up and down arrows: newest first, each line once.
export function commandHistory(jobs: ReadonlyArray<{ command_shown: string }>): string[] {
  const seen = new Set<string>();
  const lines: string[] = [];
  for (const job of jobs) {
    const line = job.command_shown.trim();
    if (line && !seen.has(line)) {
      seen.add(line);
      lines.push(line);
    }
  }
  return lines;
}

// Up goes further back, down comes forward and past the newest to the empty prompt (-1). `index` is where the person is now (-1 = the line they are typing).
export function stepHistory(length: number, index: number, direction: 'up' | 'down'): number {
  if (length === 0) return -1;
  if (direction === 'up') return Math.min(index + 1, length - 1);
  return Math.max(index - 1, -1);
}

// Ctrl+C stops the command that is running or about to run, the way it does in a terminal; with nothing running it is left alone (so copying text still works).
export function stoppableJob<T extends { status: string }>(jobs: ReadonlyArray<T>): T | undefined {
  return jobs.find((job) => canStop(job));
}

/**
 * The words and decisions behind the local-runner cards, kept apart from the components so they can be tested without a browser (runnerLogic.test.mjs).
 * The runner is a small program on the person's own machine that runs checks they approve on a card (backend/utils/runner_jobs.py, runner/).
 */
import type { RunnerJob, RunnerJobStatus, RunnerSettings } from './api';

const FINAL: readonly RunnerJobStatus[] = ['done', 'cancelled', 'expired', 'failed'];

/** True once a check has an outcome (so the card stops asking the server about it). */
export function isFinal(status: string): boolean {
  return (FINAL as readonly string[]).includes(status);
}

export type Verdict = 'waiting' | 'running' | 'passed' | 'failed' | 'timed_out' | 'cancelled' | 'not_run' | 'error';

export function verdict(job: Pick<RunnerJob, 'status' | 'exit_code' | 'timed_out'>): Verdict {
  switch (job.status) {
    case 'awaiting_approval': return 'waiting';
    case 'approved':
    case 'running': return 'running';
    case 'cancelled': return 'cancelled';
    case 'expired': return 'not_run';
    case 'failed': return 'error';
    case 'done':
      if (job.timed_out) return 'timed_out';
      return job.exit_code === 0 ? 'passed' : 'failed';
    default: return 'error';
  }
}

/** The one line a card leads with. */
export function headline(job: RunnerJob): string {
  switch (verdict(job)) {
    case 'waiting': return 'Waiting for you to press Run';
    case 'running': return job.status === 'approved' ? 'Approved. Waiting for your runner to pick it up...' : 'Running on your machine...';
    case 'passed': return `Passed${job.duration != null ? ` in ${job.duration} s` : ''}`;
    case 'failed': return `Failed (exit code ${job.exit_code})${job.duration != null ? ` after ${job.duration} s` : ''}`;
    case 'timed_out': return 'Timed out, so the result is unknown';
    case 'cancelled': return 'Cancelled';
    case 'not_run': return `Not run: ${job.error || 'it expired'}`;
    case 'error': return `Could not run: ${job.error || 'the runner reported a problem'}`;
  }
}

/** The files a check was limited to, for the card ("tests/test_a.py, tests/test_b.py"), or "" when it runs on everything. */
export function filesLine(job: Pick<RunnerJob, 'files'>): string {
  return (job.files ?? []).join(', ');
}

/** Whether a check is turned on for a runner (every check is, until the person turns it off). */
export function checkIsOn(settings: RunnerSettings, name: string): boolean {
  return !settings.disabled.includes(name);
}

/** The settings after the person ticks or unticks one check. Only a name is ever stored, never a command. */
export function withCheck(settings: RunnerSettings, name: string, on: boolean): RunnerSettings {
  const rest = settings.disabled.filter(item => item !== name);
  return { ...settings, disabled: on ? rest : [...rest, name] };
}

/** The settings after the person ticks or unticks "use a fake AI key". */
export function withFakeKeys(settings: RunnerSettings, on: boolean): RunnerSettings {
  return { ...settings, fake_keys: on };
}

/** What to say under a runner when it has no checks to show. */
export function noChecksLine(connected: boolean): string {
  return connected
    ? 'This runner found no tests or type check in its folder. If the project has some, they can be written in the runner\'s config file (see its README).'
    : 'Its checks appear here once the runner has connected.';
}

export type OsKind = 'windows' | 'mac' | 'linux';

/** Which starter script to offer first. A guess from the browser, never relied on: every script stays one click away. */
export function detectOs(platform: string, userAgent: string): OsKind {
  const text = `${platform} ${userAgent}`.toLowerCase();
  if (text.includes('win')) return 'windows';
  if (text.includes('mac') || text.includes('iphone') || text.includes('ipad')) return 'mac';
  return 'linux';
}

/** What to do with the downloaded file. Browsers do not mark a download as runnable, and Windows warns about downloaded scripts. */
export function runHint(kind: OsKind, filename: string): string {
  if (kind === 'windows') {
    return `Double-click ${filename}. Windows may warn about a downloaded file: choose More info, then Run anyway. You can open it in Notepad first to read it.`;
  }
  return `In a terminal, run:  bash ~/Downloads/${filename}   (read the file first if you like: it is short).`;
}

export const OS_LABEL: Record<OsKind, string> = { windows: 'Windows', mac: 'macOS', linux: 'Linux' };

/** Minutes left on a pairing code, rounded up, never below 0. */
export function minutesLeft(expiresAt: string, now: number): number {
  const ms = Date.parse(expiresAt) - now;
  return Number.isFinite(ms) ? Math.max(0, Math.ceil(ms / 60000)) : 0;
}

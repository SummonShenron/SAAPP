/**
 * The words and decisions behind the local-runner cards, kept apart from the components so they can be tested without a browser (runnerLogic.test.mjs).
 * The runner is a small program on the person's own machine that runs checks they approve on a card (backend/utils/runner_jobs.py, runner/).
 */
import type { ProjectSettings, RunnerJob, RunnerJobStatus, RunnerRequest, RunnerSettings, RunnerUpdate } from './api';

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

/** Whether a job is a command line (not one of the project's named checks). */
export function isCommand(job: Pick<RunnerJob, 'kind'>): boolean {
  return job.kind === 'command';
}

/** What a command's class means, in words for the card. */
export function classSentence(klass: RunnerJob['command_class']): string {
  switch (klass) {
    case 'read': return 'Only looks: it does not change anything.';
    case 'run': return "Runs a program in your project. It can write files the way that program normally does.";
    case 'change': return 'Changes things: it can edit files, change git, install packages or use the network.';
    default: return '';
  }
}

/** Whether pressing Run on this job needs the second, stronger confirmation first. */
export function needsSecondStep(job: Pick<RunnerJob, 'kind' | 'command_class' | 'needs_confirm'>): boolean {
  return isCommand(job) && (job.needs_confirm === true || job.command_class === 'change');
}

/** The one line a card leads with. */
export function headline(job: RunnerJob): string {
  if (isCommand(job)) return commandHeadline(job);
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

function commandHeadline(job: RunnerJob): string {
  switch (verdict(job)) {
    case 'waiting': return 'Waiting for you to press Run';
    case 'running': return job.status === 'approved' ? (job.auto_approved ? 'Looking only, so it started by itself. Waiting for your runner...' : 'Approved. Waiting for your runner to pick it up...') : 'Running on your machine...';
    case 'passed': return `Finished${job.duration != null ? ` in ${job.duration} s` : ''}`;
    case 'failed': return `Exited with code ${job.exit_code}${job.duration != null ? ` after ${job.duration} s` : ''}`;
    case 'timed_out': return 'Timed out, so the result is unknown';
    case 'cancelled': return 'Cancelled';
    case 'not_run': return `Not run: ${job.error || 'it expired'}`;
    case 'error': return `Did not run: ${job.error || 'the runner reported a problem'}`;
  }
}

/** The files a check was limited to, for the card ("tests/test_a.py, tests/test_b.py"), or "" when it runs on everything. */
export function filesLine(job: Pick<RunnerJob, 'files'>): string {
  return (job.files ?? []).join(', ');
}

/** Whether a check is turned on for a project (every check is, until the person turns it off). */
export function checkIsOn(settings: ProjectSettings, name: string): boolean {
  return !settings.disabled.includes(name);
}

/** The settings after the person ticks or unticks one check. Only a name is ever stored, never a command. */
export function withCheck(settings: ProjectSettings, name: string, on: boolean): ProjectSettings {
  const rest = settings.disabled.filter(item => item !== name);
  return { ...settings, disabled: on ? rest : [...rest, name] };
}

/** The settings after the person ticks or unticks "update automatically" (the runner's own setting, not a project's). */
export function withAutoUpdate(settings: RunnerSettings, on: boolean): RunnerSettings {
  return { ...settings, auto_update: on };
}

/** The settings after the person ticks or unticks "run commands that only look without asking" (the computer's own setting). */
export function withReadOnlyAuto(settings: RunnerSettings, on: boolean): RunnerSettings {
  return { ...settings, read_only_auto: on };
}

/** The first runner version that can update itself; an older one has to be set up again by hand. */
export const SELF_UPDATE_FROM = '0.1.1';

export function canSelfUpdate(version: string): boolean {
  return parseVersion(version) !== null && !isOlderVersion(version, SELF_UPDATE_FROM);
}

export interface UpdateNote { text: string; canPress: boolean; }

/** What to say about updating one runner, and whether the Update button can be pressed now. null when it is up to date. */
export function updateNote(runner: { version: string; connected: boolean; launch: string; update?: RunnerUpdate }): UpdateNote | null {
  const info = runner.update;
  if (!info || !info.available) return null;
  if (!canSelfUpdate(runner.version)) {
    return { canPress: false, text: `A newer runner is available (${info.wanted}). This older one can't update itself: choose Add a runner, run the setup file in this same project folder, then remove this old entry.` };
  }
  if (info.pending) {
    return { canPress: false, text: `Updating to ${info.wanted}... the runner installs it when it is idle and then restarts, so this can take a minute or two.` };
  }
  if (!runner.connected) {
    return { canPress: false, text: `A newer runner is available (${info.wanted}). Start this runner first; then it can update.` };
  }
  return {
    canPress: true,
    text: `A newer runner is available (${info.wanted}). ` + (runner.launch === 'hand' ? 'It was started by hand, so you will need to start it again after it updates.' : 'It restarts by itself after updating.'),
  };
}

/** The settings after the person ticks or unticks "use a fake AI key". */
export function withFakeKeys(settings: ProjectSettings, on: boolean): ProjectSettings {
  return { ...settings, fake_keys: on };
}

/** What to say under a project when it has no checks to show. */
export function noChecksLine(connected: boolean): string {
  return connected
    ? 'No tests or type check was found in this project. If it has some, they can be written in the runner\'s config file (see its README).'
    : 'Its checks appear here once the runner has connected.';
}

/** What to say about a request to add a project, from where it stands. "" for one that needs no words (added: the project shows in the list). */
export function requestSentence(request: Pick<RunnerRequest, 'name' | 'state'>): string {
  switch (request.state) {
    case 'waiting': return `Waiting for your computer to pick up the request for "${request.name}"...`;
    case 'asked': return `A window on your computer asks you to choose the folder for "${request.name}". Nothing is added until you choose it there.`;
    case 'declined': return `You said no on your computer, so "${request.name}" was not added.`;
    case 'no_picker': return `That computer can't show a folder window. Add "${request.name}" by typing this there: saapp-runner add-project --root <the project folder>`;
    case 'error': return `The folder you chose for "${request.name}" could not be added (a project must be a project's own folder, not your whole home folder or a drive).`;
    case 'expired': return `Nobody answered the window for "${request.name}" in time. Ask again when you are at that computer.`;
    default: return '';
  }
}

/** Whether any request is still waiting for the computer or for the person (so the page keeps looking). */
export function requestsOpen(requests: Array<Pick<RunnerRequest, 'state'>>): boolean {
  return requests.some(request => request.state === 'waiting' || request.state === 'asked');
}

/** The requests worth showing under a runner: the ones not yet answered, and the ones that did not end in a project being added. */
export function requestsToShow(requests: RunnerRequest[]): RunnerRequest[] {
  return requests.filter(request => request.state !== 'added');
}

/**
 * What the runner says about itself is a code from a fixed list (runner/src/saapp_runner/status.py), never free text from its computer; the page turns each into a sentence. "ok" and anything
 * unknown say nothing.
 */
const STATUS_SENTENCES: Record<string, string> = {
  folder_missing: "The project folder this runner was set up for can't be found. It may have been moved, renamed or deleted. Run the setup file again for the folder you mean.",
  config_problem: "The runner couldn't read its config file, so only the checks it finds in the folder are available.",
  no_checks_found: "No tests or type check was found in the project folder, so there is nothing to run yet. If it has some, add them to the runner's config file: it looks again every minute.",
  command_missing: "A check's program (for example Python or npm) can't be found on that computer, so that check would fail. Install it, or fix the check.",
  update_failed: "The last update didn't work, so this runner is still on its old version. It won't try again for a few hours; run the setup file again to update it now.",
};

export function statusSentence(code: string | null | undefined): string {
  return STATUS_SENTENCES[code ?? ''] ?? '';
}

/** What to say about how a connected runner was started. "" when there is nothing worth saying. */
export function launchHint(launch: string | null | undefined): string {
  if (launch === 'hand') {
    return 'This runner was started by hand, so it stops when its window closes or the computer restarts. To start it by itself at login, run: saapp-runner install-service (the setup file does this for you).';
  }
  return '';
}

/** "just now", "5 minutes ago", "3 hours ago", "2 days ago"; "" when there is no usable time. */
export function agoText(iso: string | null | undefined, now: number): string {
  const then = Date.parse(iso ?? '');
  if (!Number.isFinite(then)) return '';
  const seconds = Math.max(0, Math.round((now - then) / 1000));
  if (seconds < 90) return 'just now';
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} minutes ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 36) return `${hours} hour${hours === 1 ? '' : 's'} ago`;
  const days = Math.round(hours / 24);
  return `${days} day${days === 1 ? '' : 's'} ago`;
}

/** Why a runner reads "Not connected", as far as the page can tell. */
export function notConnectedReason(lastSeen: string | null | undefined, now: number): string {
  const ago = agoText(lastSeen, now);
  if (!ago) return "It hasn't connected yet. Run the setup file you downloaded; it should show here within a minute.";
  return `Last seen ${ago}. The runner may have been stopped, or the computer may be asleep or offline. Start it again to reconnect.`;
}

/** "0.1.1" as [0, 1, 1], or null when it is not a plain three-part version. */
export function parseVersion(text: string): [number, number, number] | null {
  const match = /^\s*(\d{1,4})\.(\d{1,4})\.(\d{1,4})\s*$/.exec(text ?? '');
  return match ? [Number(match[1]), Number(match[2]), Number(match[3])] : null;
}

/** Whether a runner's version is older than the one this server asks for. False when either is not a plain version (nothing to say then). */
export function isOlderVersion(have: string, wanted: string): boolean {
  const a = parseVersion(have);
  const b = parseVersion(wanted);
  if (!a || !b) return false;
  for (let i = 0; i < 3; i += 1) {
    if (a[i] !== b[i]) return a[i] < b[i];
  }
  return false;
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

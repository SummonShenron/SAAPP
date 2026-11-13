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
  if (isServe(job) || isLook(job)) return pageHeadline(job);
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

/** What to say about a request to send a project's files (kind 'files'), from where it stands. The first two say that nothing is sent unless the person says yes on their computer. */
function filesRequestSentence(request: Pick<RunnerRequest, 'name' | 'state'>): string {
  switch (request.state) {
    case 'waiting': return `Waiting for your computer to pick up the request to share the files of "${request.name}"...`;
    case 'asked': return `A window on your computer asks whether to send the files of "${request.name}" to SAAPP. Nothing is sent unless you say yes there.`;
    case 'shared': return `The files of "${request.name}" were sent. Sonic can read them now.`;
    case 'declined': return 'You said no on your computer, so nothing was sent.';
    case 'no_picker': return "That computer can't show a window to ask you, so nothing was sent.";
    case 'error': return `The files of "${request.name}" could not be sent. Try again; if it keeps failing, run the runner in a terminal there to see why.`;
    case 'expired': return 'Nobody answered the window in time, so nothing was sent. Ask again when you are at that computer.';
    default: return '';
  }
}

/** What to say about a request to add a project, from where it stands. "" for one that needs no words (added: the project shows in the list). */
export function requestSentence(request: Pick<RunnerRequest, 'name' | 'state'> & { kind?: string }): string {
  if (request.kind === 'files') return filesRequestSentence(request);
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

/** The latest request to send this project's files, or undefined. */
export function filesRequestFor(requests: RunnerRequest[], projectId: string): RunnerRequest | undefined {
  return [...requests].reverse().find(request => request.kind === 'files' && request.project_id === projectId);
}

/** The first runner that can send a project's files (backend/utils/runner_jobs.py FILES_MIN_VERSION); an older one would ignore the request. */
export const FILES_MIN_VERSION = '0.2.2';

/** Whether a runner's version ("0.2.1", or "" from one that has not said) can send files. */
export function runnerCanShareFiles(version: string | null | undefined): boolean {
  const parts = (version ?? '').split('.').map(Number);
  const wanted = FILES_MIN_VERSION.split('.').map(Number);
  if (parts.length !== 3 || parts.some(part => !Number.isInteger(part))) return false;
  for (let index = 0; index < 3; index += 1) {
    if (parts[index] !== wanted[index]) return parts[index] > wanted[index];
  }
  return true;
}

/** Whether the "Let Sonic read this project's files" button is pressable: the runner is answering, is new enough, and no earlier ask for this project is still open. */
export function canShareFiles(runnerConnected: boolean, requests: RunnerRequest[], projectId: string, version: string | null | undefined = FILES_MIN_VERSION): boolean {
  const open = filesRequestFor(requests, projectId);
  return runnerConnected && runnerCanShareFiles(version) && !(open && (open.state === 'waiting' || open.state === 'asked'));
}

/** What Sonic reads for this project when the runner filled the server's copy: "Sonic is reading 142 files from this project (sent 5 min ago)", or null when the copy is not from the runner or is another project's. */
export function sharingLine(workspace: { connected: boolean; name?: string; file_count?: number; synced_at?: number; source?: string } | null, projectName: string, now: number = Date.now()): string | null {
  if (!workspace || !workspace.connected || workspace.source !== 'runner' || (workspace.name ?? '').toLowerCase() !== projectName.toLowerCase()) return null;
  const count = workspace.file_count ?? 0;
  const minutes = workspace.synced_at ? Math.max(0, Math.round((now - workspace.synced_at * 1000) / 60000)) : null;
  const when = minutes === null ? '' : minutes < 1 ? ' (sent just now)' : minutes < 60 ? ` (sent ${minutes} min ago)` : ` (sent ${Math.round(minutes / 60)} h ago)`;
  return `Sonic is reading ${count} ${count === 1 ? 'file' : 'files'} from this project${when}.`;
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

// ---- a project's server, and looking at a page (backend/utils/runner_jobs.py, runner/src/saapp_runner/servers.py and browser.py) ----------------------------------------------------------------------------

/** What one approval of a server card covers (the server's SESSION_MAX_LOOKS and SESSION_IDLE_MINUTES). */
export const SESSION_LOOKS = 10;
export const SESSION_IDLE_MINUTES = 30;

export function isServe(job: Pick<RunnerJob, 'kind'>): boolean {
  return job.kind === 'serve';
}

export function isLook(job: Pick<RunnerJob, 'kind'>): boolean {
  return job.kind === 'look';
}

/** The window sizes a look can use, as the card says them (the runner's DEVICES). */
export function deviceLabel(device: string | undefined): string {
  switch (device) {
    case 'phone': return 'phone (390 × 844)';
    case 'tablet': return 'tablet (820 × 1180)';
    case 'desktop': return 'desktop (1280 × 800)';
    default: return 'desktop';
  }
}

/** What approving a server card means: the one sentence the person reads before pressing Run. */
export function serveNote(job: Pick<RunnerJob, 'session_looks_left'>): string {
  return `This starts your project's own server on your computer and keeps it running. That one approval also lets Sonic look at its pages up to ${SESSION_LOOKS} times without asking again, until it has gone ${SESSION_IDLE_MINUTES} minutes unused or you stop it. It runs with your own privileges: read the command above first.`;
}

/** What a look will do, said before it runs: where it goes, and where the picture and text go. */
export function lookNote(job: Pick<RunnerJob, 'session' | 'device'>): string {
  const sent = "A hidden browser on your computer opens only this address (every request to anywhere else is blocked). The screenshot and the page's text are sent to Sonic and its AI model.";
  return job.session ? `Part of the preview session you approved. ${sent}` : sent;
}

/** The card's lead line for a server or a look; "" for anything else (the caller uses `headline`). */
export function pageHeadline(job: RunnerJob): string {
  if (isServe(job)) {
    switch (verdict(job)) {
      case 'waiting': return 'Waiting for you to press Run';
      case 'running': return job.status === 'approved' ? 'Approved. Waiting for your runner to pick it up...' : 'Starting the server...';
      case 'passed': return job.server?.port ? `Running on port ${job.server.port}` : 'Running';
      case 'cancelled': return 'Cancelled';
      case 'not_run': return `Not started: ${job.error || 'it expired'}`;
      default: return `Did not start: ${job.error || 'the runner reported a problem'}`;
    }
  }
  switch (verdict(job)) {
    case 'waiting': return 'Waiting for you to press Run';
    case 'running': return job.status === 'approved' ? (job.auto_approved ? 'Part of your preview session, so it started by itself. Waiting for your runner...' : 'Approved. Waiting for your runner to pick it up...') : 'Looking at the page...';
    case 'passed': return `Looked${job.duration != null ? ` (${job.duration} s)` : ''}`;
    case 'cancelled': return 'Cancelled';
    case 'not_run': return `Not looked at: ${job.error || 'it expired'}`;
    default: return `Could not look: ${job.error || 'the runner reported a problem'}`;
  }
}

/** What the runner read from the page, as short plain lines for the card (problems first). Empty when there is nothing to say. */
export function lookLines(job: Pick<RunnerJob, 'look' | 'device'>): string[] {
  const look = job.look;
  if (!look) return [];
  const lines: string[] = [];
  const width = look.viewport?.[0] ?? 0;
  if (look.overflow_x) {
    const wide = (look.wide_elements ?? []).slice(0, 3).map(item => `${item.el} reaches ${item.right} px`).join('; ');
    lines.push(`Wider than the window: the content is ${look.scroll_width} px wide in a ${width} px window${wide ? ` (${wide})` : ''}.`);
  } else if (width) {
    lines.push(`Fits the width of the window (${width} px).`);
  }
  if ((job.device === 'phone' || job.device === 'tablet') && look.viewport_meta === false) lines.push('No viewport tag: a phone shows this page zoomed out, laid out at about 980 px.');
  for (const item of look.console ?? []) lines.push(`Console: ${item}`);
  for (const item of look.failed ?? []) lines.push(`Request: ${item}`);
  if ((look.blocked ?? []).length > 0) lines.push(`Blocked (outside addresses, by design): ${(look.blocked ?? []).join(', ')}`);
  return lines;
}

/** Whether to warn that the server can be reached from other computers on the person's network. */
export function serverExposed(job: Pick<RunnerJob, 'server'>): boolean {
  return job.server?.exposed === true;
}

/** Whether the server card is for a server that is up now and can be stopped. */
export function canStopServer(job: Pick<RunnerJob, 'kind' | 'status' | 'server' | 'session_stopped'>): boolean {
  return isServe(job) && job.status === 'done' && job.server?.status === 'running' && job.session_stopped !== true;
}

/** What Settings says about looking at pages: which browser the runner found, or what is missing. "" for a runner that has no news (an older one that cannot look is told to update by `updateNote`). */
export function browserLine(runner: { browser?: string; looks?: boolean; version: string; connected?: boolean }): string {
  if (runner.looks && runner.browser) {
    const name = ({ chrome: 'Chrome', edge: 'Edge', chromium: 'Chromium', custom: 'your own browser' } as Record<string, string>)[runner.browser] ?? 'a browser';
    return `Sonic can look at your pages and start your project's server here (using ${name}).`;
  }
  if (parseVersion(runner.version) !== null && !isOlderVersion(runner.version, LOOK_FROM) && !runner.browser) {
    return 'No Chrome or Edge was found on this computer, so Sonic cannot look at your pages. Install one and restart the runner.';
  }
  return '';
}

/** The first runner version that can keep a server running and look at a page. */
export const LOOK_FROM = '0.2.0';

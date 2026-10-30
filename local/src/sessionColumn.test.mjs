// Run with: node --test src/sessionColumn.test.mjs   (from local/; Node 22.18+ runs the TypeScript module directly)
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { folderLine, lastSeenText, memoryLine, planProgress, runnerLine } from './components/sessionColumnLogic.ts';

const NOW = new Date('2026-10-29T12:00:00Z');
const ago = seconds => new Date(NOW.getTime() - seconds * 1000).toISOString();
const runner = (over = {}) => ({ id: 'r1', name: 'Jack PC', connected: true, last_seen: ago(2), projects: [{ id: 'p1', name: 'local-rag' }], ...over });

test('how long ago is said in a few words, and nothing for no time or nonsense', () => {
  assert.equal(lastSeenText(ago(2), NOW), 'just now');
  assert.equal(lastSeenText(ago(12), NOW), '12s ago');
  assert.equal(lastSeenText(ago(180), NOW), '3 min ago');
  assert.equal(lastSeenText(ago(7200), NOW), '2 h ago');
  assert.equal(lastSeenText(ago(3 * 86400), NOW), '3 d ago');
  assert.equal(lastSeenText(ago(-30), NOW), 'just now');          // a clock a little ahead is not "in the future"
  assert.equal(lastSeenText(null, NOW), '');
  assert.equal(lastSeenText('not a time', NOW), '');
});

test('the runner line says connected with its project and last answer, not answering with when it was last seen, or not set up', () => {
  assert.deepEqual(runnerLine([runner()], NOW), { tone: 'ok', text: 'Runner connected', detail: 'local-rag · just now' });
  assert.deepEqual(runnerLine([runner({ connected: false, last_seen: ago(600) })], NOW), { tone: 'warn', text: 'Runner not answering', detail: 'Last seen 10 min ago' });
  assert.equal(runnerLine([runner({ connected: false, last_seen: null })], NOW).detail, 'Start it on your computer.');
  assert.equal(runnerLine([], NOW).text, 'No runner set up');
  assert.equal(runnerLine(null, NOW).text, 'Checking the runner');
  assert.equal(runnerLine([runner({ connected: false }), runner({ id: 'r2', name: 'Laptop' })], NOW).text, 'Runner connected');       // any connected runner counts
  assert.equal(runnerLine([runner({ projects: [] })], NOW).detail, 'just now');
});

test('the folder line follows the workspace status, with the folder name and how many files', () => {
  assert.deepEqual(folderLine('connected', 'local-rag', 1520), { tone: 'ok', text: 'Folder in sync', detail: 'local-rag · 1520 files' });
  assert.equal(folderLine('connected', 'local-rag', null).detail, 'local-rag');
  assert.equal(folderLine('syncing', 'x', null).tone, 'warn');
  assert.match(folderLine('not-uploaded', 'x', null).detail, /uploads when you send a message/);
  assert.equal(folderLine('needs-permission', 'x', null).text, 'Reconnect the folder');
  assert.equal(folderLine('error', 'x', null).text, 'Folder sync failed');
  assert.deepEqual(folderLine('disconnected', null, null), { tone: 'off', text: 'No folder connected', detail: '' });
});

test('plan progress counts the done steps', () => {
  const plan = { steps: [{ status: 'done' }, { status: 'todo' }, { status: 'todo' }] };
  assert.deepEqual(planProgress(plan), { done: 1, total: 3, label: '1 of 3 steps' });
  assert.equal(planProgress({ steps: [{ status: 'done' }] }).label, '1 of 1 step');
});

test('the memory line counts facts and notes and says nothing when there were none', () => {
  assert.equal(memoryLine({ facts: [{}, {}, {}, {}], notes: [{}] }), '4 facts, 1 note');
  assert.equal(memoryLine({ facts: [{}], notes: [] }), '1 fact');
  assert.equal(memoryLine({ facts: [], notes: [] }), '');
  assert.equal(memoryLine(null), '');
});

test('tokens are said in a few characters', async () => {
  const { formatTokens } = await import('./components/sessionColumnLogic.ts');
  assert.equal(formatTokens(950), '950');
  assert.equal(formatTokens(1500), '1.5k');
  assert.equal(formatTokens(38_200), '38k');
  assert.equal(formatTokens(1_048_576), '1.0M');
  assert.equal(formatTokens(-4), '0');
  assert.equal(formatTokens(NaN), '0');
});

test('the context ring says how full the biggest request was, changes colour as it nears the window, and draws nothing for numbers that are not real', async () => {
  const { contextGauge } = await import('./components/sessionColumnLogic.ts');
  const ok = contextGauge({ used: 38_200, window: 1_048_576, fraction: 0.036 });
  assert.equal(ok.level, 'ok');
  assert.equal(ok.text, 'Biggest request last turn: 38k of 1.0M tokens');
  assert.ok(Math.abs(ok.fraction - 38_200 / 1_048_576) < 1e-9);
  assert.equal(contextGauge({ used: 130_000, window: 200_000, fraction: 0.65 }).level, 'notice');
  assert.equal(contextGauge({ used: 180_000, window: 200_000, fraction: 0.9 }).level, 'throttled');
  assert.equal(contextGauge({ used: 250_000, window: 200_000, fraction: 1 }).fraction, 1);          // over the window is a full ring, not a broken one
  assert.equal(contextGauge(null), null);
  assert.equal(contextGauge({ used: 0, window: 200_000, fraction: 0 }), null);
  assert.equal(contextGauge({ used: 5, window: 0, fraction: 0 }), null);
  assert.equal(contextGauge({ used: NaN, window: 10, fraction: 0 }), null);
});

test('the folder has one thing to press while it is not working, and nothing while it works or syncs', async () => {
  const { folderPrimaryLabel } = await import('./components/sessionColumnLogic.ts');
  assert.equal(folderPrimaryLabel('disconnected'), 'Connect a folder');
  assert.equal(folderPrimaryLabel('needs-permission'), 'Reconnect the folder');
  assert.equal(folderPrimaryLabel('not-uploaded'), 'Upload it now');
  assert.equal(folderPrimaryLabel('error'), 'Try again');
  assert.equal(folderPrimaryLabel('connected'), null);
  assert.equal(folderPrimaryLabel('syncing'), null);
});

test('the option lists cover exactly the values the app can hold, so nothing the menu offered is lost', async () => {
  const { APPROVAL_OPTIONS, GIT_OPTIONS } = await import('./components/sessionColumnLogic.ts');
  const { nextLevel } = await import('./editApproval.ts');
  const { nextLocalGitMode } = await import('./localGit/commitResult.ts');
  const cycle = (start, next) => { const seen = [start]; for (let v = next(start); v !== start; v = next(v)) seen.push(v); return seen; };
  assert.deepEqual([...APPROVAL_OPTIONS.map(o => o.value)].sort(), cycle('ask', nextLevel).sort());
  assert.deepEqual([...GIT_OPTIONS.map(o => o.value)].sort(), cycle('off', nextLocalGitMode).sort());
  for (const option of [...APPROVAL_OPTIONS, ...GIT_OPTIONS]) assert.ok(option.label && option.hint, option.value);
});

test('the folded cards are read back safely, flipped one at a time without changing the set they came from, and saved as a short list', async () => {
  const { parseCollapsed, toggleCollapsed, serializeCollapsed } = await import('./components/sessionColumnLogic.ts');
  assert.deepEqual([...parseCollapsed('["gauges","plan"]')], ['gauges', 'plan']);
  for (const junk of [null, undefined, '', 'not json', '{"a":1}', '"gauges"', '[1,null,{}]']) assert.equal(parseCollapsed(junk).size, 0, String(junk));
  assert.deepEqual([...parseCollapsed('["ok", 5, "", "also"]')], ['ok', 'also']);          // only real ids are kept
  assert.equal(parseCollapsed(JSON.stringify(Array.from({ length: 50 }, (_, i) => `card${i}`))).size, 20);
  const start = new Set(['plan']);
  const opened = toggleCollapsed(start, 'plan');
  const folded = toggleCollapsed(start, 'gauges');
  assert.deepEqual([...opened], []);
  assert.deepEqual([...folded].sort(), ['gauges', 'plan']);
  assert.deepEqual([...start], ['plan']);                                                    // the original is untouched
  assert.equal(serializeCollapsed(new Set(['a', 'b'])), '["a","b"]');
  assert.deepEqual([...parseCollapsed(serializeCollapsed(folded))].sort(), ['gauges', 'plan']);
});

test('the folder light is orange when connected and says the other states the old button did, and is off with no folder', async () => {
  const { folderLight } = await import('./components/sessionColumnLogic.ts');
  assert.equal(folderLight('connected'), 'on');
  assert.equal(folderLight('syncing'), 'syncing');
  assert.equal(folderLight('not-uploaded'), 'pending');
  assert.equal(folderLight('needs-permission'), 'warn');
  assert.equal(folderLight('error'), 'error');
  assert.equal(folderLight('disconnected'), null);
  assert.equal(folderLight('something else'), null);
});

test('the scope reads "All" for the default, the knowledge base name when it is one the person can use, and a guest is offered no "All"', async () => {
  const { scopeLabel, scopeChoices } = await import('./components/sessionColumnLogic.ts');
  const allowed = [{ id: 'Affiliate_A', display_name: 'Affiliate A' }, { id: 'kb-1', display_name: "jack_admin's Knowledge Base" }];
  assert.equal(scopeLabel('All', allowed), 'All');
  assert.equal(scopeLabel('All', []), 'All');
  assert.equal(scopeLabel('kb-1', allowed), "jack_admin's Knowledge Base");
  assert.equal(scopeLabel('Mystery', allowed), 'Mystery');
  assert.equal(scopeLabel('', allowed), 'All');
  assert.deepEqual(scopeChoices(allowed, false).map(c => c.value), ['All', 'Affiliate_A', 'kb-1']);
  assert.deepEqual(scopeChoices(allowed, false)[0], { value: 'All', label: 'All' });
  assert.deepEqual(scopeChoices(allowed, true).map(c => c.value), ['Affiliate_A', 'kb-1']);
  assert.deepEqual(scopeChoices([], false), [{ value: 'All', label: 'All' }]);
});

test('the gauges in a few words: both, one, or none, kept between 0 and 100', async () => {
  const { gaugeSummary, LEVEL_COLOR } = await import('./components/sessionColumnLogic.ts');
  assert.equal(gaugeSummary({ fraction: 0.4 }, { fraction: 0.124 }), 'Context 40% · Usage 12%');
  assert.equal(gaugeSummary(null, { fraction: 0.5 }), 'Usage 50%');
  assert.equal(gaugeSummary({ fraction: 0.2 }, null), 'Context 20%');
  assert.equal(gaugeSummary(null, null), '');
  assert.equal(gaugeSummary({ fraction: 1.7 }, { fraction: -0.2 }), 'Context 100% · Usage 0%');
  for (const level of ['ok', 'notice', 'throttled', 'blocked']) assert.ok(LEVEL_COLOR[level], level);
});

test('the phone strip is the same controls as the column, and it never carries the folder', async () => {
  const { readFile } = await import('node:fs/promises');
  const rail = await readFile(new URL('./components/SessionRail.tsx', import.meta.url), 'utf8');
  const column = await readFile(new URL('./components/SessionColumn.tsx', import.meta.url), 'utf8');
  // one definition of each control, used by both
  assert.match(rail, /RepoFields/); assert.match(rail, /ScopeField/); assert.match(rail, /Gauge /);
  assert.match(column, /<RepoFields repo=\{repo\} \/>/); assert.match(column, /<ScopeField scope=\{scope\} \/>/);
  assert.doesNotMatch(rail, /folder/i);
  // the page hides the small ring and the repo button for either one, but the folder button only for the column
  const chat = await readFile(new URL('./pages/Chat.tsx', import.meta.url), 'utf8');
  assert.match(chat, /const sidePanel = columnVisible \|\| railVisible;/);
  // the message box never carries the repo button, the repo banner or the usage ring, on any screen: switching the column or the strip off just hides them
  assert.match(chat, /const compactScreen = !isEmbedded && !roomForColumn;/);
  assert.doesNotMatch(chat, /UsageRing/);
  assert.doesNotMatch(chat, /Set a target GitHub repo/);
  assert.doesNotMatch(chat, /showRepoBanner/);
  assert.doesNotMatch(chat, /className="repo-banner"/);
  assert.match(chat, /\{!columnVisible && \(<>\s*\{folderAvailable && \(/);
});

test('the Start runner button is offered only on Windows desktops, and opens one fixed link', async () => {
  const { canStartRunnerFromLink, RUNNER_START_LINK, runnerLine } = await import('./components/sessionColumnLogic.ts');
  assert.equal(RUNNER_START_LINK, 'saapp-runner://start');
  assert.ok(canStartRunnerFromLink('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36'));
  for (const other of ['Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)', 'Mozilla/5.0 (X11; Linux x86_64)', 'Mozilla/5.0 (Linux; Android 14; Pixel) Mobile', 'Mozilla/5.0 (Windows Phone 10.0; Android 4.2.1) Mobile', '']) assert.equal(canStartRunnerFromLink(other), false, other);
  assert.equal(runnerLine([{ id: 'r', name: 'PC', connected: false, last_seen: '2026-10-30T00:00:00Z', projects: [] }], new Date('2026-10-30T16:00:00Z')).tone, 'warn');
});

test('the runner row is wired to the button and the quick re-check after a press', async () => {
  const { readFile } = await import('node:fs/promises');
  const column = await readFile(new URL('./components/SessionColumn.tsx', import.meta.url), 'utf8');
  const chat = await readFile(new URL('./pages/Chat.tsx', import.meta.url), 'utf8');
  const hook = await readFile(new URL('./hooks/useRunnerPing.ts', import.meta.url), 'utf8');
  assert.match(column, /runner\.tone === 'warn' && runnerStart/);                          // only a runner that is set up but not answering
  assert.match(chat, /window\.location\.href = RUNNER_START_LINK/);
  assert.match(chat, /checkRunnerSoon\(\)/);
  assert.match(hook, /QUICK_MS = 3_000/);                                                   // it asks every few seconds for a while after the press
});

test('the Start runner button waits with a spinner, says so when the wait runs out, and is idle again once the runner has reported', async () => {
  const { runnerStartPhase, RUNNER_START_WAIT_MS } = await import('./components/sessionColumnLogic.ts');
  const seen = '2026-10-30T12:00:00Z';
  assert.equal(runnerStartPhase(null, seen), 'idle');                                          // never pressed
  assert.equal(runnerStartPhase({ seen, failed: false }, seen), 'starting');                  // pressed, nothing has reported since
  assert.equal(runnerStartPhase({ seen, failed: true }, seen), 'failed');                     // the wait ran out
  assert.equal(runnerStartPhase({ seen, failed: false }, '2026-10-30T12:00:40Z'), 'idle');    // the runner reported: it is back (the row says Runner connected)
  assert.equal(runnerStartPhase({ seen, failed: true }, '2026-10-30T13:00:00Z'), 'idle');     // a later outage starts clean
  assert.ok(RUNNER_START_WAIT_MS >= 30_000 && RUNNER_START_WAIT_MS <= 120_000);
  const { readFile } = await import('node:fs/promises');
  const column = await readFile(new URL('./components/SessionColumn.tsx', import.meta.url), 'utf8');
  assert.match(column, /sc-spinner/); assert.match(column, /disabled=\{runnerStart\.phase === 'starting'\}/); assert.match(column, /aria-live="polite"/);
});

test('the install hint uses the runner\'s own Python by full path, in a PowerShell and a Command Prompt form, not a bare saapp-runner', async () => {
  const { RUNNER_INSTALL_POWERSHELL, RUNNER_INSTALL_CMD } = await import('./components/sessionColumnLogic.ts');
  const tail = '\\.saapp-runner\\venv\\Scripts\\python.exe" -m saapp_runner install-service';       // one backslash each, in the string the person copies
  assert.equal(RUNNER_INSTALL_POWERSHELL, '& "$env:USERPROFILE' + tail);
  assert.equal(RUNNER_INSTALL_CMD, '"%USERPROFILE%' + tail);
  const { readFile } = await import('node:fs/promises');
  const column = await readFile(new URL('./components/SessionColumn.tsx', import.meta.url), 'utf8');
  assert.doesNotMatch(column, /<code>saapp-runner install-service<\/code>/);
  assert.match(column, /RUNNER_INSTALL_POWERSHELL/); assert.match(column, /RUNNER_INSTALL_CMD/);
});

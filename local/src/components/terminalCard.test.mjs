// Run with: node --test src/components/terminalCard.test.mjs   (from local/; Node 22.18+ runs the TypeScript module directly)
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync } from 'node:fs';
import {
  SEND_MAX_CHARS,
  SEND_TAIL_LINES,
  TERMINAL_PICK_KEY,
  DEFAULT_TAIL_LINES,
  POLL_INTERVAL_MS,
  IDLE_POLL_MS,
  LIVE_POLL_MS,
  canRun,
  canStop,
  commandHistory,
  formatDuration,
  isActiveStatus,
  pollDelay,
  readStoredPick,
  sendToSonicBlock,
  terminalChoices,
  writeStoredPick,
  rowOutcome,
  scrollbackOrder,
  shouldPoll,
  statusDot,
  statusLabel,
  stepHistory,
  stoppableJob,
  terminalTarget,
  toggleExpanded,
  trimOutputTail,
} from './terminalLogic.ts';

const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8');

test('each job status has a plain label, and unknown statuses are shown as they came', () => {
  assert.equal(statusLabel('running'), 'Running');
  assert.equal(statusLabel('ok'), 'Finished');
  assert.equal(statusLabel('failed'), 'Failed');
  assert.equal(statusLabel('timed_out'), 'Timed out');
  assert.equal(statusLabel('waiting_approval'), 'Waiting for approval');
  assert.equal(statusLabel('not run'), 'Not run');
  assert.equal(statusLabel('weird'), 'weird');
});

test('the status dot colour follows the outcome: bad for failures, neutral for unknown', () => {
  assert.equal(statusDot('running'), 'running');
  assert.equal(statusDot('ok'), 'ok');
  assert.equal(statusDot('failed'), 'bad');
  assert.equal(statusDot('timed_out'), 'bad');
  assert.equal(statusDot('stopped'), 'neutral');
  assert.equal(statusDot('waiting_approval'), 'waiting');
  assert.equal(statusDot('weird'), 'neutral');
});

test('only running and waiting jobs count as active', () => {
  assert.equal(isActiveStatus('running'), true);
  assert.equal(isActiveStatus('waiting_approval'), true);
  assert.equal(isActiveStatus('ok'), false);
  assert.equal(isActiveStatus('failed'), false);
  assert.equal(isActiveStatus('not run'), false);
});

test('output keeps only its last lines and leaves short text unchanged', () => {
  assert.equal(trimOutputTail(''), '');
  assert.equal(trimOutputTail('one\ntwo'), 'one\ntwo');
  assert.equal(trimOutputTail('a\nb\nc\nd', 2), 'c\nd');
  const long = Array.from({ length: 250 }, (_, i) => `line ${i}`).join('\n');
  const trimmed = trimOutputTail(long);
  assert.equal(DEFAULT_TAIL_LINES, 200);
  assert.equal(trimmed.split('\n').length, 200);
  assert.ok(trimmed.endsWith('line 249'));
  assert.ok(trimmed.startsWith('line 50\n'));
});

test('durations read as milliseconds, seconds, or minutes and seconds', () => {
  assert.equal(formatDuration(0.85), '850 ms');
  assert.equal(formatDuration(12.4), '12 s');
  assert.equal(formatDuration(125), '2m 05s');
  assert.equal(formatDuration(null), '');
  assert.equal(formatDuration(undefined), '');
  assert.equal(formatDuration(-1), '');
  assert.equal(formatDuration(Number.NaN), '');
});

test('toggling a row adds it when folded and removes it when open, without changing the input', () => {
  const start = new Set(['a']);
  const opened = toggleExpanded(start, 'b');
  assert.deepEqual([...opened].sort(), ['a', 'b']);
  assert.deepEqual([...start], ['a']);
  const folded = toggleExpanded(opened, 'a');
  assert.deepEqual([...folded], ['b']);
});

test('the card polls only while it is open and a listed job is still active', () => {
  assert.equal(POLL_INTERVAL_MS, 10000);
  assert.equal(shouldPoll(true, [{ status: 'running' }]), true);
  assert.equal(shouldPoll(true, [{ status: 'waiting_approval' }]), true);
  assert.equal(shouldPoll(false, [{ status: 'running' }]), false);
  assert.equal(shouldPoll(true, [{ status: 'ok' }, { status: 'failed' }]), false);
  assert.equal(shouldPoll(true, []), false);
});

test('the statuses the server really stores have plain labels, and a failed exit code is not "Finished"', () => {
  assert.equal(statusLabel('done'), 'Finished');
  assert.equal(statusLabel('cancelled'), 'Stopped');
  assert.equal(statusLabel('awaiting_approval'), 'Waiting for your OK');
  assert.equal(statusDot('awaiting_approval'), 'waiting');
  assert.deepEqual(rowOutcome({ status: 'done', exit_code: 0 }), { label: 'Finished', dot: 'ok' });
  assert.deepEqual(rowOutcome({ status: 'done', exit_code: 1 }), { label: 'Exited 1', dot: 'bad' });
  assert.deepEqual(rowOutcome({ status: 'done', exit_code: 1, timed_out: true }), { label: 'Timed out', dot: 'bad' });
  assert.deepEqual(rowOutcome({ status: 'failed' }), { label: 'Failed', dot: 'bad' });
});

test('a row waiting for the person has Run and Cancel, a running or queued one has Stop, and both keep the card polling', () => {
  assert.equal(canRun({ status: 'awaiting_approval' }), true);
  assert.equal(canRun({ status: 'running' }), false);
  assert.equal(canStop({ status: 'running' }), true);
  assert.equal(canStop({ status: 'approved' }), true);
  assert.equal(canStop({ status: 'done' }), false);
  assert.equal(shouldPoll(true, [{ status: 'awaiting_approval' }]), true);
  assert.equal(shouldPoll(true, [{ status: 'approved' }]), true);
  assert.equal(shouldPoll(true, [{ status: 'done' }, { status: 'not run' }]), false);
});

const runner = (id, names, extra = {}) => ({ id, connected: true, projects: names.map((name, i) => ({ id: `${id}-p${i}`, name })), ...extra });

test('the card talks to the project named like the open folder, else the only project of the only live runner, else nothing', () => {
  const laptop = runner('r1', ['saapp', 'notes']);
  assert.deepEqual(terminalTarget([laptop], 'Notes'), { runnerId: 'r1', projectId: 'r1-p1', projectName: 'notes' });          // case does not matter
  assert.equal(terminalTarget([laptop], 'something else'), null);                                        // two projects and no match: not guessing
  assert.deepEqual(terminalTarget([runner('r2', ['only'])], ''), { runnerId: 'r2', projectId: 'r2-p0', projectName: 'only' });
  assert.equal(terminalTarget([runner('r3', ['a']), runner('r4', ['b'])], ''), null);                    // two live runners: not guessing
  assert.equal(terminalTarget([runner('r5', ['a'], { connected: false })], 'a'), null);
  assert.equal(terminalTarget([runner('r6', ['a'], { commands: false })], 'a'), null);                   // too old to run commands
  assert.equal(terminalTarget(null, 'a'), null);
  assert.equal(terminalTarget([runner('r7', [])], 'a'), null);
});

test('the terminal is a blade opened from the top bar, drawn in the app shell, and the column no longer carries a card for it', () => {
  const chat = read('../pages/Chat.tsx');
  assert.match(chat, /const target = terminalTarget\(runnerList, workspace\.folderName \?\? ''\);\s*if \(target\) return \{ \.\.\.target, choices: undefined/);   // the open folder names a project: it wins
  assert.match(chat, /const choices = terminalChoices\(runnerList\);\s*if \(choices\.length === 0\) return null;/);                            // no connected project at all: no button
  assert.match(chat, /return \{ \.\.\.\(picked \?\? choices\[0\]\), choices \};/);                                                                  // the dropdown is still there after a pick
  assert.match(chat, /import \{ terminalChoices, terminalTarget, type TerminalChoice \} from '\.\.\/components\/terminalLogic';/);
  assert.match(chat, /\{terminal && \(\s*<button[^>]*onClick=\{\(\) => setShowTerminal\(open => !open\)\}/);   // the top-bar button, only while a runner is connected
  assert.match(chat, /<Icon name="terminal" size=\{18\} \/>/);
  assert.match(chat, /showTerminal && terminal && createPortal\(/);
  assert.match(chat, /document\.querySelector\('\.app-shell'\) \?\? document\.body/);               // not inside the chat page: it is a size container
  assert.match(chat, /useRunnerPing\(!isEmbedded && !isGuestPrincipal\(principal\)\)/);                  // asked whether or not the column shows
  assert.doesNotMatch(read('./SessionColumn.tsx'), /Terminal|terminal=/);
  const api = read('../api.ts');
  assert.match(api, /export const createRunnerCommand = /);
  assert.match(api, /'\/api\/runner\/jobs', \{ method: 'POST'/);
  assert.match(api, /^  createRunnerCommand,$/m);                       // on the exported api object, the way the blade calls it
  assert.match(api, /^  listCommandJobs,$/m);
  assert.match(read('./icons.tsx'), /terminal: \(/);
});

test('a typed command starts at once unless it changes things, which waits for Run and the stronger question', () => {
  const blade = read('./TerminalBlade.tsx');
  assert.match(blade, /job\.status === 'awaiting_approval' && !job\.needs_confirm\) await api\.approveRunnerJob\(job\.id\)/);
  assert.match(blade, /job\.needs_confirm && !window\.confirm\(/);
  assert.match(blade, /api\.approveRunnerJob\(job\.id, Boolean\(job\.needs_confirm\)\)/);
});

test('the blade closes on Escape, stops the running command on Ctrl+C only when one is running, and walks earlier commands with the arrows', () => {
  const blade = read('./TerminalBlade.tsx');
  assert.match(blade, /event\.key === 'Escape'\) onClose\(\)/);
  assert.match(blade, /event\.ctrlKey && event\.key\.toLowerCase\(\) === 'c' && running/);
  assert.match(blade, /selectionStart !== inputRef\.current\.selectionEnd/);        // with text selected, Ctrl+C still copies
  assert.match(blade, /'ArrowUp' \|\| event\.key === 'ArrowDown'/);
  assert.match(read('./__styles__/TerminalBlade.css'), /\.terminal-blade \{[^}]*position: fixed;[^}]*right: 0;/);
});

test('the scrollback reads oldest first, the arrows walk the earlier commands without repeats, and the poll pace follows what is running', () => {
  assert.deepEqual(scrollbackOrder([{ id: 'new' }, { id: 'old' }]).map((j) => j.id), ['old', 'new']);
  const jobs = [{ command_shown: 'pytest -q' }, { command_shown: 'git status' }, { command_shown: 'pytest -q' }, { command_shown: '  ' }];
  assert.deepEqual(commandHistory(jobs), ['pytest -q', 'git status']);
  assert.equal(stepHistory(2, -1, 'up'), 0);
  assert.equal(stepHistory(2, 0, 'up'), 1);
  assert.equal(stepHistory(2, 1, 'up'), 1);                        // the oldest is the end
  assert.equal(stepHistory(2, 0, 'down'), -1);                     // past the newest is the line being typed
  assert.equal(stepHistory(2, -1, 'down'), -1);
  assert.equal(stepHistory(0, -1, 'up'), -1);
  assert.equal(LIVE_POLL_MS, 3000);
  assert.equal(IDLE_POLL_MS, 10000);
  assert.equal(pollDelay([{ status: 'done' }]), IDLE_POLL_MS);
  assert.equal(pollDelay([{ status: 'done' }, { status: 'running' }]), LIVE_POLL_MS);
  assert.equal(stoppableJob([{ status: 'done' }, { status: 'approved' }])?.status, 'approved');
  assert.equal(stoppableJob([{ status: 'done' }]), undefined);
});

test('the dropdown lists every project of every connected runner that can run commands, labelled "runner: project"', () => {
  const runners = [
    { id: 'r1', name: 'laptop', connected: true, projects: [{ id: 'p1', name: 'saapp' }, { id: 'p2', name: 'notes' }] },
    { id: 'r2', name: 'desktop', connected: true, commands: false, projects: [{ id: 'p3', name: 'old' }] },       // too old to run commands
    { id: 'r3', name: 'tablet', connected: false, projects: [{ id: 'p4', name: 'away' }] },                          // not connected
    { id: 'r4', connected: true, projects: [{ id: 'p5', name: 'unnamed-runner-project' }] },
  ];
  assert.deepEqual(terminalChoices(runners).map((c) => [c.runnerId, c.projectId, c.label]), [['r1', 'p1', 'laptop: saapp'], ['r1', 'p2', 'laptop: notes'], ['r4', 'p5', 'r4: unnamed-runner-project']]);
  assert.deepEqual(terminalChoices(null), []);
});

test('the remembered project works without storage, and a storage that throws never breaks the blade', () => {
  assert.equal(TERMINAL_PICK_KEY, 'saapp.terminal.project');
  const memory = new Map();
  const storage = { getItem: (k) => memory.get(k) ?? null, setItem: (k, v) => memory.set(k, v) };
  assert.equal(readStoredPick(storage), null);
  writeStoredPick(storage, 'r1|p2');
  assert.equal(readStoredPick(storage), 'r1|p2');
  const broken = { getItem: () => { throw new Error('blocked'); }, setItem: () => { throw new Error('blocked'); } };
  assert.equal(readStoredPick(broken), null);
  assert.doesNotThrow(() => writeStoredPick(broken, 'x'));
  assert.equal(readStoredPick(null), null);
  assert.doesNotThrow(() => writeStoredPick(undefined, 'x'));
});

test('Send to Sonic builds an editable block with the command, its exit code and only the end of the output', () => {
  assert.equal(sendToSonicBlock('pytest -q', 1, 'one\ntwo\nthree'), 'Command: pytest -q\nExit code: 1\n```\none\ntwo\nthree\n```');
  assert.match(sendToSonicBlock('git status', null, 'x'), /Exit code: none/);
  const many = Array.from({ length: 300 }, (_, i) => `line ${i}`).join('\n');
  const tail = sendToSonicBlock('x', 0, many);
  assert.equal(SEND_TAIL_LINES, 200);
  assert.ok(tail.includes('line 299') && tail.includes('line 100') && !tail.includes('line 99\n'), 'the last 200 lines');
  const long = sendToSonicBlock('x', 0, 'a'.repeat(SEND_MAX_CHARS + 500));
  assert.equal(long.split('\n')[3].length, SEND_MAX_CHARS, 'at most 4,000 characters of output');
  const fenced = sendToSonicBlock('x', 0, 'before\n```\nsneaky\n```\nafter');
  assert.ok(fenced.includes('````'), 'a fence longer than any run of backticks in the output, so it cannot close the block early');
});

test('Send to Sonic only puts text in the message box: it never sends', () => {
  const chat = read('../pages/Chat.tsx');
  assert.match(chat, /const putInComposer = useCallback\(\(text: string\) => \{\s*setInput\(prev => \(prev\.trim\(\) \?/);
  assert.match(chat, /setInput\(prev => \(prev\.trim\(\) \? `\$\{prev\}[^`]*\$\{text\}` : text\)\);\s*inputRef\.current\?\.focus\(\);/);
  assert.match(chat, /onSendToChat=\{putInComposer\}/);
  assert.doesNotMatch(read('./TerminalBlade.tsx'), /handleSendMessage|sendChatMessage/);
});

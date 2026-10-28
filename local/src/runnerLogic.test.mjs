// Run with: node --test src/runnerLogic.test.mjs   (from local/; Node 22.18+ runs the TypeScript module directly)
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { OS_LABEL, detectOs, filesLine, headline, isFinal, minutesLeft, runHint, verdict } from './runnerLogic.ts';

const job = (over = {}) => ({ id: 'j1', runner_id: 'r1', check: 'tests', command_shown: 'pytest -q {files}', files: [], status: 'done', created_at: null, expires_at: null, finished_at: null, exit_code: 0, output_tail: '', truncated: false, timed_out: false, duration: 1.5, error: '', ...over });

test('only a check with an outcome is final, so the card stops asking the server about it', () => {
  for (const s of ['done', 'cancelled', 'expired', 'failed']) assert.equal(isFinal(s), true, s);
  for (const s of ['awaiting_approval', 'approved', 'running', '', 'nonsense']) assert.equal(isFinal(s), false, s);
});

test('each state of a check reads as one clear verdict', () => {
  assert.equal(verdict(job({ status: 'awaiting_approval', exit_code: null })), 'waiting');
  assert.equal(verdict(job({ status: 'approved', exit_code: null })), 'running');
  assert.equal(verdict(job({ status: 'running', exit_code: null })), 'running');
  assert.equal(verdict(job({ status: 'done', exit_code: 0 })), 'passed');
  assert.equal(verdict(job({ status: 'done', exit_code: 2 })), 'failed');
  assert.equal(verdict(job({ status: 'done', exit_code: null, timed_out: true })), 'timed_out');
  assert.equal(verdict(job({ status: 'cancelled', exit_code: null })), 'cancelled');
  assert.equal(verdict(job({ status: 'expired', exit_code: null })), 'not_run');
  assert.equal(verdict(job({ status: 'failed', exit_code: null })), 'error');
  assert.equal(verdict(job({ status: 'something-new' })), 'error');
});

test('the headline says what happened in plain words, with the exit code and time when there are some', () => {
  assert.equal(headline(job({ status: 'awaiting_approval', exit_code: null })), 'Waiting for you to press Run');
  assert.equal(headline(job({ status: 'approved', exit_code: null })), 'Approved. Waiting for your runner to pick it up...');
  assert.equal(headline(job({ status: 'running', exit_code: null })), 'Running on your machine...');
  assert.equal(headline(job()), 'Passed in 1.5 s');
  assert.equal(headline(job({ exit_code: 1, duration: 3.2 })), 'Failed (exit code 1) after 3.2 s');
  assert.equal(headline(job({ exit_code: 1, duration: null })), 'Failed (exit code 1)');
  assert.equal(headline(job({ exit_code: null, timed_out: true })), 'Timed out, so the result is unknown');
  assert.equal(headline(job({ status: 'cancelled', exit_code: null })), 'Cancelled');
  assert.equal(headline(job({ status: 'expired', exit_code: null, error: 'Nobody approved it in time.' })), 'Not run: Nobody approved it in time.');
  assert.equal(headline(job({ status: 'expired', exit_code: null, error: '' })), 'Not run: it expired');
  assert.equal(headline(job({ status: 'failed', exit_code: null, error: 'command not found: pytest' })), 'Could not run: command not found: pytest');
});

test('the files a check was limited to are listed, and none means it ran on everything', () => {
  assert.equal(filesLine(job({ files: ['tests/a.py', 'b.py'] })), 'tests/a.py, b.py');
  assert.equal(filesLine(job()), '');
  assert.equal(filesLine({}), '');
});

test('the starter script offered first matches the computer, and every system still has one', () => {
  assert.equal(detectOs('Win32', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'), 'windows');
  assert.equal(detectOs('MacIntel', 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'), 'mac');
  assert.equal(detectOs('iPhone', 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0)'), 'mac');
  assert.equal(detectOs('Linux x86_64', 'Mozilla/5.0 (X11; Linux x86_64)'), 'linux');
  assert.equal(detectOs('', ''), 'linux');
  assert.deepEqual(Object.keys(OS_LABEL).sort(), ['linux', 'mac', 'windows']);
});

test('the instruction for the downloaded file names the file and says what to expect', () => {
  const win = runHint('windows', 'saapp-runner-setup.cmd');
  assert.match(win, /Double-click saapp-runner-setup\.cmd/);
  assert.match(win, /More info, then Run anyway/);
  assert.match(runHint('mac', 'saapp-runner-setup.command'), /bash ~\/Downloads\/saapp-runner-setup\.command/);
  assert.match(runHint('linux', 'saapp-runner-setup.sh'), /bash ~\/Downloads\/saapp-runner-setup\.sh/);
});

test('a pairing code shows the minutes it has left, rounded up and never below zero', () => {
  const now = Date.parse('2026-10-27T12:00:00Z');
  assert.equal(minutesLeft('2026-10-27T12:10:00Z', now), 10);
  assert.equal(minutesLeft('2026-10-27T12:00:30Z', now), 1);
  assert.equal(minutesLeft('2026-10-27T11:00:00Z', now), 0);
  assert.equal(minutesLeft('not a date', now), 0);
});

test('this page tells the server it can show a check card, and shows one on the message that asked', () => {
  const api = readFileSync(new URL('./api.ts', import.meta.url), 'utf8');
  const chat = readFileSync(new URL('./pages/Chat.tsx', import.meta.url), 'utf8');
  assert.match(api, /runner_cards: runnerCards/);
  assert.match(chat, /payload\.event === 'runner_check'/);
  assert.match(chat, /runnerCheck: \{ job: payload\.job/);
  assert.match(chat, /<RunnerCheckCard/);
  assert.match(chat, /gitBranch,\s*true\s*\n\s*\)/);
});

test('the settings section is on the account page for signed-in people only', () => {
  const account = readFileSync(new URL('./pages/Account.tsx', import.meta.url), 'utf8');
  assert.match(account, /\{!guest && <RunnerSettingsCard \/>\}/);
});

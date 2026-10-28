// Run with: node --test src/runnerLogic.test.mjs   (from local/; Node 22.18+ runs the TypeScript module directly)
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { OS_LABEL, agoText, checkIsOn, detectOs, filesLine, headline, isFinal, isOlderVersion, canSelfUpdate, launchHint, minutesLeft, noChecksLine, notConnectedReason, parseVersion, requestSentence, requestsOpen, requestsToShow, runHint, statusSentence, updateNote, verdict, withAutoUpdate, withCheck, withFakeKeys, classSentence, isCommand, needsSecondStep, withReadOnlyAuto } from './runnerLogic.ts';

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

test('every check is on until it is turned off, and turning one off or on touches only that name', () => {
  const start = { disabled: [], fake_keys: true };
  assert.equal(checkIsOn(start, 'tests'), true);
  const off = withCheck(start, 'types', false);
  assert.deepEqual(off, { disabled: ['types'], fake_keys: true });
  assert.equal(checkIsOn(off, 'types'), false);
  assert.equal(checkIsOn(off, 'tests'), true);
  assert.deepEqual(withCheck(off, 'types', false).disabled, ['types']);          // turning it off twice does not list it twice
  assert.deepEqual(withCheck(off, 'types', true), start);
  assert.deepEqual(start.disabled, [], 'the old settings are not changed in place');
});

test('the fake key choice changes only itself', () => {
  const settings = { disabled: ['types'], fake_keys: true };
  assert.deepEqual(withFakeKeys(settings, false), { disabled: ['types'], fake_keys: false });
  assert.equal(settings.fake_keys, true);
});

test('a runner with no checks says why, depending on whether it has connected', () => {
  assert.match(noChecksLine(true), /No tests or type check was found in this project/);
  assert.match(noChecksLine(false), /once the runner has connected/);
});

test('the settings card saves a project\'s names and yes or no with its id, and the runner\'s own setting without one', () => {
  const card = readFileSync(new URL('./components/RunnerSettingsCard.tsx', import.meta.url), 'utf8');
  const api = readFileSync(new URL('./api.ts', import.meta.url), 'utf8');
  assert.match(card, /api\.updateRunnerSettings\(runner\.id, \{ project_id: project\.id, disabled: next\.disabled, fake_keys: next\.fake_keys \}\)/);
  assert.match(card, /api\.updateRunnerSettings\(runner\.id, \{ auto_update: on \}\)/);
  assert.match(card, /withCheck\(project\.settings, check\.name, event\.target\.checked\)/);
  assert.match(card, /withFakeKeys\(project\.settings, event\.target\.checked\)/);
  assert.match(card, /changeAutoUpdate\(runner, event\.target\.checked\)/);
  assert.match(api, /\/api\/runner\/runners\/\$\{encodeURIComponent\(id\)\}\/settings/);
});

test('a runner is out of date only when its version is older than the one the server asks for', () => {
  assert.equal(isOlderVersion('0.1.0', '0.1.1'), true);
  assert.equal(isOlderVersion('0.1.9', '0.2.0'), true);
  assert.equal(isOlderVersion('0.9.9', '1.0.0'), true);
  assert.equal(isOlderVersion('0.1.1', '0.1.1'), false);
  assert.equal(isOlderVersion('0.2.0', '0.1.9'), false);
  assert.equal(isOlderVersion('0.1.10', '0.1.9'), false);        // compared as numbers, not text
  for (const bad of ['', 'dev', '0.1', '0.1.0-beta', 'x.y.z']) {
    assert.equal(isOlderVersion(bad, '0.1.1'), false, bad);
    assert.equal(isOlderVersion('0.1.0', bad), false, bad);
  }
  assert.deepEqual(parseVersion(' 12.0.3 '), [12, 0, 3]);
});

test('the settings card shows the update note, the Update button only when it can be pressed, and the automatic tick', () => {
  const card = readFileSync(new URL('./components/RunnerSettingsCard.tsx', import.meta.url), 'utf8');
  assert.match(card, /updateNote\(runner\)!\.canPress/);
  assert.match(card, /api\.requestRunnerUpdate\(runner\.id\)/);
  assert.match(card, /withAutoUpdate\(item\.settings, on\)/);
  assert.match(card, /runner\.update\.pending/);
});

test('every status code the runner can send has a sentence, and ok or anything unknown says nothing', () => {
  for (const code of ['folder_missing', 'config_problem', 'no_checks_found', 'command_missing']) assert.ok(statusSentence(code).length > 40, code);
  for (const quiet of ['ok', '', 'made_up', '<script>alert(1)</script>', null, undefined]) assert.equal(statusSentence(quiet), '', String(quiet));
});

test('the codes the page knows are exactly the codes the server accepts', () => {
  const server = readFileSync(new URL('../../backend/utils/runner_jobs.py', import.meta.url), 'utf8');
  const listed = /RUNNER_STATUS_CODES = \(([^)]*)\)/.exec(server)[1].split(',').map(s => s.trim().replace(/"/g, ''));
  const logic = readFileSync(new URL('./runnerLogic.ts', import.meta.url), 'utf8');
  for (const code of listed.filter(c => c !== 'ok')) assert.ok(logic.includes(`  ${code}: `), `${code} has no sentence`);
});

test('how long ago a runner was seen is said in plain words', () => {
  const now = Date.parse('2026-10-28T12:00:00Z');
  const ago = seconds => new Date(now - seconds * 1000).toISOString();
  assert.equal(agoText(ago(20), now), 'just now');
  assert.equal(agoText(ago(10 * 60), now), '10 minutes ago');
  assert.equal(agoText(ago(60 * 60), now), '1 hour ago');
  assert.equal(agoText(ago(3 * 3600), now), '3 hours ago');
  assert.equal(agoText(ago(3 * 86400), now), '3 days ago');
  assert.equal(agoText(new Date(now + 5000).toISOString(), now), 'just now');          // a clock a little ahead does not say "in the future"
  for (const bad of [null, undefined, '', 'not a date']) assert.equal(agoText(bad, now), '');
});

test('a runner that is not connected says when it was last seen, or that it never connected', () => {
  const now = Date.parse('2026-10-28T12:00:00Z');
  assert.match(notConnectedReason(new Date(now - 3 * 3600 * 1000).toISOString(), now), /Last seen 3 hours ago.*asleep or offline/);
  assert.match(notConnectedReason(null, now), /hasn't connected yet/);
});

test('the card shows the status sentence only while connected, the reason only while not, and a missing program next to its check', () => {
  const card = readFileSync(new URL('./components/RunnerSettingsCard.tsx', import.meta.url), 'utf8');
  assert.match(card, /runner\.connected && statusSentence\(runner\.status\)/);
  assert.match(card, /!runner\.connected && .*notConnectedReason\(runner\.last_seen, now\)/);
  assert.match(card, /check\.ready === false/);
});

test('a runner started by hand is told it stops with its window, and one started at login or by an older runner says nothing', () => {
  assert.match(launchHint('hand'), /started by hand.*install-service/);
  for (const quiet of ['service', '', 'made_up', null, undefined]) assert.equal(launchHint(quiet), '', String(quiet));
  const card = readFileSync(new URL('./components/RunnerSettingsCard.tsx', import.meta.url), 'utf8');
  assert.match(card, /runner\.connected && launchHint\(runner\.launch\)/);
});

const upd = (over = {}) => ({ version: '0.1.1', connected: true, launch: 'service', update: { wanted: '0.1.2', available: true, pending: false }, ...over });

test('an up to date runner has no update note, and the note says what will happen for each situation', () => {
  assert.equal(updateNote(upd({ update: { wanted: '0.1.1', available: false, pending: false } })), null);
  assert.equal(updateNote({ version: '0.1.1', connected: true, launch: '' }), null);
  const ready = updateNote(upd());
  assert.equal(ready.canPress, true);
  assert.match(ready.text, /\(0\.1\.2\).*restarts by itself/);
  assert.match(updateNote(upd({ launch: 'hand' })).text, /started by hand.*start it again/);
  const waiting = updateNote(upd({ update: { wanted: '0.1.2', available: true, pending: true } }));
  assert.equal(waiting.canPress, false);
  assert.match(waiting.text, /Updating to 0\.1\.2.*when it is idle/);
  const stopped = updateNote(upd({ connected: false }));
  assert.equal(stopped.canPress, false);
  assert.match(stopped.text, /Start this runner first/);
});

test('a runner too old to update itself is told to run the setup file again, and cannot press Update', () => {
  const old = updateNote(upd({ version: '0.1.0' }));
  assert.equal(old.canPress, false);
  assert.match(old.text, /can't update itself.*setup file/);
  assert.equal(canSelfUpdate('0.1.1'), true);
  assert.equal(canSelfUpdate('0.2.0'), true);
  assert.equal(canSelfUpdate('0.1.0'), false);
  assert.equal(canSelfUpdate(''), false);
  assert.equal(canSelfUpdate('dev'), false);
});

test('automatic updates change only that one setting', () => {
  const settings = { disabled: ['types'], fake_keys: true, auto_update: false };
  assert.deepEqual(withAutoUpdate(settings, true), { disabled: ['types'], fake_keys: true, auto_update: true });
  assert.equal(settings.auto_update, false);
});


test('a request to add a project says what happened in plain words, and an added one needs none', () => {
  const asked = (state) => requestSentence({ name: 'my-app', state });
  assert.match(asked('waiting'), /Waiting for your computer/);
  assert.match(asked('asked'), /window on your computer.*Nothing is added until you choose it there/);
  assert.match(asked('declined'), /said no on your computer.*not added/);
  assert.match(asked('no_picker'), /can't show a folder window.*saapp-runner add-project --root/);
  assert.match(asked('error'), /could not be added.*home folder/);
  assert.match(asked('expired'), /Nobody answered the window/);
  for (const state of ['added', 'made_up', '']) assert.equal(asked(state), '', state);
  for (const state of ['waiting', 'asked', 'declined', 'no_picker', 'error', 'expired']) assert.ok(asked(state).includes('my-app'), state);
});

test('the page keeps looking while a request waits and shows only the ones that did not end in a project', () => {
  const requests = [{ id: '1', name: 'a', state: 'added' }, { id: '2', name: 'b', state: 'declined' }, { id: '3', name: 'c', state: 'asked' }];
  assert.equal(requestsOpen(requests), true);
  assert.equal(requestsOpen([{ state: 'added' }, { state: 'declined' }, { state: 'expired' }]), false);
  assert.equal(requestsOpen([]), false);
  assert.deepEqual(requestsToShow(requests).map(r => r.id), ['2', '3']);
});

test('the settings card sends only a folder name when asking for a project, lists each project with its own checks, and keeps looking while a request is open', () => {
  const card = readFileSync(new URL('./components/RunnerSettingsCard.tsx', import.meta.url), 'utf8');
  const api = readFileSync(new URL('./api.ts', import.meta.url), 'utf8');
  assert.match(card, /api\.addRunnerProject\(runner\.id, name\)/);
  assert.match(card, /runner\.projects\.map\(project =>/);
  assert.match(card, /project\.checks\.map\(check =>/);
  assert.match(card, /requestsOpen\(runner\.requests\)/);
  assert.match(card, /api\.removeRunnerProject\(runner\.id, project\.id\)/);
  assert.match(card, /disabled=\{!runner\.connected\}/);                                           // a project can only be asked for while the runner is there to show the window
  assert.match(api, /body: JSON\.stringify\(\{ name \}\)/);                                         // the request carries a name and nothing else
  assert.doesNotMatch(api.slice(api.indexOf('export const addRunnerProject'), api.indexOf('export const removeRunnerProject')), /path/i);
});

// ---- command lines ---------------------------------------------------------------------------------------------------------------------------

const cmd = (over = {}) => job({ kind: 'command', check: 'command', command_shown: 'git status', command_class: 'read', command_notes: [], auto_approved: false, needs_confirm: false, ...over });

test('a command line reads as a command, and a named check does not', () => {
  assert.equal(isCommand(cmd()), true);
  assert.equal(isCommand(job()), false);
  assert.equal(isCommand({}), false);
});

test('a command finishes or exits with a code instead of passing or failing like a check', () => {
  assert.match(headline(cmd({ status: 'done', exit_code: 0, duration: 0.4 })), /^Finished in 0\.4 s$/);
  assert.match(headline(cmd({ status: 'done', exit_code: 2, duration: 1 })), /^Exited with code 2 after 1 s$/);
  assert.match(headline(cmd({ status: 'failed', exit_code: null, error: 'You said no in the window on your computer.' })), /^Did not run: You said no/);
  assert.match(headline(cmd({ status: 'approved', exit_code: null, auto_approved: true })), /started by itself/);
  assert.match(headline(cmd({ status: 'approved', exit_code: null })), /^Approved/);
  assert.equal(headline(cmd({ status: 'awaiting_approval', exit_code: null })), 'Waiting for you to press Run');
  assert.match(headline(job({ status: 'done', exit_code: 0 })), /^Passed/);                         // a named check keeps its words
});

test('only a command that changes things needs the second, stronger confirmation', () => {
  assert.equal(needsSecondStep(cmd({ command_class: 'change', needs_confirm: true })), true);
  assert.equal(needsSecondStep(cmd({ command_class: 'change', needs_confirm: undefined })), true);
  assert.equal(needsSecondStep(cmd({ command_class: 'run' })), false);
  assert.equal(needsSecondStep(cmd({ command_class: 'read' })), false);
  assert.equal(needsSecondStep(job({ command_class: 'change' })), false);                           // a named check never does
});

test('each class says in words what it can do', () => {
  assert.match(classSentence('read'), /does not change anything/);
  assert.match(classSentence('run'), /runs a program/i);
  assert.match(classSentence('change'), /edit files.*git.*network/);
  assert.equal(classSentence(''), '');
});

test('the read-only choice is the computers own and leaves its other choice alone', () => {
  assert.deepEqual(withReadOnlyAuto({ auto_update: true, read_only_auto: false }, true), { auto_update: true, read_only_auto: true });
  assert.deepEqual(withAutoUpdate({ auto_update: false, read_only_auto: true }, true), { auto_update: true, read_only_auto: true });
});

test('the card asks twice for a change, sends the confirmation only on the second yes, and the page never sends a command', () => {
  const card = readFileSync(new URL('./components/RunnerCheckCard.tsx', import.meta.url), 'utf8');
  const api = readFileSync(new URL('./api.ts', import.meta.url), 'utf8');
  const settings = readFileSync(new URL('./components/RunnerSettingsCard.tsx', import.meta.url), 'utf8');
  assert.match(card, /Review and run\.\.\./);
  assert.match(card, /act\('run', true\)/);                                                          // the confirmation is sent by the second button only
  assert.equal((card.match(/act\('run'\)/g) ?? []).length, 1);                                       // and the plain Run is shown only for a line that needs no second step
  assert.match(card, /kind === 'waiting' && !second/);
  assert.match(api, /JSON\.stringify\(\{ confirmed \}\)/);
  assert.match(settings, /read_only_auto: on/);
  assert.match(settings, /runner\.commands &&/);                                                      // the choice is shown only for a runner that can run commands
});

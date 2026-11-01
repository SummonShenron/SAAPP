// Run with: node --test src/stepsLogic.test.mjs   (from local/; Node 22.18+ runs the TypeScript module directly)
// The steps card: a short list of command lines Sonic proposes and the person runs with a click each (components/CommandStepsCard.tsx, backend/utils/command_steps.py).
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { IDLE, anyChanges, canRun, hasNews, isFinished, klassLabel, needsConfirm, nextToRun, runAllQuestion, runFromJob, summaryText } from './stepsLogic.ts';

const steps = [
  { id: 's1', title: 'Stage the changes', line: 'git add -A', klass: 'change', notes: [] },
  { id: 's2', title: 'Commit', line: "git commit -m 'feat: x'", klass: 'change', notes: [] },
  { id: 's3', title: 'Check the log', line: 'git log -1', klass: 'read', notes: [] },
];
const job = (over = {}) => ({ id: 'j1', status: 'done', exit_code: 0, timed_out: false, output_tail: '', error: '', ...over });

test('a step that changes things needs the stronger confirmation; one that only looks does not', () => {
  assert.equal(needsConfirm(steps[0]), true);
  assert.equal(needsConfirm(steps[2]), false);
  assert.equal(anyChanges(steps), true);
  assert.equal(anyChanges([steps[2]]), false);
  assert.deepEqual(['read', 'run', 'change'].map(klassLabel), ['looks only', 'runs a program', 'changes things']);
});

test('a job becomes a step state: done, failed, cancelled, could not run, or still going (a job waiting for approval counts as going)', () => {
  assert.equal(runFromJob(job()).state, 'done');
  assert.equal(runFromJob(job({ exit_code: 1, output_tail: 'boom' })).state, 'failed');
  assert.equal(runFromJob(job({ timed_out: true, exit_code: null })).state, 'failed');
  assert.match(runFromJob(job({ timed_out: true, exit_code: null })).message, /Timed out/);
  assert.equal(runFromJob(job({ status: 'cancelled' })).state, 'cancelled');
  assert.deepEqual([runFromJob(job({ status: 'failed', error: 'The runner refused it: x' })).state, runFromJob(job({ status: 'failed', error: 'The runner refused it: x' })).message], ['error', 'The runner refused it: x']);
  assert.equal(runFromJob(job({ status: 'expired' })).state, 'error');
  for (const status of ['awaiting_approval', 'approved', 'running']) assert.equal(runFromJob(job({ status })).state, 'running');
});

test('"Run all" goes to the next step only while every step before it went well', () => {
  assert.equal(nextToRun(steps, {})?.id, 's1');
  assert.equal(nextToRun(steps, { s1: { state: 'done' } })?.id, 's2');
  assert.equal(nextToRun(steps, { s1: { state: 'done' }, s2: { state: 'done' } })?.id, 's3');
  assert.equal(nextToRun(steps, { s1: { state: 'done' }, s2: { state: 'failed' } }), null, 'a failure stops the run');
  assert.equal(nextToRun(steps, { s1: { state: 'running' } }), null, 'nothing is started while one runs');
  assert.equal(nextToRun(steps, { s1: { state: 'done' }, s2: { state: 'done' }, s3: { state: 'done' } }), null);
  assert.equal(nextToRun(steps, { s1: { state: 'cancelled' } }), null);
});

test('a step can be run by hand when nothing is running; a step that failed can be tried again but one that is done is not offered again', () => {
  assert.equal(canRun(steps[1], {}), true, 'any step, in any order');
  assert.equal(canRun(steps[1], { s1: { state: 'running' } }), false, 'one at a time');
  assert.equal(canRun(steps[0], { s1: { state: 'done' } }), false);
  assert.equal(canRun(steps[0], { s1: { state: 'failed' } }), true);
  assert.equal(canRun(steps[0], { s1: { state: 'cancelled' } }), true);
  assert.equal(canRun(steps[0], { s1: { state: 'error' } }), true);
});

test('what the person is asked before "Run all" says how many change things and that it stops at a failure', () => {
  assert.match(runAllQuestion(steps), /^Run all 3 steps in order\? 2 of them change things/);
  assert.match(runAllQuestion(steps), /asks again in its own window/);
  assert.match(runAllQuestion(steps), /stops at the first step that fails/);
  assert.doesNotMatch(runAllQuestion([steps[2]]), /change things/);
});

test('the message for Sonic lists every step, with the end of the output only for a failure', () => {
  const text = summaryText(steps, { s1: { state: 'done' }, s2: { state: 'failed', exitCode: 1, tail: 'a'.repeat(900) + 'nothing to commit' } });
  const lines = text.split('\n');
  assert.equal(lines[0], 'How the steps went:');
  assert.equal(lines[1], '1. Stage the changes (git add -A): done');
  assert.match(lines[2], /^2\. Commit \(git commit -m 'feat: x'\): failed \(exit code 1\)$/);
  assert.match(lines[3], /^….*nothing to commit$/);
  assert.ok(lines[3].length <= 402, 'only the end of a long output');
  assert.equal(lines[4], '3. Check the log (git log -1): not run');
});

test('a stopped, refused or still running step is said plainly, and there is news once any step has an outcome', () => {
  const text = summaryText(steps, { s1: { state: 'cancelled' }, s2: { state: 'error', message: 'The runner refused it: x' }, s3: { state: 'running' } });
  assert.match(text, /1\. Stage the changes \(git add -A\): I stopped it/);
  assert.match(text, /2\. Commit .*: did not run \(The runner refused it: x\)/);
  assert.match(text, /3\. Check the log .*: still running/);
  assert.equal(hasNews(steps, {}), false);
  assert.equal(hasNews(steps, { s1: { state: 'running' } }), false);
  assert.equal(hasNews(steps, { s1: { state: 'done' } }), true);
  assert.equal(isFinished(IDLE) || isFinished(undefined), false);
});

test('the card runs a step through the same command job as the Terminal and never claims anything on its own', () => {
  const card = readFileSync(new URL('./components/CommandStepsCard.tsx', import.meta.url), 'utf8');
  assert.match(card, /api\.createRunnerCommand\(runner\.id, projectId, step\.line\)/);
  assert.match(card, /api\.approveRunnerJob\(created\.id, confirmed\)/);
  assert.match(card, /api\.getRunnerJob\(/);
  assert.match(card, /api\.cancelRunnerJob\(/);
  assert.match(card, /onTell\(summaryText\(steps, runs\)\)/);
  assert.doesNotMatch(card, /dangerouslySetInnerHTML/);
  const chat = readFileSync(new URL('./pages/Chat.tsx', import.meta.url), 'utf8');
  assert.match(chat, /payload\.event === 'command_steps'/);
  assert.match(chat, /<CommandStepsCard key=\{msg\.commandSteps\.steps\.map\(s => s\.id\)\.join\('-'\) \+ msg\.id\} steps=\{msg\.commandSteps\.steps\} runner=\{msg\.commandSteps\.runner\} onTell=\{putInComposer\} \/>/);
});

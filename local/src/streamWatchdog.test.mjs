// Run with: node --test src/streamWatchdog.test.mjs   (from local/; Node 22.18+ runs the TypeScript module directly)
// The page's side of the chat stream's keepalive: a stream that promised one and then goes quiet is given up on, so the page does not stay busy until a reload. Also the Apply report that tells the page
// when the turn it waits on is gone.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { IDLE_LIMIT_MS, StreamIdleError, isKeepaliveLine, readOrGiveUp, waitAtMost } from './streamWatchdog.ts';

const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8');
const never = () => new Promise(() => {});

test('four missed keepalives is the limit, and a server comment is recognised, data and blank lines are not', () => {
  assert.equal(IDLE_LIMIT_MS, 60_000);
  assert.equal(isKeepaliveLine(': ready'), true);
  assert.equal(isKeepaliveLine(': keepalive'), true);
  assert.equal(isKeepaliveLine('  : keepalive  '), true);
  assert.equal(isKeepaliveLine('data: {"event":"trace"}'), false);
  assert.equal(isKeepaliveLine(''), false);
});

test('a read that stays quiet past the limit is given up on: the reader is cancelled and the result is a StreamIdleError', async () => {
  let cancelled = 0;
  await assert.rejects(readOrGiveUp(never(), 20, () => { cancelled += 1; }), (error) => error instanceof StreamIdleError && error.name === 'StreamIdleError');
  assert.equal(cancelled, 1);
});

test('a read that answers in time passes through untouched and never cancels anything', async () => {
  let cancelled = 0;
  const value = await readOrGiveUp(new Promise((resolve) => setTimeout(() => resolve({ done: false, value: 'chunk' }), 5)), 200, () => { cancelled += 1; });
  assert.deepEqual(value, { done: false, value: 'chunk' });
  await new Promise((resolve) => setTimeout(resolve, 250));          // well past the limit: the timer was cleared
  assert.equal(cancelled, 0);
});

test('a read that fails passes its own error through, not an idle error', async () => {
  await assert.rejects(readOrGiveUp(Promise.reject(new Error('network')), 200, () => {}), /network/);
});

test('a stream that never promised a keepalive is not watched at all: the read comes back as it is', async () => {
  const slow = new Promise((resolve) => setTimeout(() => resolve('late but fine'), 60));
  assert.equal(readOrGiveUp(slow, null, () => assert.fail('no timer without a promise')), slow);
  assert.equal(await slow, 'late but fine');
});

test('the chat reader watches only a stream that said `: ready`, and an idle stream ends as an interrupted send, which has Retry', () => {
  const api = read('./api.ts');
  assert.match(api, /let keepalive = false;/);
  assert.match(api, /if \(isKeepaliveLine\(trimmed\)\) keepalive = true;/);
  assert.match(api, /readOrGiveUp\(reader\.read\(\), keepalive \? IDLE_LIMIT_MS : null, \(\) => \{ void reader\.cancel\(\)/);
  assert.match(api, /throw isStop\(err\) \? err : new ChatSendError\('interrupted'\);/);                     // any non-stop failure, an idle stream included
  assert.match(read('./chatErrors.ts'), /interrupted/);
});

test('the Apply report returns what the server did with it, so the page knows when nobody is waiting', () => {
  const api = read('./api.ts');
  assert.match(api, /export type EditDecisionResult = 'recorded' \| 'no_waiter' \| 'error';/);
  assert.match(api, /if \(!res\.ok\) return 'error';/);
  assert.match(api, /body && body\.status === 'no_waiter' \? 'no_waiter' : 'recorded'/);
});

test('only the first Apply or Decline for the proposal a turn waits on can end that turn, and an Undo never does', () => {
  const chat = read('./pages/Chat.tsx');
  assert.match(chat, /if \(proposal\.awaiting\) awaitedProposalRef\.current = proposal\.proposal_id;/);
  assert.match(chat, /const awaited = outcome !== 'undone' && awaitedProposalRef\.current === proposalId;/);
  assert.match(chat, /if \(awaited\) awaitedProposalRef\.current = null;/);                      // a second press cannot end a healthy turn
  assert.match(chat, /if \(awaited && result === 'no_waiter'\) \{\s*lostTurnRef\.current = true;\s*abortControllerRef\.current\?\.abort\(\);/);
  assert.match(chat, /onDecision=\{\(proposalId, outcome, reason\) => localEditActionsRef\.current\.decide\(proposalId, outcome, reason\)\}/);
});

test('a turn ended this way says why and that the change was applied, and both flags are reset when any turn ends', () => {
  const chat = read('./pages/Chat.tsx');
  assert.match(chat, /The server stopped waiting for this answer, so the turn ended\. Your change was applied\. Press Continue to carry on\./);
  assert.match(chat, /abortControllerRef\.current = null;\s*awaitedProposalRef\.current = null;\s*lostTurnRef\.current = false;\s*setLoading\(false\);/);
});

test('work that finishes in time gives its value, and nothing is reported late', async () => {
  let late = 0;
  const value = await waitAtMost(new Promise((resolve) => setTimeout(() => resolve('synced'), 5)), 200, () => { late += 1; });
  assert.equal(value, 'synced');
  await new Promise((resolve) => setTimeout(resolve, 250));
  assert.equal(late, 0);
});

test('work that hangs gives undefined after the limit, says so once, and is left to finish in the background', async () => {
  let late = 0;
  let finished = false;
  const slow = new Promise((resolve) => setTimeout(() => { finished = true; resolve('late'); }, 80));
  assert.equal(await waitAtMost(slow, 20, () => { late += 1; }), undefined);
  assert.equal(late, 1);
  assert.equal(finished, false);                                  // the caller moved on while the work was still running
  await slow;
  assert.equal(finished, true);                                   // and the work was not cancelled
});

test('work that fails gives undefined and never throws, because a sync problem must not block the message', async () => {
  assert.equal(await waitAtMost(Promise.reject(new Error('network')), 200), undefined);
  assert.equal(await waitAtMost(Promise.reject(new Error('network')), 200, () => assert.fail('a failure is not lateness')), undefined);
});

test('the page sends the message without waiting longer than 20 s for the folder sync or 5 s for the git branch', () => {
  const chat = read('./pages/Chat.tsx');
  assert.match(chat, /const PRE_SEND_SYNC_MS = 20_000;/);
  assert.match(chat, /const PRE_SEND_BRANCH_MS = 5_000;/);
  assert.match(chat, /await waitAtMost\(workspace\.ensureSynced\(\), PRE_SEND_SYNC_MS,/);
  assert.match(chat, /const gitBranch = \(await waitAtMost\(workspace\.currentGitBranch\(\), PRE_SEND_BRANCH_MS\)\) \?\? null;/);
  assert.doesNotMatch(chat, /await workspace\.ensureSynced\(\);/);            // never a bare wait again
});

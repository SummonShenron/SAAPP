// Run with: node --test src/utils/authReady.test.mjs   (from local/; Node 22.18+ runs the TypeScript module directly)
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { DENIED_AFTER, GIVE_UP_AFTER_MS, retryDelay, waitUntilAuthenticated } from './authReady.ts';

/** A clock the test moves forward by the sleeps it is asked for, so no test really waits. */
function fakeTime() {
  let t = 0;
  const slept = [];
  return { now: () => t, sleep: async ms => { slept.push(ms); t += ms; }, slept };
}

function script(...results) {
  let i = 0;
  return async () => {
    const next = results[Math.min(i++, results.length - 1)];
    if (next instanceof Error) throw next;
    return next;
  };
}

test('it is ready as soon as the session checks out, without waiting', async () => {
  const time = fakeTime();
  assert.equal(await waitUntilAuthenticated({ check: script('ready'), ...time }), 'ready');
  assert.deepEqual(time.slept, []);
});

test('a session that is not there yet is retried until it is, with quick tries first', async () => {
  const time = fakeTime();
  const outcome = await waitUntilAuthenticated({ check: script('retry', 'retry', 'retry', 'ready'), ...time });
  assert.equal(outcome, 'ready');
  assert.deepEqual(time.slept, [400, 800, 1500]);
});

test('a check that throws (a dropped connection) counts as not yet, not as a failure', async () => {
  const time = fakeTime();
  assert.equal(await waitUntilAuthenticated({ check: script(new Error('network'), 'ready'), ...time }), 'ready');
});

test('refusals in a row are believed, but a single one is not', async () => {
  const time = fakeTime();
  assert.equal(await waitUntilAuthenticated({ check: script('denied', 'ready'), ...time }), 'ready');
  assert.equal(await waitUntilAuthenticated({ check: script('denied'), ...fakeTime() }), 'denied');
  let tries = 0;
  const counted = async () => { tries++; return 'denied'; };
  await waitUntilAuthenticated({ check: counted, ...fakeTime() });
  assert.equal(tries, DENIED_AFTER);
});

test('a refusal followed by "not yet" starts the count again', async () => {
  const time = fakeTime();
  const outcome = await waitUntilAuthenticated({ check: script('denied', 'denied', 'retry', 'denied', 'denied', 'ready'), ...time });
  assert.equal(outcome, 'ready');
});

test('it gives up after the time allowed and says so', async () => {
  const time = fakeTime();
  const outcome = await waitUntilAuthenticated({ check: script('retry'), ...time });
  assert.equal(outcome, 'timeout');
  assert.ok(time.now() >= GIVE_UP_AFTER_MS && time.now() < GIVE_UP_AFTER_MS + 5_000);
});

test('it stops at once when the page that asked has gone away', async () => {
  const time = fakeTime();
  let calls = 0;
  const outcome = await waitUntilAuthenticated({ check: async () => { calls++; return 'retry'; }, isCancelled: () => calls >= 2, ...time });
  assert.equal(outcome, 'cancelled');
  assert.equal(calls, 2);
});

test('the screen is told how each unfinished try went', async () => {
  const time = fakeTime();
  const seen = [];
  await waitUntilAuthenticated({ check: script('retry', 'denied', 'ready'), onTry: (attempt, last) => seen.push([attempt, last]), ...time });
  assert.deepEqual(seen, [[0, 'retry'], [1, 'denied']]);
});

test('the delay grows and then stays at four seconds', () => {
  assert.deepEqual([0, 1, 2, 3, 4, 5, 50].map(retryDelay), [400, 800, 1500, 2500, 4000, 4000, 4000]);
  assert.equal(retryDelay(-3), 400);
});

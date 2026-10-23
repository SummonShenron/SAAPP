import assert from 'node:assert/strict';
import { test } from 'node:test';
import { SyncRequestError, isTransient, restorableStamps, withRetry } from './localWorkspaceCore.ts';

const noWait = async () => {};

test('a dropped connection and a busy or restarting server are worth retrying; a refusal is not', () => {
  assert.equal(isTransient(new TypeError('Failed to fetch')), true);
  for (const status of [408, 425, 429, 500, 502, 503, 504]) assert.equal(isTransient(new SyncRequestError('x', status)), true, String(status));
  assert.equal(isTransient(new SyncRequestError('no answer')), true);
  for (const status of [400, 401, 403, 404, 409, 413, 422]) assert.equal(isTransient(new SyncRequestError('x', status)), false, String(status));
  assert.equal(isTransient(new Error('something else')), false);
});

test('a transient failure is tried again and the upload carries on', async () => {
  let calls = 0;
  const result = await withRetry(async () => {
    calls += 1;
    if (calls < 3) throw new SyncRequestError('busy', 503);
    return 'done';
  }, { sleep: noWait });
  assert.equal(result, 'done');
  assert.equal(calls, 3);
});

test('it gives up after the last try and throws the last error', async () => {
  let calls = 0;
  await assert.rejects(withRetry(async () => { calls += 1; throw new SyncRequestError('down', 502); }, { attempts: 3, sleep: noWait }), /down/);
  assert.equal(calls, 3);
});

test('a refusal is thrown at once, without a second try', async () => {
  let calls = 0;
  await assert.rejects(withRetry(async () => { calls += 1; throw new SyncRequestError('replaced', 409); }, { sleep: noWait }), /replaced/);
  assert.equal(calls, 1);
});

test('it waits a little longer each time, and the last wait repeats', async () => {
  const waits = [];
  await assert.rejects(withRetry(async () => { throw new TypeError('offline'); }, { attempts: 4, delaysMs: [100, 300], sleep: async ms => { waits.push(ms); } }));
  assert.deepEqual(waits, [100, 300, 300]);
});

test('success on the first try waits for nothing', async () => {
  const waits = [];
  assert.equal(await withRetry(async () => 7, { sleep: async ms => { waits.push(ms); } }), 7);
  assert.deepEqual(waits, []);
});

const saved = { folder: 'my-repo', serverCount: 561, stamps: [['a.py', '10:1'], ['b.py', '20:2']] };

test('after a reload the remembered stamps are used only if the server still holds exactly what that upload left', () => {
  const stamps = restorableStamps(saved, 'my-repo', { connected: true, file_count: 561 });
  assert.deepEqual([...stamps], [['a.py', '10:1'], ['b.py', '20:2']]);
});

test('a server that lost its copy, or holds another one, means starting from scratch', () => {
  assert.equal(restorableStamps(saved, 'my-repo', { connected: false }), null);
  assert.equal(restorableStamps(saved, 'my-repo', { connected: true, file_count: 560 }), null);
  assert.equal(restorableStamps(saved, 'my-repo', { connected: true }), null);
  assert.equal(restorableStamps(saved, 'my-repo', null), null);
});

test('a different folder, or nothing remembered, is never trusted', () => {
  assert.equal(restorableStamps(saved, 'other-repo', { connected: true, file_count: 561 }), null);
  assert.equal(restorableStamps(null, 'my-repo', { connected: true, file_count: 561 }), null);
  assert.equal(restorableStamps({ ...saved, stamps: 'broken' }, 'my-repo', { connected: true, file_count: 561 }), null);
});

// Run with: node --test src/chatErrors.test.mjs   (from local/; Node 22.18+ runs the TypeScript module directly)
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { ChatSendError, describeChatError, kindForStatus, safeDetail, toChatSendError } from './chatErrors.ts';

test('each status maps to a kind', () => {
  const expected = { 401: 'session', 403: 'forbidden', 429: 'rate_limited', 400: 'bad_request', 413: 'bad_request', 422: 'bad_request', 502: 'starting', 503: 'starting', 504: 'starting', 500: 'server', 501: 'server', 404: 'unknown' };
  for (const [status, kind] of Object.entries(expected)) assert.equal(kindForStatus(Number(status)), kind, status);
});

test('every kind gets its own message, and a 401, a 500, a dropped network and a mid-answer drop all differ', () => {
  const kinds = ['session', 'forbidden', 'bad_request', 'rate_limited', 'starting', 'server', 'network', 'interrupted', 'unknown'];
  const messages = kinds.map(kind => describeChatError(new ChatSendError(kind)).message);
  assert.equal(new Set(messages).size, kinds.length);
});

test('no message ever names vectors, allocation or the local engine, whatever the cause', () => {
  const kinds = ['session', 'forbidden', 'bad_request', 'rate_limited', 'starting', 'server', 'network', 'interrupted', 'unknown'];
  for (const kind of kinds) {
    for (const status of [undefined, 401, 413, 500, 503]) {
      const view = describeChatError(new ChatSendError(kind, { status }));
      assert.doesNotMatch(view.message + view.details, /vector|allocation|engine|assertion/i, `${kind} ${status}`);
    }
  }
});

test('the unknown case says it is unknown and does not guess a component', () => {
  const view = describeChatError(new ChatSendError('unknown'));
  assert.match(view.message, /not known/);
  assert.equal(view.retryable, true);
});

test('only causes that retrying could fix offer a retry', () => {
  const retryable = { session: false, forbidden: false, bad_request: false, rate_limited: true, starting: true, server: true, network: true, interrupted: true, unknown: true };
  for (const [kind, expected] of Object.entries(retryable)) assert.equal(describeChatError(new ChatSendError(kind)).retryable, expected, kind);
});

test('a 413 is told apart from other bad requests', () => {
  assert.match(describeChatError(new ChatSendError('bad_request', { status: 413 })).message, /too large/);
  assert.doesNotMatch(describeChatError(new ChatSendError('bad_request', { status: 400 })).message, /too large/);
});

test('the details line carries the status, the kind and the server\'s own words, and nothing else', () => {
  const view = describeChatError(new ChatSendError('server', { status: 500, detail: 'Database unavailable' }));
  assert.equal(view.details, 'HTTP 500 · server · Database unavailable');
  assert.equal(describeChatError(new ChatSendError('network')).details, 'no response · network');
  assert.equal(describeChatError(new ChatSendError('interrupted')).details, 'stream dropped · interrupted');
});

test('a plain-string server detail is kept, cleaned and bounded', () => {
  assert.equal(safeDetail(500, { detail: 'Something\nbroke   here' }), 'Something broke here');
  assert.equal(safeDetail(500, { detail: 'x'.repeat(500) }).length, 160);
  assert.equal(safeDetail(403, { detail: '' }), undefined);
});

test('a 422 never shows its body, because validation errors echo what the person sent', () => {
  const echo = { detail: [{ loc: ['body', 'question'], msg: 'too long', input: 'MY PRIVATE MESSAGE' }] };
  assert.equal(safeDetail(422, echo), undefined);
  assert.equal(safeDetail(422, { detail: 'MY PRIVATE MESSAGE' }), undefined);
  assert.equal(safeDetail(500, echo), undefined); // a list is not a plain string either
});

test('a thrown TypeError is a network failure, an existing ChatSendError is kept, and anything else is unknown', () => {
  assert.equal(toChatSendError(new TypeError('Failed to fetch')).kind, 'network');
  const original = new ChatSendError('starting', { status: 503 });
  assert.equal(toChatSendError(original), original);
  assert.equal(toChatSendError(new Error('boom')).kind, 'unknown');
  assert.equal(toChatSendError('a string').kind, 'unknown');
});

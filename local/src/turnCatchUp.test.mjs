// Run with: npm test (from local/)
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync } from 'node:fs';
import { BUSY_NOTICE, DROPPED_NOTICE, shouldCatchUp, waitForTurn, withNotice } from './turnCatchUp.ts';

test('a dropped stream or a busy conversation means the turn may still be running; other failures do not', () => {
  assert.equal(shouldCatchUp({ kind: 'interrupted' }), true);
  assert.equal(shouldCatchUp({ kind: 'unknown', status: 409 }), true);
  for (const err of [{ kind: 'network' }, { kind: 'server', status: 500 }, { kind: 'session', status: 401 }, null, undefined]) assert.equal(shouldCatchUp(err), false);
});

function clock() {
  let t = 0;
  return { now: () => t, sleep: async ms => { t += ms; } };
}

test('it asks until the turn is no longer running', async () => {
  const answers = [true, true, false];
  const c = clock();
  const asked = [];
  const result = await waitForTurn(async () => { asked.push(c.now()); return answers.shift(); }, { intervalMs: 3000, maxMs: 60000, ...c });
  assert.equal(result, 'finished');
  assert.deepEqual(asked, [0, 3000, 6000]);
});

test('a failed ask is not an answer: it asks again, and gives up after the limit', async () => {
  const c = clock();
  let calls = 0;
  const result = await waitForTurn(async () => { calls += 1; if (calls % 2) throw new Error('offline'); return null; }, { intervalMs: 1000, maxMs: 5000, ...c });
  assert.equal(result, 'timeout');
  assert.equal(calls, 6);                                     // 0,1,2,3,4,5 s: asked at the limit once more, then stopped
});

test('the notice goes after what already arrived, or stands alone', () => {
  assert.equal(withNotice('Half an answer', DROPPED_NOTICE), `Half an answer\n\n${DROPPED_NOTICE}`);
  assert.equal(withNotice('', BUSY_NOTICE), BUSY_NOTICE);
  assert.equal(withNotice(undefined, BUSY_NOTICE), BUSY_NOTICE);
});

test('the chat page sends Stop to the server, catches up after a dropped stream, and checks a running turn when it opens a conversation', () => {
  const chat = readFileSync(new URL('./pages/Chat.tsx', import.meta.url), 'utf8');
  assert.match(chat, /const stopGeneration = \(\) => \{\s*void api\.stopChat\(sessionIdRef\.current\)\.catch\(\(\) => \{[^}]*\}\);\s*abortControllerRef\.current\?\.abort\(\);/);
  assert.match(chat, /if \(shouldCatchUp\(sendError\)\) \{/);
  assert.match(chat, /void catchUpRef\.current\(sessionId\);/);
  assert.match(chat, /api\.getTurnStatus\(sessionId\)[\s\S]{0,400}STILL_WORKING_NOTICE/);
  const api = readFileSync(new URL('./api.ts', import.meta.url), 'utf8');
  assert.match(api, /\/api\/chat\/stop/);
  assert.match(api, /\/api\/chat\/turn-status\?session_id=\$\{encodeURIComponent\(sessionId\)\}/);
});

test('the layout keeps the chat mounted outside the loading boundary, with the theme typed as the chat page needs', () => {
  const layout = readFileSync(new URL('./components/Layout.tsx', import.meta.url), 'utf8');
  assert.match(layout, /theme: "sonic" \| "shadow";/);
  assert.match(layout, /<\/Suspense>\s*\{\/\*[^*]*\*\/\}\s*\{chatOpened && \(/);
  assert.match(layout, /display: onChat \? "contents" : "none"/);
  const app = readFileSync(new URL('./App.tsx', import.meta.url), 'utf8');
  assert.doesNotMatch(app, /<ChatPage/);
});

// Run with: node --experimental-test-module-mocks --test src/pages/feedbackStamp.test.mjs   (from local/)
// The Sonic Health feedback page shows thumbs by how much the turn mattered, by use of a case from Sonic's own record, and by situation fact (backend/utils/reply_stamp.py `summarize`).
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

const read = name => readFileSync(new URL(name, import.meta.url), 'utf8').replace(/\r\n/g, '\n');
const page = read('./SonicHealthFeedback.tsx');
const api = read('../api.ts');

test('the page types carry the three new tables, optional because an older server does not send them', () => {
  assert.match(api, /stakes\?: \{ label: string; positive: number; negative: number \}\[\];/);
  assert.match(api, /record\?: \{ label: string; positive: number; negative: number \}\[\];/);
  assert.match(api, /facts\?: \{ id: string; positive: number; negative: number \}\[\];/);
});

test('the page shows each table with words for every value the server can send, and says so when there is nothing yet', () => {
  for (const key of ['safety', 'emotional', 'checkin', 'companion', 'identity', 'frustration', 'none']) assert.match(page, new RegExp(String.raw`const STAKES_LABELS[^\n]*[{,] ${key}:`), `a label for stakes ${key}`);
  for (const key of ['none', 'offered', 'cited']) assert.match(page, new RegExp(String.raw`const RECORD_LABELS[^\n]*[{,] ${key}:`), `a label for record ${key}`);
  assert.match(page, /<VoiceTable rows=\{feedback\.by_voice\.stakes\} first="Turn"/);
  assert.match(page, /<VoiceTable rows=\{feedback\.by_voice\.record\} first="Record"/);
  assert.match(page, /<VoiceTable rows=\{feedback\.by_voice\.facts\} first="Fact"/);
  assert.match(page, /!feedback\.by_voice\.stakes \|\| feedback\.by_voice\.stakes\.length === 0/);          // an older server sends none of them: the page says so instead of failing
  assert.match(page, /feedback\.by_voice\.stamped > 0 && \(/);
});

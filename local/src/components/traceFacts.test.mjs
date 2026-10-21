// Run with: node --experimental-test-module-mocks --test src/components/traceFacts.test.mjs   (from local/; Node 22.18+ runs the TypeScript module directly)
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { cleanFacts, mergeFacts, recordFacts, toggleExpanded, toolStepLabel } from './traceFacts.ts';

test('tool steps get plain-words titles, browser tools share one, unknown tools get a generic one, no tool means no label', () => {
  assert.equal(toolStepLabel('web_search'), 'Searched the web');
  assert.equal(toolStepLabel('read_gmail_message'), 'Checked your email');
  assert.equal(toolStepLabel('list_google_calendar_events'), 'Checked your calendar');
  assert.equal(toolStepLabel('browser_click'), 'Used the browser');
  assert.equal(toolStepLabel('something_new'), 'Used a tool');
  assert.equal(toolStepLabel(undefined), null);
  assert.equal(toolStepLabel(''), null);
  assert.equal(toolStepLabel('read_repo_file', 'main.tsx'), 'Read main.tsx');
  assert.equal(toolStepLabel('read_repo_file'), 'Read a file');
  assert.equal(toolStepLabel('browser_click', '  '), 'Used the browser');
  assert.equal(toolStepLabel(undefined, 'ignored'), null);
});

const step = (id, stepId) => ({ id, stepId, title: 'Working...', detail: '', status: 'active' });

test('facts that arrive BEFORE their step is drawn are merged when the step appears', () => {
  let byId = recordFacts({}, 'abc', ['Tool: read_repo_file', 'Took: 0.20 s']);
  let steps = [];
  assert.deepEqual(mergeFacts(steps, byId), []);
  steps = [...steps, step('s1', 'abc')];
  assert.deepEqual(mergeFacts(steps, byId)[0].facts, ['Tool: read_repo_file', 'Took: 0.20 s']);
});

test('facts that arrive AFTER their step land on that step only', () => {
  const steps = [step('s1', 'abc'), step('s2', 'def')];
  const byId = recordFacts({}, 'def', ['Tool: search_code']);
  const merged = mergeFacts(steps, byId);
  assert.equal(merged[0].facts, undefined);
  assert.deepEqual(merged[1].facts, ['Tool: search_code']);
});

test('a step with no step id, or facts for a step that never appears, are left alone', () => {
  const steps = [step('s1', undefined)];
  const byId = recordFacts({}, 'ghost', ['Tool: x']);
  assert.equal(mergeFacts(steps, byId), steps);
});

test('merging twice with the same facts does not rebuild the steps', () => {
  const byId = recordFacts({}, 'abc', ['Tool: x']);
  const once = mergeFacts([step('s1', 'abc')], byId);
  assert.equal(mergeFacts(once, byId), once);
});

test('a missing id or empty facts record nothing', () => {
  const empty = {};
  assert.equal(recordFacts(empty, undefined, ['x']), empty);
  assert.equal(recordFacts(empty, 'abc', []), empty);
  assert.equal(recordFacts(empty, 'abc', 'not a list'), empty);
});

test('facts are capped at 10 lines of 200 characters and only strings are kept', () => {
  const cleaned = cleanFacts([...Array.from({ length: 30 }, (_, i) => `line ${i}`), 5, null, '   ']);
  assert.equal(cleaned.length, 10);
  const long = cleanFacts(['x'.repeat(500)]);
  assert.equal(long[0].length, 200);
  assert.deepEqual(cleanFacts([null, 3, '']), []);
});

test('toggling opens then closes one step and leaves the others as they were', () => {
  const opened = toggleExpanded(new Set(['a']), 'b');
  assert.deepEqual([...opened].sort(), ['a', 'b']);
  const closed = toggleExpanded(opened, 'a');
  assert.deepEqual([...closed], ['b']);
});

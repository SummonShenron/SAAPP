// Run with: node --experimental-test-module-mocks --test src/components/traceFacts.test.mjs   (from local/; Node 22.18+ runs the TypeScript module directly)
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { cleanExcerpt, cleanFacts, mergeExcerpts, mergeFacts, recordExcerpt, recordFacts, toggleExpanded, toolStepLabel } from './traceFacts.ts';

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

// ---- what a read step read ------------------------------------------------------------------------------------------------------------------

test('an excerpt is kept to a short path and 1000 characters, and anything that is not a read excerpt with text is dropped', () => {
  assert.deepEqual(cleanExcerpt({ kind: 'read', path: 'a.py', text: 'print(1)', truncated: false }), { kind: 'read', path: 'a.py', text: 'print(1)', truncated: false });
  const long = cleanExcerpt({ kind: 'read', path: 'p'.repeat(900), text: 'ab '.repeat(2000) });
  assert.equal(long.text.length, 1000);
  assert.equal(long.path.length, 300);
  assert.equal(long.truncated, true);
  for (const bad of [null, undefined, 'text', 5, [], { kind: 'search', text: 'x' }, { kind: 'read' }, { kind: 'read', text: '' }, { kind: 'read', text: '  ' }, { kind: 'read', text: 5 }]) {
    assert.equal(cleanExcerpt(bad), null, JSON.stringify(bad));
  }
});

test('an excerpt is recorded by step id, and the same map comes back when there is nothing to record', () => {
  const none = {};
  assert.equal(recordExcerpt(none, undefined, { kind: 'read', text: 'x' }), none);
  assert.equal(recordExcerpt(none, 'abc', { kind: 'search', text: 'x' }), none);
  const one = recordExcerpt(none, 'abc', { kind: 'read', path: 'a.py', text: 'hi' });
  assert.deepEqual(Object.keys(one), ['abc']);
  assert.notEqual(one, none);
});

test('an excerpt that arrives before or after its step lands on that step only, and nothing else about the step changes', () => {
  const step = (id, stepId) => ({ id, stepId, title: 'Read a.py', detail: '', status: 'complete', facts: ['Tool: read_repo_file'] });
  const byId = recordExcerpt({}, 'abc', { kind: 'read', path: 'a.py', text: 'hi' });
  assert.deepEqual(mergeExcerpts([], byId), []);
  const merged = mergeExcerpts([step('s1', 'abc'), step('s2', 'other'), step('s3', undefined)], byId);
  assert.equal(merged[0].excerpt.text, 'hi');
  assert.equal(merged[1].excerpt, undefined);
  assert.equal(merged[2].excerpt, undefined);
  assert.deepEqual(merged[0].facts, ['Tool: read_repo_file']);
  const steps = [step('s1', 'abc')];
  const again = mergeExcerpts(mergeExcerpts(steps, byId), byId);
  assert.equal(again[0].excerpt, byId.abc);                                   // merging twice keeps the same object, so the panel does not redraw
});

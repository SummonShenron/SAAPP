// Run with: node --experimental-test-module-mocks --test src/components/traceFacts.test.mjs   (from local/; Node 22.18+ runs the TypeScript module directly)
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { cleanExcerpt, searchSummary, splitAroundTerm, cleanFacts, mergeExcerpts, mergeFacts, recordExcerpt, recordFacts, toggleExpanded, toolStepLabel } from './traceFacts.ts';

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


// ---- what a search found ---------------------------------------------------------------------------------------------------------------------

const search = (over = {}) => ({ kind: 'search', term: 'timestamp', files: [{ path: 'a.ts', matches: [{ line: 3, text: 'const timestamp = 1' }] }, { path: 'b.py', matches: [{ line: 9, text: 'Timestamp()' }] }], total_files: 2, total_matches: 2, hidden: 0, truncated: false, ...over });

test('a search excerpt keeps its files and lines, to the same limits as the server, and drops anything that is not one', () => {
  assert.deepEqual(cleanExcerpt(search()), search());
  const big = { kind: 'search', term: 't'.repeat(400), files: Array.from({ length: 20 }, (_, n) => ({ path: `f${n}.py`, matches: Array.from({ length: 9 }, (_, k) => ({ line: k + 1, text: 'x'.repeat(500) })) })), total_files: 20 };
  const cleaned = cleanExcerpt(big);
  assert.equal(cleaned.files.length, 10);
  assert.ok(cleaned.files.every(f => f.matches.length <= 5));
  assert.equal(cleaned.files.reduce((n, f) => n + f.matches.length, 0), 25);
  assert.ok(cleaned.files[0].matches[0].text.length === 160 && cleaned.term.length === 120 && cleaned.total_files === 20);
  for (const bad of [{ kind: 'search' }, { kind: 'search', files: [] }, { kind: 'search', files: 'x' }, { kind: 'search', files: [{ path: '' }] }, { kind: 'search', files: [null, 5, { path: 7 }] }]) assert.equal(cleanExcerpt(bad), null, JSON.stringify(bad));
  const odd = cleanExcerpt(search({ files: [{ path: 'a.ts', matches: [{ line: 0, text: 'zero' }, { line: '2', text: 'str' }, { line: 4, text: 9 }, null, { line: 5, text: 'ok' }] }], total_files: 'many', hidden: -1 }));
  assert.deepEqual(odd.files[0].matches, [{ line: 5, text: 'ok' }]);
  assert.equal(odd.total_files, 1);
  assert.equal(odd.hidden, 0);
});

test('a read excerpt still works beside it, and the two kinds are told apart', () => {
  assert.equal(cleanExcerpt({ kind: 'read', path: 'a.py', text: 'hi' }).kind, 'read');
  assert.equal(cleanExcerpt(search()).kind, 'search');
  assert.equal(cleanExcerpt({ kind: 'other', text: 'x' }), null);
});

test('the searched text is marked wherever it appears, ignoring case, and the line is returned whole', () => {
  const parts = splitAroundTerm('const Timestamp = timestamp + 1', 'timestamp');
  assert.deepEqual(parts.map(p => p.text).join(''), 'const Timestamp = timestamp + 1');
  assert.deepEqual(parts.filter(p => p.hit).map(p => p.text), ['Timestamp', 'timestamp']);
  assert.deepEqual(splitAroundTerm('nothing here', 'timestamp'), [{ text: 'nothing here', hit: false }]);
  assert.deepEqual(splitAroundTerm('abc', ''), [{ text: 'abc', hit: false }]);
  assert.deepEqual(splitAroundTerm('abc', '   '), [{ text: 'abc', hit: false }]);
  assert.deepEqual(splitAroundTerm('timestamp', 'timestamp'), [{ text: 'timestamp', hit: true }]);
  assert.equal(splitAroundTerm('<b>timestamp</b>', 'timestamp').map(p => p.text).join(''), '<b>timestamp</b>');                 // markup in a line is only ever text
});

test('the summary says how many matches in how many files, and what was left out', () => {
  assert.equal(searchSummary(search({ total_matches: 29, total_files: 7 })), '29 matches in 7 files');
  assert.equal(searchSummary(search({ total_matches: 1, total_files: 1 })), '1 match in 1 file');
  assert.equal(searchSummary(search({ truncated: true, hidden: 2 })), '2 matches in 2 files (showing the first ones; 2 files that hold secrets left out)');
  assert.equal(searchSummary(search({ hidden: 1 })), '2 matches in 2 files (1 file that holds secrets left out)');
  assert.equal(searchSummary({ kind: 'search', term: 'x', files: [{ path: 'a.py', matches: [] }, { path: 'b.py', matches: [] }], total_files: 2, total_matches: 2, hidden: 0, truncated: false }), '2 files');          // a search that only lists files
});

test('the steps show a search as plain text and mark the term with mark elements, never as HTML, and the panel and the steps under a reply draw them with the same component', () => {
  const panel = readFileSync(new URL('./TraceSteps.tsx', import.meta.url), 'utf8');
  assert.match(readFileSync(new URL('./TracePanel.tsx', import.meta.url), 'utf8'), /<TraceSteps [^>]*steps=\{steps\} running=\{running\} \/>/);
  assert.match(readFileSync(new URL('./StepsDisclosure.tsx', import.meta.url), 'utf8'), /<TraceSteps steps=\{steps\} running=\{false\}/);       // under a reply; a running turn draws the same component with the ring as its newest node
  assert.match(readFileSync(new URL('./StepsDisclosure.tsx', import.meta.url), 'utf8'), /<TraceSteps [\s\S]*?running spinner/);
  assert.match(panel, /excerpt\.kind === 'search' && <SearchResults excerpt=\{excerpt\} \/>/);
  assert.match(panel, /<mark key=\{index\} className="trace-search-hit">\{part\.text\}<\/mark>/);
  assert.doesNotMatch(panel, /dangerouslySetInnerHTML/);
  assert.match(panel, /splitAroundTerm\(match\.text, excerpt\.term\)/);
});

test('a finished turn keeps its steps with every one marked complete, and the label counts them', async () => {
  const { finishedSteps, stepsLabel } = await import('./traceFacts.ts');
  const steps = [{ id: 'a', title: 'Read', detail: '', status: 'complete' }, { id: 'b', title: 'Writing', detail: 'x', status: 'active', facts: ['1 file'] }];
  const done = finishedSteps(steps);
  assert.deepEqual(done.map(s => s.status), ['complete', 'complete']);
  assert.deepEqual(done[1].facts, ['1 file']);
  assert.equal(done[0], steps[0]);                                   // an already finished step is not copied
  assert.equal(steps[1].status, 'active');                           // and the live list is not changed
  assert.deepEqual(finishedSteps([]), []);
  assert.equal(stepsLabel(1), '1 step');
  assert.equal(stepsLabel(7), '7 steps');
});

test('the newest reply keeps its steps open until the next request, an older one is folded, and a choice the person made always wins', async () => {
  const { stepsAreOpen } = await import('./traceFacts.ts');
  assert.equal(stepsAreOpen(null, true), true);
  assert.equal(stepsAreOpen(null, false), false);          // the next request made it an older reply
  assert.equal(stepsAreOpen(false, true), false);          // closed by hand stays closed
  assert.equal(stepsAreOpen(true, false), true);           // opened by hand stays open
});

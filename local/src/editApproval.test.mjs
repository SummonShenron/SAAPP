// Run with: node --test src/editApproval.test.mjs   (from local/; Node 22.18+ runs the TypeScript module directly)
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { AUTO_BURST_LIMIT, MAX_AUTO_FILES, decideAutoApply, levelHint, levelLabel, loadLevel, nextLevel, saveLevel } from './editApproval.ts';

const file = (path, extra = {}) => ({ path, kind: 'edit', diff: '', additions: 1, deletions: 0, ...extra });
const proposal = (paths, extra = {}) => ({
  proposal_id: 'p1', summary: '', folder: 'demo',
  files: paths.map(p => file(p)),
  edits: paths.map(p => ({ type: 'replace', path: p, old_string: 'a', new_string: 'b' })),
  ...extra,
});

test('the levels go round in a circle and each has words for the menu', () => {
  assert.equal(nextLevel('ask'), 'sonic');
  assert.equal(nextLevel('sonic'), 'session');
  assert.equal(nextLevel('session'), 'ask');
  for (const level of ['ask', 'sonic', 'session']) {
    assert.match(levelLabel(level), /^Edit approval:/);
    assert.ok(levelHint(level).length > 20);
  }
});

test('ask never applies anything by itself', () => {
  assert.deepEqual(decideAutoApply('ask', proposal(['.sonic/notes.md']), [], 0), { auto: false });
});

test('the sonic level applies only proposals that stay inside .sonic/', () => {
  assert.equal(decideAutoApply('sonic', proposal(['.sonic/notes.md', '.sonic/tests/test_a.py']), [], 0).auto, true);
  const outside = decideAutoApply('sonic', proposal(['.sonic/notes.md', 'src/app.py']), [], 0);
  assert.equal(outside.auto, false);
  assert.match(outside.reason, /outside \.sonic\//);
  assert.equal(decideAutoApply('sonic', proposal(['.sonicfake/notes.md']), [], 0).auto, false);
  assert.equal(decideAutoApply('sonic', proposal(['.sonic/../src/app.py']), [], 0).auto, false);
  assert.equal(decideAutoApply('sonic', proposal([]), [], 0).auto, false);
});

test('the session level applies anything, except what always asks', () => {
  assert.equal(decideAutoApply('session', proposal(['src/app.py']), [], 0).auto, true);
  const word = proposal(['Resume (new).docx'], { files: [file('Resume (new).docx', { format: 'docx', kind: 'create' })], edits: [{ type: 'create_docx', path: 'Resume (new).docx', document: {} }] });
  for (const level of ['sonic', 'session']) assert.match(decideAutoApply(level, word, [], 0).reason, /Word document/);
  const many = proposal(Array.from({ length: MAX_AUTO_FILES + 1 }, (_, n) => `src/f${n}.py`));
  assert.match(decideAutoApply('session', many, [], 0).reason, /more than 8 files/);
});

test('a burst of automatic changes makes it ask, and old ones stop counting', () => {
  const now = 1_000_000;
  const burst = Array.from({ length: AUTO_BURST_LIMIT }, (_, n) => now - n * 1000);
  assert.match(decideAutoApply('session', proposal(['src/app.py']), burst, now).reason, /too many automatic changes/);
  const old = Array.from({ length: AUTO_BURST_LIMIT }, () => now - 5 * 60_000);
  assert.equal(decideAutoApply('session', proposal(['src/app.py']), old, now).auto, true);
});

test('sonic is remembered, session is forgotten with the tab, and a broken store means ask', () => {
  const makeStore = () => { const data = new Map(); return { getItem: k => (data.has(k) ? data.get(k) : null), setItem: (k, v) => data.set(k, v), removeItem: k => data.delete(k) }; };
  globalThis.localStorage = makeStore();
  globalThis.sessionStorage = makeStore();
  assert.equal(loadLevel(), 'ask');
  saveLevel('sonic');
  assert.equal(loadLevel(), 'sonic');
  saveLevel('session');
  assert.equal(loadLevel(), 'session');
  assert.equal(globalThis.localStorage.getItem('sonic_edit_approval'), null);
  globalThis.sessionStorage = makeStore();            // a new tab
  assert.equal(loadLevel(), 'ask');
  saveLevel('ask');
  assert.equal(loadLevel(), 'ask');
  const broken = { getItem() { throw new Error('blocked'); }, setItem() { throw new Error('blocked'); }, removeItem() { throw new Error('blocked'); } };
  globalThis.localStorage = broken;
  globalThis.sessionStorage = broken;
  assert.equal(loadLevel(), 'ask');
  saveLevel('session');
});

import assert from 'node:assert/strict';
import { test } from 'node:test';
import { CLAUDE_KEEP, SyncRequestError, isTransient, parentName, restorableStamps, skipsDirectory, skipsFilesIn, withRetry } from './localWorkspaceCore.ts';

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

// ---- `.claude`: only its skills folder is sent (the same cases as backend/tests/test_runner_files_rules.py CLAUDE_CASES; the server and the runner must agree) ----

const SKIP_DIRS = new Set(['node_modules', '.git', 'dist', 'build', '__pycache__', '.venv', 'venv', '.next', 'coverage', 'chroma_db', 'index-db']);

/** Walks a path the way scanFolder does: each folder is entered unless skipped, then the file itself must not sit directly in `.claude`. (Ends in a file name.) */
function wouldSend(path) {
  const parts = path.split('/');
  let prefix = '';
  for (const folder of parts.slice(0, -1)) {
    if (skipsDirectory(parentName(prefix), folder, SKIP_DIRS)) return false;
    prefix += `${folder}/`;
  }
  const name = parts[parts.length - 1];
  if (skipsFilesIn(parentName(prefix))) return false;
  return !/^\.env($|\.)/.test(name);                                       // (file names are filtered separately in the app: a secret is never sent)
}

test('only the skills folder of .claude is sent: its worktrees and settings are not', () => {
  const cases = [
    ['.claude/skills/finish/SKILL.md', true], ['.claude/skills/finish/scripts/run.py', true], ['.claude/skills/SKILL.md', true], ['pkg/.claude/skills/x/SKILL.md', true],
    ['.claude/worktrees/abc/app.py', false], ['.claude/settings.local.json', false], ['.claude/launch.json', false], ['.claude/skillset/x.md', false],
    ['.claude/skills/x/node_modules/y.js', false], ['.claude/skills/x/.env', false], ['src/app.py', true], ['node_modules/x.js', false],
  ];
  for (const [path, sendable] of cases) assert.equal(wouldSend(path), sendable, path);
});

test('the rule names the folder a scan is in and keeps exactly the skills folder', () => {
  assert.equal(parentName(''), '');
  assert.equal(parentName('a/'), 'a');
  assert.equal(parentName('a/b/.claude/'), '.claude');
  assert.deepEqual([...CLAUDE_KEEP], ['skills']);
  assert.equal(skipsDirectory('.claude', 'skills', SKIP_DIRS), false);
  assert.equal(skipsDirectory('.claude', 'worktrees', SKIP_DIRS), true);
  assert.equal(skipsDirectory('', '.claude', SKIP_DIRS), false);              // the folder itself is entered; what is inside it is decided one level down
  assert.equal(skipsDirectory('skills', 'node_modules', SKIP_DIRS), true);     // the usual skipped names still apply inside the skills folder
  assert.equal(skipsFilesIn('.claude'), true);
  assert.equal(skipsFilesIn('skills'), false);
});


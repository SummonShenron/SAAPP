// The local git import (importCommit.ts), checked against REAL git: a repository is built with git, Sonic's commit is made in a clone of it and turned into the same bundle the server sends,
// the import is run over the first repository's .git folder, and git itself is asked whether the result is right (status, fsck, log, the index). Skipped when git is not installed.
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { test } from 'node:test';
import { parseIndex, serializeIndex } from './gitIndex.ts';
import { applyImport, planImport } from './importCommit.ts';
import { objectId, parseIsoDate, serializeCommit, serializeTree } from './objects.ts';

const haveGit = (() => {
  try {
    execFileSync('git', ['--version'], { stdio: 'ignore' });
    return true;
  } catch {
    return false;
  }
})();
const gitTest = (name, fn) => test(name, { skip: !haveGit && 'git is not installed' }, fn);

const ENV = {
  ...process.env, GIT_AUTHOR_NAME: 'Sonic Person', GIT_AUTHOR_EMAIL: 'person@example.com', GIT_COMMITTER_NAME: 'Sonic Person', GIT_COMMITTER_EMAIL: 'person@example.com',
  GIT_AUTHOR_DATE: '2026-10-25T20:10:21+0000', GIT_COMMITTER_DATE: '2026-10-25T20:10:21+0000', GIT_CONFIG_GLOBAL: '/dev/null', GIT_CONFIG_NOSYSTEM: '1',
};
const git = (cwd, ...args) => execFileSync('git', args, { cwd, env: ENV, encoding: 'utf8' });
const gitBuffer = (cwd, ...args) => execFileSync('git', args, { cwd, env: ENV, maxBuffer: 64 * 1024 * 1024 });

function nodeFs(root) {
  const at = path => join(root, ...path.split('/'));
  return {
    async read(path) { try { return new Uint8Array(readFileSync(at(path))); } catch { return null; } },
    async write(path, data) { mkdirSync(dirname(at(path)), { recursive: true }); writeFileSync(at(path), data); },
    async exists(path) { return existsSync(at(path)); },
    async remove(path) { rmSync(at(path), { force: true }); },
    async stat(path) { try { const s = statSync(at(path)); return { size: s.size, mtimeMs: s.mtimeMs }; } catch { return null; } },
    async list(path) { try { return readdirSync(at(path)); } catch { return []; } },
  };
}

const write = (root, path, text) => { mkdirSync(dirname(join(root, path)), { recursive: true }); writeFileSync(join(root, path), text); };

/** A repository with one commit on main and an origin URL, and the "Sonic" clone that makes the next commit. */
function scenario({ eol = '\n' } = {}) {
  const base = mkdtempSync(join(tmpdir(), 'sonic-git-'));
  const local = join(base, 'local');
  mkdirSync(local);
  git(local, 'init', '-q', '-b', 'main');
  git(local, 'config', 'commit.gpgsign', 'false');
  git(local, 'config', 'core.autocrlf', eol === '\r\n' ? 'true' : 'false');
  write(local, 'a.txt', `one${eol}two${eol}`);
  write(local, 'src/b.txt', `bee${eol}`);
  write(local, 'docs/readme.md', `# docs${eol}`);
  write(local, 'docs/deep/c.txt', `see${eol}`);
  git(local, 'add', '-A');
  git(local, 'commit', '-q', '-m', 'init');
  git(local, 'remote', 'add', 'origin', 'https://github.com/owner/repo.git');
  const parent = git(local, 'rev-parse', 'HEAD').trim();
  const sonic = join(base, 'sonic');
  git(base, 'clone', '-q', local, sonic);
  git(sonic, 'config', 'commit.gpgsign', 'false');
  write(sonic, 'a.txt', 'one\ntwo\nthree\n');
  write(sonic, 'src/new.txt', 'brand new\n');
  write(sonic, 'docs/deep/c.txt', 'seen\n');
  git(sonic, 'add', '-A');
  git(sonic, 'commit', '-q', '-m', 'Add three, a new file and a deeper change', '-m', 'Second paragraph of the message.');
  return { base, local, sonic, parent, sha: git(sonic, 'rev-parse', 'HEAD').trim() };
}

function bundleFrom(sonic, sha, parent, { tamper = false } = {}) {
  const raw = gitBuffer(sonic, 'cat-file', 'commit', sha).toString('utf8');
  const [headers, ...rest] = raw.split('\n\n');
  const message = rest.join('\n\n');
  const field = name => headers.split('\n').find(line => line.startsWith(`${name} `)).slice(name.length + 1);
  const person = text => {
    const m = /^(.*) <(.*)> (\d+) ([+-]\d{4})$/.exec(text);
    const offset = `${m[4].slice(0, 3)}:${m[4].slice(3)}`;
    return { name: m[1], email: m[2], date: new Date(Number(m[3]) * 1000).toISOString().replace('.000Z', m[4] === '+0000' ? 'Z' : 'Z') };
  };
  const changed = git(sonic, 'diff-tree', '-r', '--no-commit-id', '--name-status', parent, sha).trim().split('\n').map(line => line.split('\t'));
  const folders = new Set(['']);
  for (const [, path] of changed) {
    const parts = path.split('/');
    for (let i = 1; i < parts.length; i++) folders.add(parts.slice(0, i).join('/'));
  }
  const trees = [...folders].map(folder => {
    const id = git(sonic, 'rev-parse', folder ? `${sha}:${folder}` : `${sha}^{tree}`).trim();
    const entries = git(sonic, 'ls-tree', id).trim().split('\n').map(line => {
      const m = /^(\d+) (\w+) ([0-9a-f]{40})\t(.*)$/.exec(line);
      return { mode: m[1], name: m[4], sha: m[3] };
    });
    return { sha: id, entries };
  });
  const files = changed.map(([, path]) => ({ path, sha: git(sonic, 'rev-parse', `${sha}:${path}`).trim(), mode: '100644' }));
  const blobs = files.map(file => {
    let content = gitBuffer(sonic, 'cat-file', 'blob', file.sha);
    if (tamper) content = Buffer.from('tampered\n');
    return { sha: file.sha, content: content.toString('base64') };
  });
  return {
    repo: 'owner/repo',
    commit: { sha, tree: field('tree'), parents: [parent], author: person(field('author')), committer: person(field('committer')), message, signature: null },
    trees, blobs, files,
  };
}

/** The person's own working files: the committed changes, as they would be on disk, uncommitted. */
function applyWorkingChanges(local, { eol = '\n' } = {}) {
  write(local, 'a.txt', `one${eol}two${eol}three${eol}`);
  write(local, 'src/new.txt', `brand new${eol}`);
  write(local, 'docs/deep/c.txt', `seen${eol}`);
}

const porcelain = cwd => git(cwd, 'status', '--porcelain').replace(/\n+$/, '');
const cleanup = s => rmSync(s.base, { recursive: true, force: true });

gitTest('git itself accepts the result: a new branch at the commit, switched to, with a clean status and a sound repository', async () => {
  const s = scenario();
  try {
    applyWorkingChanges(s.local);
    assert.notEqual(porcelain(s.local), '', 'before the import git sees the changes as uncommitted');
    const planned = await planImport(nodeFs(s.local), bundleFrom(s.sonic, s.sha, s.parent), 'feat/notes');
    assert.equal(planned.ok, true, JSON.stringify(planned));
    assert.equal(planned.plan.action, 'switch');
    const done = await applyImport(nodeFs(s.local), planned.plan);
    assert.equal(done.ok, true, JSON.stringify(done));
    assert.equal(git(s.local, 'rev-parse', 'HEAD').trim(), s.sha);
    assert.equal(git(s.local, 'symbolic-ref', '--short', 'HEAD').trim(), 'feat/notes');
    assert.equal(git(s.local, 'rev-parse', 'origin/feat/notes').trim(), s.sha);
    assert.equal(git(s.local, 'rev-parse', 'main').trim(), s.parent, 'the branch they were on is untouched');
    assert.equal(porcelain(s.local), '', 'nothing is left over: the working files already were the commit');
    git(s.local, 'diff', '--cached', '--exit-code');
    execFileSync('git', ['fsck', '--strict', '--no-progress'], { cwd: s.local, env: ENV, stdio: 'pipe' });
    assert.equal(git(s.local, 'cat-file', 'commit', s.sha), git(s.sonic, 'cat-file', 'commit', s.sha), 'the very same commit object');
    assert.match(git(s.local, 'log', '-1', '--format=%an|%ae|%s|%b', s.sha), /^Sonic Person\|person@example\.com\|Add three, a new file and a deeper change\|Second paragraph of the message\./);
    assert.equal(existsSync(join(s.local, '.git', 'index.lock')), false, 'the lock is released');
    assert.match(git(s.local, 'ls-files', '-s', 'src/new.txt'), /^100644 [0-9a-f]{40} 0\tsrc\/new\.txt/);
  } finally {
    cleanup(s);
  }
});

gitTest('what was not part of the commit stays an ordinary local change, and nothing is stashed or lost', async () => {
  const s = scenario();
  try {
    applyWorkingChanges(s.local);
    write(s.local, 'docs/readme.md', '# docs\nmy own unfinished edit\n');
    write(s.local, 'leftover.txt', 'not committed\n');
    const planned = await planImport(nodeFs(s.local), bundleFrom(s.sonic, s.sha, s.parent), 'feat/notes');
    assert.equal((await applyImport(nodeFs(s.local), planned.plan)).ok, true);
    assert.deepEqual(porcelain(s.local).split('\n').sort(), [' M docs/readme.md', '?? leftover.txt']);
    assert.equal(readFileSync(join(s.local, 'docs', 'readme.md'), 'utf8'), '# docs\nmy own unfinished edit\n');
  } finally {
    cleanup(s);
  }
});

gitTest('a committed file the person had already changed further shows as modified, never as clean', async () => {
  const s = scenario();
  try {
    applyWorkingChanges(s.local);
    write(s.local, 'a.txt', 'one\ntwo\nthree\nand a fourth line that was never committed\n');
    const planned = await planImport(nodeFs(s.local), bundleFrom(s.sonic, s.sha, s.parent), 'feat/notes');
    assert.equal((await applyImport(nodeFs(s.local), planned.plan)).ok, true);
    assert.equal(porcelain(s.local), ' M a.txt');
  } finally {
    cleanup(s);
  }
});

gitTest('a push to the branch they are already on moves that branch forward and leaves HEAD where it is', async () => {
  const s = scenario();
  try {
    applyWorkingChanges(s.local);
    const planned = await planImport(nodeFs(s.local), bundleFrom(s.sonic, s.sha, s.parent), 'main');
    assert.equal(planned.ok && planned.plan.action, 'advance');
    assert.equal((await applyImport(nodeFs(s.local), planned.plan)).ok, true);
    assert.equal(git(s.local, 'rev-parse', 'main').trim(), s.sha);
    assert.equal(git(s.local, 'symbolic-ref', '--short', 'HEAD').trim(), 'main');
    assert.equal(git(s.local, 'rev-parse', 'origin/main').trim(), s.sha);
    assert.equal(porcelain(s.local), '');
  } finally {
    cleanup(s);
  }
});

gitTest('Windows line endings in the working files (autocrlf) still end with a clean status', async () => {
  const s = scenario({ eol: '\r\n' });
  try {
    applyWorkingChanges(s.local, { eol: '\r\n' });
    const planned = await planImport(nodeFs(s.local), bundleFrom(s.sonic, s.sha, s.parent), 'feat/notes');
    assert.equal(planned.ok, true, JSON.stringify(planned));
    assert.equal((await applyImport(nodeFs(s.local), planned.plan)).ok, true);
    assert.equal(porcelain(s.local), '');
  } finally {
    cleanup(s);
  }
});

gitTest('a repository whose index is already staged for other files keeps those staged entries', async () => {
  const s = scenario();
  try {
    applyWorkingChanges(s.local);
    write(s.local, 'src/b.txt', 'bee staged\n');
    git(s.local, 'add', 'src/b.txt');
    const planned = await planImport(nodeFs(s.local), bundleFrom(s.sonic, s.sha, s.parent), 'feat/notes');
    assert.equal((await applyImport(nodeFs(s.local), planned.plan)).ok, true);
    assert.equal(porcelain(s.local), 'M  src/b.txt');
  } finally {
    cleanup(s);
  }
});

async function refusal(mutate, { bundle = {}, branch = 'feat/notes' } = {}) {
  const s = scenario();
  try {
    applyWorkingChanges(s.local);
    mutate?.(s);
    const planned = await planImport(nodeFs(s.local), bundleFrom(s.sonic, s.sha, s.parent, bundle), branch);
    assert.equal(planned.ok, false, 'it must refuse');
    assert.equal(git(s.local, 'rev-parse', 'HEAD').trim(), git(s.local, 'rev-parse', 'HEAD').trim());
    return planned.reasons.join(' | ');
  } finally {
    cleanup(s);
  }
}

gitTest('it refuses, in words and before writing anything, when it is not safe', async () => {
  assert.match(await refusal(s => { git(s.local, 'commit', '-q', '--allow-empty', '-m', 'local only'); }), /starts from/);
  assert.match(await refusal(s => writeFileSync(join(s.local, '.git', 'index.lock'), '')), /index\.lock/);
  assert.match(await refusal(s => git(s.local, 'remote', 'set-url', 'origin', 'https://github.com/someone/else.git')), /not owner\/repo/);
  assert.match(await refusal(null, { bundle: { tamper: true } }), /does not match its id/);
  assert.match(await refusal(s => git(s.local, 'update-index', '--index-version', '4')), /version 4/);
  assert.match(await refusal(s => git(s.local, 'checkout', '-q', '--detach')), /detached/);
  assert.match(await refusal(s => git(s.local, 'branch', 'feat/notes')), /already exists/);
  assert.match(await refusal(s => writeFileSync(join(s.local, '.git', 'MERGE_HEAD'), `${s.parent}\n`)), /middle of a merge/);
  assert.match(await refusal(null, { branch: 'bad name' }), /not one this will write/);
  assert.match(await refusal(null, { branch: '../x' }), /not one this will write/);
});

gitTest('the same bundle is idempotent: importing it again after it worked refuses safely instead of damaging anything', async () => {
  const s = scenario();
  try {
    applyWorkingChanges(s.local);
    const bundle = bundleFrom(s.sonic, s.sha, s.parent);
    const planned = await planImport(nodeFs(s.local), bundle, 'feat/notes');
    assert.equal((await applyImport(nodeFs(s.local), planned.plan)).ok, true);
    const again = await planImport(nodeFs(s.local), bundle, 'feat/notes');
    assert.equal(again.ok, false);
    assert.match(again.reasons.join(' '), /starts from/);
    assert.equal(porcelain(s.local), '');
  } finally {
    cleanup(s);
  }
});

gitTest('the index can be read and written back so that git reads it exactly as before', async () => {
  const s = scenario();
  try {
    write(s.local, 'z-é.txt', 'unicode name\n');
    write(s.local, 'a b/with space.txt', 'space\n');
    git(s.local, 'add', '-A');
    const before = git(s.local, 'ls-files', '-s');
    const statusBefore = git(s.local, 'status', '--porcelain');
    const bytes = new Uint8Array(readFileSync(join(s.local, '.git', 'index')));
    const parsed = parseIndex(bytes);
    assert.equal(parsed.entries.length, 6);
    writeFileSync(join(s.local, '.git', 'index'), await serializeIndex(parsed));
    assert.equal(git(s.local, 'ls-files', '-s'), before);
    assert.equal(git(s.local, 'status', '--porcelain'), statusBefore, 'a rewritten index is still a valid one');
    execFileSync('git', ['fsck', '--strict', '--no-progress'], { cwd: s.local, env: ENV, stdio: 'pipe' });
  } finally {
    cleanup(s);
  }
});

test('objects are built the way git builds them', async () => {
  assert.equal(await objectId('blob', new TextEncoder().encode('hello\n')), 'ce013625030ba8dba906f756967f9e9ca394464a');
  assert.deepEqual(parseIsoDate('2026-10-25T20:10:21Z'), { timestamp: Date.UTC(2026, 9, 25, 20, 10, 21) / 1000, offset: '+0000' });
  assert.deepEqual(parseIsoDate('2026-10-25T15:10:21-05:00'), { timestamp: Date.UTC(2026, 9, 25, 20, 10, 21) / 1000, offset: '-0500' });
  assert.deepEqual(parseIsoDate('2026-10-25T20:10:21+05:30').offset, '+0530');
  assert.throws(() => parseIsoDate('yesterday'));
  const tree = serializeTree([{ mode: '040000', name: 'src', sha: 'a'.repeat(40) }, { mode: '100644', name: 'a.txt', sha: 'b'.repeat(40) }]);
  assert.equal(new TextDecoder().decode(tree.subarray(0, 10)), '40000 src\0');
  const commit = new TextDecoder().decode(serializeCommit({
    tree: 'c'.repeat(40), parents: ['d'.repeat(40)], author: { name: 'A', email: 'a@x', timestamp: 1, offset: '+0000' }, committer: { name: 'B', email: 'b@x', timestamp: 2, offset: '-0500' },
    message: 'subject\n\nbody', signature: '-----BEGIN-----\n\nabc\n-----END-----',
  }));
  assert.equal(commit, `tree ${'c'.repeat(40)}\nparent ${'d'.repeat(40)}\nauthor A <a@x> 1 +0000\ncommitter B <b@x> 2 -0500\ngpgsig -----BEGIN-----\n \n abc\n -----END-----\n\nsubject\n\nbody`);
});

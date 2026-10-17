// Run with: node --test src/tidy/tidyFs.test.mjs   (from local/; Node 22.18+ runs the TypeScript module directly)
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { applyMoves, COPY_LIMIT_BYTES, scanFolder, undoMoves } from './tidyFs.ts';

// A small in-memory folder that behaves like the browser's: names ignore case, a non-empty folder will not be removed, and a few things can be made to go wrong.
function makeFs({ supportsMove = true, locked = [] } = {}) {
  const lockedNames = new Set(locked.map(n => n.toLowerCase()));      // files added with these names are "open in another program": they cannot be moved or removed
  function dir(name) {
    const entries = new Map();
    const self = {
      kind: 'directory', name, entries,
      async *values() { for (const { node } of entries.values()) yield node; },
      async getDirectoryHandle(n, o = {}) {
        const e = entries.get(n.toLowerCase());
        if (e && e.node.kind === 'directory') return e.node;
        if (e || !o.create) throw Object.assign(new Error('not found'), { name: 'NotFoundError' });
        const d = dir(n); entries.set(n.toLowerCase(), { node: d }); return d;
      },
      async getFileHandle(n, o = {}) {
        const e = entries.get(n.toLowerCase());
        if (e && e.node.kind === 'file') return e.node;
        if (e || !o.create) throw Object.assign(new Error('not found'), { name: 'NotFoundError' });
        const f = file(n, 0, self); entries.set(n.toLowerCase(), { node: f }); return f;
      },
      async removeEntry(n, o = {}) {
        const e = entries.get(n.toLowerCase());
        if (!e) throw Object.assign(new Error('not found'), { name: 'NotFoundError' });
        if (e.node.kind === 'directory' && e.node.entries.size && !o.recursive) throw new Error('not empty');
        if (e.node.kind === 'file' && e.node.locked) throw new Error('in use');
        entries.delete(n.toLowerCase());
      },
    };
    return self;
  }
  function file(name, size, parent, modified = 1, isLocked = false) {
    const f = {
      kind: 'file', name, size, modified, parent, locked: isLocked,
      async getFile() { return { size: f.size, lastModified: f.modified }; },
      async createWritable() { let pending = f.size; return { async write(d) { pending = d.size; }, async close() { f.size = pending; } }; },
    };
    if (supportsMove) {
      f.move = async (dest, newName) => {
        if (f.locked) throw new Error('in use');
        f.parent.entries.delete(f.name.toLowerCase());
        f.name = newName ?? f.name; f.parent = dest;
        dest.entries.set(f.name.toLowerCase(), { node: f });
      };
    }
    return f;
  }
  const root = dir('Downloads');
  const add = (n, size = 100, modified = 1) => { root.entries.set(n.toLowerCase(), { node: file(n, size, root, modified, lockedNames.has(n.toLowerCase())) }); };
  const names = d => [...d.entries.values()].map(e => e.node.name).sort();
  return { root, add, names };
}

test('a scan lists the loose files with their sizes and dates, and the folder names, and reads nothing else', async () => {
  const fs = makeFs();
  fs.add('a.pdf', 10, 111); fs.add('b.jpg', 20, 222);
  await fs.root.getDirectoryHandle('Old stuff', { create: true });
  const scan = await scanFolder(fs.root);
  assert.deepEqual(scan.files.map(f => [f.name, f.size, f.modified]).sort(), [['a.pdf', 10, 111], ['b.jpg', 20, 222]]);
  assert.deepEqual(scan.folderNames, ['Old stuff']);
});

for (const supportsMove of [true, false]) {
  const how = supportsMove ? 'by moving them' : 'by copying and checking the copy when the browser cannot move';

  test(`files end up in their folders ${how}, and the folders are created`, async () => {
    const fs = makeFs({ supportsMove });
    fs.add('a.pdf'); fs.add('p.jpg'); fs.add('keep.xyz');
    const out = await applyMoves(fs.root, [{ name: 'a.pdf', folder: 'Documents' }, { name: 'p.jpg', folder: 'Pictures' }]);
    assert.deepEqual(out.failed, []);
    assert.deepEqual(fs.names(fs.root), ['Documents', 'Pictures', 'keep.xyz']);
    assert.deepEqual(fs.names(await fs.root.getDirectoryHandle('Documents')), ['a.pdf']);
    assert.deepEqual(out.createdFolders.sort(), ['Documents', 'Pictures']);
    assert.deepEqual(out.done.map(m => m.to), ['a.pdf', 'p.jpg']);
  });

  test(`a name that is already taken gets a number and nothing is overwritten ${how}`, async () => {
    const fs = makeFs({ supportsMove });
    const docs = await fs.root.getDirectoryHandle('Documents', { create: true });
    docs.entries.set('a.pdf', { node: Object.assign(await docs.getFileHandle('a.pdf', { create: true }), { size: 999 }) });
    fs.add('a.pdf', 100);
    const out = await applyMoves(fs.root, [{ name: 'a.pdf', folder: 'Documents' }]);
    assert.deepEqual(fs.names(docs), ['a (2).pdf', 'a.pdf']);
    assert.equal((await (await docs.getFileHandle('a.pdf')).getFile()).size, 999);      // the one already there is untouched
    assert.equal(out.done[0].to, 'a (2).pdf');
  });
}

test('a folder that already existed is reused and is not reported as created', async () => {
  const fs = makeFs();
  await fs.root.getDirectoryHandle('Pictures', { create: true });
  fs.add('p.jpg'); fs.add('s.png');
  const out = await applyMoves(fs.root, [{ name: 'p.jpg', folder: 'Pictures' }, { name: 's.png', folder: 'Pictures/Screenshots' }]);
  assert.deepEqual(out.createdFolders, ['Pictures/Screenshots']);
  assert.deepEqual(fs.names(await (await fs.root.getDirectoryHandle('Pictures')).getDirectoryHandle('Screenshots')), ['s.png']);
});

test('a file that is open in another program stays exactly where it was, with no stray copy, and the others still move', async () => {
  const fs = makeFs({ supportsMove: false, locked: ['busy.pdf'] });
  fs.add('busy.pdf', 50); fs.add('fine.pdf', 60);
  const out = await applyMoves(fs.root, [{ name: 'busy.pdf', folder: 'Documents' }, { name: 'fine.pdf', folder: 'Documents' }]);
  assert.deepEqual(out.failed.map(f => f.name), ['busy.pdf']);
  assert.match(out.failed[0].reason, /open in another program/);
  assert.deepEqual(fs.names(fs.root), ['Documents', 'busy.pdf']);
  assert.deepEqual(fs.names(await fs.root.getDirectoryHandle('Documents')), ['fine.pdf']);      // no half-made copy of busy.pdf
});

test('a very large file is not copied when the browser cannot move it in place, and the rest carry on', async () => {
  const fs = makeFs({ supportsMove: false });
  fs.add('huge.iso', COPY_LIMIT_BYTES + 1); fs.add('small.exe', 10);
  const out = await applyMoves(fs.root, [{ name: 'huge.iso', folder: 'Installers' }, { name: 'small.exe', folder: 'Installers' }]);
  assert.deepEqual(out.failed.map(f => [f.name, f.reason]), [['huge.iso', 'too large to move safely']]);
  assert.deepEqual(fs.names(fs.root), ['Installers', 'huge.iso']);
});

test('a file that has gone missing is reported and the others still move', async () => {
  const fs = makeFs();
  fs.add('here.pdf');
  const out = await applyMoves(fs.root, [{ name: 'gone.pdf', folder: 'Documents' }, { name: 'here.pdf', folder: 'Documents' }]);
  assert.deepEqual(out.failed.map(f => f.name), ['gone.pdf']);
  assert.deepEqual(out.done.map(m => m.name), ['here.pdf']);
});

test('progress is reported after every file', async () => {
  const fs = makeFs();
  fs.add('a.pdf'); fs.add('b.pdf'); fs.add('c.pdf');
  const seen = [];
  await applyMoves(fs.root, ['a', 'b', 'c'].map(n => ({ name: `${n}.pdf`, folder: 'Documents' })), (done, total) => seen.push([done, total]));
  assert.deepEqual(seen, [[1, 3], [2, 3], [3, 3]]);
});

test('undo puts every file back under its own name and removes the folders the tidy-up made', async () => {
  const fs = makeFs();
  fs.add('a.pdf'); fs.add('p.jpg'); fs.add('s.png');
  const moves = [{ name: 'a.pdf', folder: 'Documents' }, { name: 'p.jpg', folder: 'Pictures' }, { name: 's.png', folder: 'Pictures/Screenshots' }];
  const out = await applyMoves(fs.root, moves);
  const back = await undoMoves(fs.root, out.done, out.createdFolders);
  assert.deepEqual(back.failed, []);
  assert.deepEqual(fs.names(fs.root), ['a.pdf', 'p.jpg', 's.png']);
});

test('undo never removes a folder that has something else in it, and never overwrites a file that has taken the old name', async () => {
  const fs = makeFs();
  fs.add('a.pdf'); fs.add('b.pdf');
  const out = await applyMoves(fs.root, [{ name: 'a.pdf', folder: 'Documents' }, { name: 'b.pdf', folder: 'Documents' }]);
  const docs = await fs.root.getDirectoryHandle('Documents');
  docs.entries.set('mine.txt', { node: { kind: 'file', name: 'mine.txt', async getFile() { return { size: 1, lastModified: 1 }; } } });   // added since
  fs.add('a.pdf', 777);                                                                                                                      // a new file took the name
  await undoMoves(fs.root, out.done, out.createdFolders);
  assert.deepEqual(fs.names(fs.root), ['Documents', 'a (2).pdf', 'a.pdf', 'b.pdf']);
  assert.deepEqual(fs.names(docs), ['mine.txt']);
});

// Run with: node --test --experimental-test-module-mocks src/localEdits.test.mjs   (from local/; Node 22.18+ runs the TypeScript module directly)
// Applies and undoes real proposals against an in-memory folder, with the server and the browser's storage stood in for.
import assert from 'node:assert/strict';
import { registerHooks } from 'node:module';
import { mock, test } from 'node:test';

// The app imports its own modules without a file ending (the bundler adds it); Node needs one, so try it with .ts when the plain name is not found.
registerHooks({
  resolve(specifier, context, next) {
    try {
      return next(specifier, context);
    } catch (error) {
      if (specifier.startsWith('.') && !/\.\w+$/.test(specifier)) return next(`${specifier}.ts`, context);
      throw error;
    }
  },
});

const store = new Map();
const rendered = [];
const edited = [];
let renderFails = false;
let editFails = null;
mock.module('./idb.ts', {
  namedExports: {
    keyValueStore: () => ({
      async get(key) { return store.get(key) ?? null; },
      async put(key, value) { store.set(key, structuredClone(value)); },
      async remove(key) { store.delete(key); },
      async all() { return [...store.values()].map(v => structuredClone(v)); },
    }),
  },
});
mock.module('./api.ts', {
  namedExports: {
    renderLocalDocx: async (document) => {
      rendered.push(document);
      if (renderFails) throw new Error('The server said no.');
      return new TextEncoder().encode('PK-fake-docx-for:' + document.title).buffer;
    },
    editLocalDocx: async (base64, wordEdits) => {
      edited.push({ base64, wordEdits });
      if (editFails) throw new Error(editFails);
      return new TextEncoder().encode('PK-edited-copy-of:' + Buffer.from(base64, 'base64').toString('utf-8')).buffer;
    },
  },
});
let granted = true;
mock.module('./localWorkspace.ts', {
  namedExports: { hasPermission: async () => granted, bytesToBase64: (bytes) => Buffer.from(bytes).toString('base64') },
});
const { applyProposal, undoBatch } = await import('./localEdits.ts');

// An in-memory folder: names are case-sensitive here, writes can be made to fail, and each write advances a clock so a re-save changes the stamp.
function makeFolder({ failWriteOf = null } = {}) {
  let clock = 1000;
  const files = new Map();            // path -> { data: string | ArrayBuffer, modified }
  const dirs = new Set(['']);
  function handle(prefix) {
    return {
      kind: 'directory', name: prefix || 'root',
      async getDirectoryHandle(name, o = {}) {
        const path = prefix + name + '/';
        if (!dirs.has(path)) { if (!o.create) throw new DOMException('not found', 'NotFoundError'); dirs.add(path); }
        return handle(path);
      },
      async getFileHandle(name, o = {}) {
        const path = prefix + name;
        if (!files.has(path)) { if (!o.create) throw new DOMException('not found', 'NotFoundError'); files.set(path, { data: '', modified: clock++ }); }
        return {
          kind: 'file', name,
          async getFile() {
            const entry = files.get(path);
            const size = typeof entry.data === 'string' ? entry.data.length : entry.data.byteLength;
            const bytes = () => (typeof entry.data === 'string' ? new TextEncoder().encode(entry.data) : new Uint8Array(entry.data));
            return {
              size, lastModified: entry.modified,
              async text() { return typeof entry.data === 'string' ? entry.data : new TextDecoder().decode(entry.data); },
              async arrayBuffer() { return bytes().buffer.slice(0); },
            };
          },
          async createWritable() {
            return {
              async write(data) { if (path === failWriteOf) throw new Error('disk full'); files.get(path).data = data; },
              async close() { files.get(path).modified = clock++; },
            };
          },
        };
      },
      async removeEntry(name) { if (!files.delete(prefix + name)) throw new DOMException('not found', 'NotFoundError'); },
    };
  }
  return { root: handle(''), files, save: (path, data) => files.set(path, { data, modified: clock++ }) };
}

const DOC = { title: 'Resume', blocks: [{ type: 'paragraph', text: 'Hello' }] };
const proposal = (edits, id = 'p1') => ({ proposal_id: id, summary: 's', folder: 'Docs', files: [], edits });

test('a new Word document is rendered by the server and written as the bytes it returned', async () => {
  const folder = makeFolder();
  const result = await applyProposal(folder.root, proposal([{ type: 'create_docx', path: 'CV/Resume.docx', document: DOC }]));
  assert.deepEqual(result, { ok: true, filesChanged: 1 });
  assert.equal(new TextDecoder().decode(folder.files.get('CV/Resume.docx').data), 'PK-fake-docx-for:Resume');
  assert.deepEqual(rendered.at(-1), DOC);
});

test('undo removes the document it wrote, and the copy kept for undo holds no description of the document', async () => {
  const folder = makeFolder();
  await applyProposal(folder.root, proposal([{ type: 'create_docx', path: 'Resume.docx', document: DOC }], 'u1'));
  assert.equal(store.get('u1').files[0].document, undefined);
  assert.match(store.get('u1').files[0].stamp, /^\d+:\d+$/);
  assert.deepEqual(await undoBatch(folder.root, 'u1'), { ok: true, filesRestored: 1 });
  assert.equal(folder.files.has('Resume.docx'), false);
});

test('undo refuses, and touches nothing, when the document was saved in Word after it was written', async () => {
  const folder = makeFolder();
  await applyProposal(folder.root, proposal([{ type: 'create_docx', path: 'Resume.docx', document: DOC }, { type: 'create', path: 'notes.txt', content: 'hi' }], 'u2'));
  folder.save('Resume.docx', 'edited later in Word');
  const result = await undoBatch(folder.root, 'u2');
  assert.equal(result.ok, false);
  assert.match(result.errors[0], /changed after the edit was applied.*Resume\.docx/);
  assert.equal(folder.files.has('notes.txt'), true);      // all or nothing: the text file was not undone either
});

test('if the server cannot build the document nothing at all is written, text files included', async () => {
  const folder = makeFolder();
  renderFails = true;
  const result = await applyProposal(folder.root, proposal([{ type: 'create', path: 'notes.txt', content: 'hi' }, { type: 'create_docx', path: 'Resume.docx', document: DOC }], 'f1'));
  renderFails = false;
  assert.equal(result.ok, false);
  assert.match(result.errors[0], /Couldn't make Resume\.docx: The server said no\. Nothing was changed\./);
  assert.equal(folder.files.size, 0);
  assert.equal(store.has('f1'), false);
});

test('if a write fails part way, what was already written (a Word document too) is put back', async () => {
  const folder = makeFolder({ failWriteOf: 'notes.txt' });
  const result = await applyProposal(folder.root, proposal([{ type: 'create_docx', path: 'Resume.docx', document: DOC }, { type: 'create', path: 'notes.txt', content: 'hi' }], 'f2'));
  assert.equal(result.ok, false);
  assert.match(result.errors[0], /Writing failed \(disk full\); the files were put back/);
  assert.equal(folder.files.has('Resume.docx'), false);
});

test('a document is not written over a file that is already there, and no render is wasted on it', async () => {
  const folder = makeFolder();
  folder.save('Resume.docx', 'PK existing');
  const before = rendered.length;
  const result = await applyProposal(folder.root, proposal([{ type: 'create_docx', path: 'Resume.docx', document: DOC }], 'e1'));
  assert.equal(result.ok, false);
  assert.match(result.errors[0], /already exists/);
  assert.equal(folder.files.get('Resume.docx').data, 'PK existing');
  assert.equal(rendered.length, before);
});

test('without write permission nothing is rendered or written', async () => {
  const folder = makeFolder();
  granted = false;
  const before = rendered.length;
  const result = await applyProposal(folder.root, proposal([{ type: 'create_docx', path: 'Resume.docx', document: DOC }], 'n1'));
  granted = true;
  assert.equal(result.ok, false);
  assert.match(result.errors[0], /Write access .* not granted/);
  assert.equal(folder.files.size, 0);
  assert.equal(rendered.length, before);
});

test('ordinary text edits still apply and undo as before', async () => {
  const folder = makeFolder();
  folder.save('README.md', 'one\ntwo\n');
  const applied = await applyProposal(folder.root, proposal([{ type: 'replace', path: 'README.md', old_string: 'two', new_string: 'three' }], 't1'));
  assert.equal(applied.ok, true);
  assert.equal(folder.files.get('README.md').data, 'one\nthree\n');
  assert.equal((await undoBatch(folder.root, 't1')).ok, true);
  assert.equal(folder.files.get('README.md').data, 'one\ntwo\n');
});

// ---- an edited copy of an existing Word document ----------------------------------------------------------------------------------------------------

const EDIT = (id = 'w') => proposal([{ type: 'edit_docx', path: 'Resume.docx', save_as: 'Resume (edited).docx', word_edits: [{ op: 'delete', paragraph: 3 }] }], id);

test("the original document's current bytes go to the server with the edits, and what comes back is saved as a new file next to it", async () => {
  const folder = makeFolder();
  folder.save('Resume.docx', 'PK-original-bytes');
  const result = await applyProposal(folder.root, EDIT('w1'));
  assert.deepEqual(result, { ok: true, filesChanged: 1 });
  const sent = edited.at(-1);
  assert.equal(Buffer.from(sent.base64, 'base64').toString('utf-8'), 'PK-original-bytes');
  assert.deepEqual(sent.wordEdits, [{ op: 'delete', paragraph: 3 }]);
  assert.equal(new TextDecoder().decode(folder.files.get('Resume (edited).docx').data), 'PK-edited-copy-of:PK-original-bytes');
});

test('the original is never written: same bytes, same modified time, after the copy is made and after undo', async () => {
  const folder = makeFolder();
  folder.save('Resume.docx', 'PK-original-bytes');
  const before = { ...folder.files.get('Resume.docx') };
  await applyProposal(folder.root, EDIT('w2'));
  assert.deepEqual(folder.files.get('Resume.docx'), before);
  assert.deepEqual(await undoBatch(folder.root, 'w2'), { ok: true, filesRestored: 1 });
  assert.deepEqual(folder.files.get('Resume.docx'), before);
  assert.equal(folder.files.has('Resume (edited).docx'), false);        // undo removed only the copy
});

test('undo of a copy refuses, and removes nothing, once the copy has been saved to since', async () => {
  const folder = makeFolder();
  folder.save('Resume.docx', 'PK-original-bytes');
  await applyProposal(folder.root, EDIT('w3'));
  folder.save('Resume (edited).docx', 'the person kept working on the copy');
  const result = await undoBatch(folder.root, 'w3');
  assert.equal(result.ok, false);
  assert.match(result.errors[0], /Resume \(edited\)\.docx/);
  assert.equal(folder.files.get('Resume (edited).docx').data, 'the person kept working on the copy');
});

test('when the server refuses the edit (the document changed since Sonic read it, say) nothing is written and nothing is kept for undo', async () => {
  const folder = makeFolder();
  folder.save('Resume.docx', 'PK-original-bytes');
  editFails = 'word edit 1: the text to replace was not found in paragraph 3.';
  const result = await applyProposal(folder.root, EDIT('w4'));
  editFails = null;
  assert.equal(result.ok, false);
  assert.match(result.errors[0], /Couldn't make Resume \(edited\)\.docx: word edit 1: the text to replace was not found in paragraph 3\. Nothing was changed\./);
  assert.equal(folder.files.has('Resume (edited).docx'), false);
  assert.equal(store.has('w4'), false);
});

test('a document that has vanished from the folder is reported, not guessed at', async () => {
  const folder = makeFolder();
  const result = await applyProposal(folder.root, EDIT('w5'));
  assert.equal(result.ok, false);
  assert.match(result.errors[0], /no longer in the folder/);
  assert.equal(folder.files.size, 0);
});

test('if a copy already exists under that name nothing is sent to the server and nothing is overwritten', async () => {
  const folder = makeFolder();
  folder.save('Resume.docx', 'PK-original-bytes');
  folder.save('Resume (edited).docx', 'an older copy');
  const sentBefore = edited.length;
  const result = await applyProposal(folder.root, EDIT('w6'));
  assert.equal(result.ok, false);
  assert.match(result.errors[0], /already exists/);
  assert.equal(folder.files.get('Resume (edited).docx').data, 'an older copy');
  assert.equal(edited.length, sentBefore);
});

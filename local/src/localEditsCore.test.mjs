// Run with: node --test src/localEditsCore.test.mjs   (from local/; Node 22.18+ runs the TypeScript module directly)
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { changesMovedSince, forUndo, planEdits } from './localEditsCore.ts';

const DOC = { title: 'Resume', blocks: [{ type: 'paragraph', text: 'Hello' }] };
const files = (map) => async (path) => (path in map ? map[path] : null);

test('a new Word document plans as a binary create that carries its description and no text', async () => {
  const plan = await planEdits([{ type: 'create_docx', path: 'Resume.docx', document: DOC }], files({}));
  assert.equal(plan.ok, true);
  assert.deepEqual(plan.changes, [{ path: 'Resume.docx', before: null, after: '', binary: 'docx', document: DOC }]);
});

test('a Word document rides in one proposal with ordinary text edits', async () => {
  const plan = await planEdits(
    [{ type: 'create', path: 'notes.txt', content: 'hi' }, { type: 'create_docx', path: 'Letter.docx', document: DOC }],
    files({}),
  );
  assert.equal(plan.ok, true);
  assert.deepEqual(plan.changes.map(c => [c.path, c.binary ?? 'text']), [['notes.txt', 'text'], ['Letter.docx', 'docx']]);
});

test('an existing file with that name is never overwritten, whatever it holds', async () => {
  const plan = await planEdits([{ type: 'create_docx', path: 'Resume.docx', document: DOC }], files({ 'Resume.docx': 'PK binary junk' }));
  assert.equal(plan.ok, false);
  assert.match(plan.errors[0], /already exists.*never overwrites/);
});

test('a Word document must be named .docx, and a protected or unsafe path is refused', async () => {
  const wrongName = await planEdits([{ type: 'create_docx', path: 'Resume.txt', document: DOC }], files({}));
  assert.match(wrongName.errors[0], /must be named something\.docx/);
  const escaped = await planEdits([{ type: 'create_docx', path: '../Resume.docx', document: DOC }], files({}));
  assert.match(escaped.errors[0], /unsafe path/);
  const protectedDir = await planEdits([{ type: 'create_docx', path: 'node_modules/x/Resume.docx', document: DOC }], files({}));
  assert.match(protectedDir.errors[0], /protected directory/);
});

test('nothing else in the same proposal may change a document it creates', async () => {
  const plan = await planEdits(
    [{ type: 'create_docx', path: 'A.docx', document: DOC }, { type: 'replace', path: 'A.docx', old_string: 'x', new_string: 'y' }],
    files({}),
  );
  assert.equal(plan.ok, false);
  assert.match(plan.errors[0], /created by this same proposal/);
});

test('one bad edit means nothing at all is planned', async () => {
  const plan = await planEdits(
    [{ type: 'create_docx', path: 'A.docx', document: DOC }, { type: 'replace', path: 'gone.txt', old_string: 'x', new_string: 'y' }],
    files({}),
  );
  assert.equal(plan.ok, false);
});

test('the copy kept for undo drops the description and keeps the stamp', () => {
  const kept = forUndo({ path: 'A.docx', before: null, after: '', binary: 'docx', document: DOC, stamp: '120:1700' });
  assert.deepEqual(kept, { path: 'A.docx', before: null, after: '', binary: 'docx', stamp: '120:1700' });
});

test('undo is refused for a Word document that was saved after it was written, or never stamped', async () => {
  const change = { path: 'A.docx', before: null, after: '', binary: 'docx', stamp: '120:1700' };
  const text = files({});
  assert.deepEqual(await changesMovedSince([change], text, async () => '120:1700'), []);
  assert.deepEqual(await changesMovedSince([change], text, async () => '131:1800'), ['A.docx']);   // edited in Word since
  assert.deepEqual(await changesMovedSince([change], text, async () => null), ['A.docx']);          // already deleted
  assert.deepEqual(await changesMovedSince([{ ...change, stamp: undefined }], text, async () => '120:1700'), ['A.docx']);
});

test('text files are still checked by their content, so the old undo guard is unchanged', async () => {
  const change = { path: 'notes.txt', before: 'a', after: 'b' };
  assert.deepEqual(await changesMovedSince([change], files({ 'notes.txt': 'b' }), async () => null), []);
  assert.deepEqual(await changesMovedSince([change], files({ 'notes.txt': 'b, edited later' }), async () => null), ['notes.txt']);
});

// ---- editing an existing Word document: always a new copy, the original only read ----------------------------------------------------------------

const EDIT = { type: 'edit_docx', path: 'Resume.docx', save_as: 'Resume (edited).docx', word_edits: [{ op: 'delete', paragraph: 3 }] };

test('an edit to an existing Word document plans one new binary copy and no change at all to the original', async () => {
  const plan = await planEdits([EDIT], files({ 'Resume.docx': 'PK original' }));
  assert.equal(plan.ok, true);
  assert.deepEqual(plan.changes, [{ path: 'Resume (edited).docx', before: null, after: '', binary: 'docx', docxEdit: { source: 'Resume.docx', word_edits: EDIT.word_edits } }]);
});

test('the copy never replaces anything: same name, another case of the same name, an existing file, or a missing original are all refused', async () => {
  const there = files({ 'Resume.docx': 'x' });
  assert.match((await planEdits([{ ...EDIT, save_as: 'Resume.docx' }], there)).errors[0], /new name; the original is never changed/);
  assert.match((await planEdits([{ ...EDIT, save_as: 'resume.DOCX' }], there)).errors[0], /new name/);
  assert.match((await planEdits([EDIT], files({ 'Resume.docx': 'x', 'Resume (edited).docx': 'older copy' }))).errors[0], /already exists.*never overwrites/);
  assert.match((await planEdits([EDIT], files({}))).errors[0], /no longer in the folder/);
});

test('the copy must be a .docx at a path that is allowed, and the original must be a .docx too', async () => {
  const there = files({ 'Resume.docx': 'x', 'notes.txt': 'x' });
  assert.match((await planEdits([{ ...EDIT, save_as: 'copy.txt' }], there)).errors[0], /must be \.docx files/);
  assert.match((await planEdits([{ ...EDIT, path: 'notes.txt' }], there)).errors[0], /must be \.docx files/);
  assert.match((await planEdits([{ ...EDIT, save_as: '../x.docx' }], there)).errors[0], /unsafe path/);
  assert.match((await planEdits([{ ...EDIT, save_as: 'node_modules/x.docx' }], there)).errors[0], /protected directory/);
});

test('two edits cannot both make the same copy', async () => {
  const plan = await planEdits([EDIT, { ...EDIT, word_edits: [{ op: 'delete', paragraph: 4 }] }], files({ 'Resume.docx': 'x' }));
  assert.equal(plan.ok, false);
  assert.match(plan.errors[0], /already exists/);
});

test('undo keeps nothing of how the copy was made', () => {
  const kept = forUndo({ path: 'C.docx', before: null, after: '', binary: 'docx', docxEdit: { source: 'A.docx', word_edits: [] }, stamp: '1:2' });
  assert.deepEqual(kept, { path: 'C.docx', before: null, after: '', binary: 'docx', stamp: '1:2' });
});

// ---- deleting and moving files (2026-11-01) ----

test('a delete plans as a removed file that keeps its text for Undo and writes nothing', async () => {
  const plan = await planEdits([{ type: 'delete', path: 'src/old.ts' }], files({ 'src/old.ts': 'export const old = 1;\n' }));
  assert.equal(plan.ok, true);
  assert.deepEqual(plan.changes, [{ path: 'src/old.ts', before: 'export const old = 1;\n', after: '', deleted: true }]);
});

test('a delete of a file that is gone, or of a Word document, is refused', async () => {
  const gone = await planEdits([{ type: 'delete', path: 'src/old.ts' }], files({}));
  assert.match(gone.errors[0], /no longer exists, so there is nothing to delete/);
  const word = await planEdits([{ type: 'delete', path: 'Letter.docx' }], files({ 'Letter.docx': 'x' }));
  assert.match(word.errors[0], /never deleted/);
  const secret = await planEdits([{ type: 'delete', path: '.env' }], files({ '.env': 'KEY=1' }));
  assert.match(secret.errors[0], /not allowed/);
});

test('a move plans as the old path removed and the new path created with the same text', async () => {
  const plan = await planEdits([{ type: 'rename', path: 'a/old.ts', to: 'b/new.ts' }], files({ 'a/old.ts': 'let x = 1;\n' }));
  assert.equal(plan.ok, true);
  assert.deepEqual(plan.changes, [
    { path: 'a/old.ts', before: 'let x = 1;\n', after: '', deleted: true },
    { path: 'b/new.ts', before: null, after: 'let x = 1;\n' },
  ]);
});

test('a move onto an existing file, a missing file, itself, a Word document or a protected path is refused', async () => {
  const onto = await planEdits([{ type: 'rename', path: 'a.ts', to: 'b.ts' }], files({ 'a.ts': '1', 'b.ts': '2' }));
  assert.match(onto.errors[0], /already exists.*never overwrites/);
  const missing = await planEdits([{ type: 'rename', path: 'a.ts', to: 'b.ts' }], files({}));
  assert.match(missing.errors[0], /no longer exists, so it can't be moved/);
  const itself = await planEdits([{ type: 'rename', path: 'a.ts', to: 'a.ts' }], files({ 'a.ts': '1' }));
  assert.match(itself.errors[0], /already there/);
  const word = await planEdits([{ type: 'rename', path: 'a.docx', to: 'b.docx' }], files({ 'a.docx': '1' }));
  assert.match(word.errors[0], /never moved/);
  const protectedPath = await planEdits([{ type: 'rename', path: 'a.ts', to: 'node_modules/b.ts' }], files({ 'a.ts': '1' }));
  assert.match(protectedPath.errors[0], /protected directory/);
});

test('edits after a move apply to the file at its new path, and a file can be deleted and made again', async () => {
  const moved = await planEdits(
    [{ type: 'rename', path: 'a.ts', to: 'b.ts' }, { type: 'replace', path: 'b.ts', old_string: 'one', new_string: 'two' }],
    files({ 'a.ts': 'one\n' }),
  );
  assert.equal(moved.ok, true);
  assert.deepEqual(moved.changes.map(c => [c.path, c.after, c.deleted === true]), [['a.ts', '', true], ['b.ts', 'two\n', false]]);
  const again = await planEdits([{ type: 'delete', path: 'a.ts' }, { type: 'create', path: 'a.ts', content: 'fresh\n' }], files({ 'a.ts': 'old\n' }));
  assert.equal(again.ok, true);
  assert.deepEqual(again.changes, [{ path: 'a.ts', before: 'old\n', after: 'fresh\n' }]);
});

test('undo of a delete is blocked when something has been put back where the file was, and allowed when it is still gone', async () => {
  const change = { path: 'a.ts', before: 'old\n', after: '', deleted: true };
  const gone = await changesMovedSince([change], async () => null, async () => null);
  assert.deepEqual(gone, []);
  const back = await changesMovedSince([change], async () => 'new work\n', async () => null);
  assert.deepEqual(back, ['a.ts']);
});

test('undo keeps the deleted flag so it knows to write the text back', () => {
  assert.deepEqual(forUndo({ path: 'a.ts', before: 'old\n', after: '', deleted: true }), { path: 'a.ts', before: 'old\n', after: '', deleted: true });
});

test('applying a delete removes the file, and a rollback or Undo writes its text back', async () => {
  const { readFileSync } = await import('node:fs');
  const source = readFileSync(new URL('./localEdits.ts', import.meta.url), 'utf8');
  assert.match(source, /else if \(change\.deleted\) \{\s*await removeFile\(root, change\.path\);/);
  assert.match(source, /if \(change\.before === null\) await removeFile\(root, change\.path\);\s*else await writeFileText\(root, change\.path, change\.before\);/);
});

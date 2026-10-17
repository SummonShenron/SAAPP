// Run with: node --test src/tidy/tidyCore.test.mjs   (from local/; Node 22.18+ runs the TypeScript module directly)
import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  buildPlan, describeStays, extensionOf, formatBytes, kindOf, planMoves, RECENT_MS, reverseMoves, skipReason, uniqueName,
} from './tidyCore.ts';

const NOW = Date.UTC(2026, 9, 18, 12, 0, 0);
const old = (name, size = 1000) => ({ name, size, modified: NOW - 3 * 24 * 3600 * 1000 });

test('the ending of a file name, in lower case, and none for names without one', () => {
  assert.equal(extensionOf('Report.FINAL.PDF'), 'pdf');
  for (const name of ['README', '.gitignore', 'trailing.', '']) assert.equal(extensionOf(name), '', name);
});

test('files are sorted by kind, and screenshots are told apart from other pictures by their name', () => {
  assert.equal(kindOf('holiday.JPG').id, 'pictures');
  assert.equal(kindOf('Screenshot 2026-10-01 at 9.14.02 AM.png').id, 'screenshots');
  assert.equal(kindOf('Screenshot_20261001.jpg').folder, 'Pictures/Screenshots');
  assert.equal(kindOf('screenshots-notes.pdf').id, 'documents');           // only pictures can be screenshots
  assert.deepEqual(['lease.pdf', 'budget.xlsx', 'talk.pptx', 'song.mp3', 'clip.mov', 'backup.zip', 'setup.exe'].map(n => kindOf(n).id),
    ['documents', 'spreadsheets', 'presentations', 'music', 'videos', 'archives', 'installers']);
  for (const name of ['mystery.xyz', 'noextension', 'data.json']) assert.equal(kindOf(name), null, name);
});

test('hidden, system, still-downloading and just-changed files are left alone whatever they are', () => {
  assert.equal(skipReason(old('.hidden.pdf'), NOW), 'system');
  assert.equal(skipReason(old('Thumbs.db'), NOW), 'system');
  assert.equal(skipReason(old('~$budget.xlsx'), NOW), 'system');            // an Office lock file
  assert.equal(skipReason(old('movie.mp4.crdownload'), NOW), 'in_progress');
  assert.equal(skipReason({ name: 'new.pdf', size: 1, modified: NOW - RECENT_MS + 1000 }, NOW), 'recent');
  assert.equal(skipReason({ name: 'new.pdf', size: 1, modified: NOW - RECENT_MS - 1000 }, NOW), null);
  assert.equal(skipReason(old('lease.pdf'), NOW), null);
});

test('the plan groups files by kind, biggest group first, and says what stays and why', () => {
  const files = [
    old('a.pdf'), old('b.pdf'), old('c.pdf'), old('p1.jpg', 500), old('p2.png', 700), old('Screenshot 1.png'),
    old('mystery.xyz'), old('.DS_Store'), old('half.crdownload'), { name: 'fresh.pdf', size: 1, modified: NOW - 1000 },
  ];
  const plan = buildPlan(files, NOW);
  assert.deepEqual(plan.groups.map(g => [g.id, g.files.length]), [['documents', 3], ['pictures', 2], ['screenshots', 1]]);
  assert.equal(plan.movableFiles, 6);
  assert.equal(plan.totalFiles, 10);
  assert.equal(plan.groups.find(g => g.id === 'pictures').bytes, 1200);
  assert.equal(describeStays(plan.stays), '1 not sure where they belong, 1 changed in the last few minutes, 1 still downloading, 1 hidden or system files');
});

test('a folder is never given the name of a file that is already there', () => {
  const plan = buildPlan([old('a.pdf'), old('Screenshot 1.png')], NOW, ['documents', 'Pictures']);
  assert.equal(plan.groups.find(g => g.id === 'documents').folder, 'Documents (sorted)');
  assert.equal(plan.groups.find(g => g.id === 'screenshots').folder, 'Pictures (sorted)/Screenshots');
});

test('only the groups the person left ticked are turned into moves, and nothing else', () => {
  const plan = buildPlan([old('a.pdf'), old('p.jpg'), old('mystery.xyz')], NOW);
  assert.deepEqual(planMoves(plan, ['pictures']), [{ name: 'p.jpg', folder: 'Pictures' }]);
  assert.deepEqual(planMoves(plan, []), []);
  assert.equal(planMoves(plan, ['documents', 'pictures']).length, 2);
});

test('a taken name gets a number, ignoring case, and keeps its ending', () => {
  const taken = new Set(['report.pdf', 'report (2).pdf', 'notes']);
  assert.equal(uniqueName('free.pdf', taken), 'free.pdf');
  assert.equal(uniqueName('Report.PDF', taken), 'Report (3).PDF');
  assert.equal(uniqueName('notes', taken), 'notes (2)');
});

test('undo moves are the real moves backwards, last first, and a move that never happened has nothing to undo', () => {
  const done = [
    { name: 'a.pdf', folder: 'Documents', to: 'a.pdf' },
    { name: 'b.pdf', folder: 'Documents', to: 'b (2).pdf' },
    { name: 'c.pdf', folder: 'Documents' },
  ];
  assert.deepEqual(reverseMoves(done), [
    { name: 'b (2).pdf', folder: 'Documents', to: 'b.pdf' },
    { name: 'a.pdf', folder: 'Documents', to: 'a.pdf' },
  ]);
});

test('sizes read the way a person says them', () => {
  assert.equal(formatBytes(0), '0 B');
  assert.equal(formatBytes(1536), '1.5 KB');
  assert.equal(formatBytes(5 * 1024 * 1024), '5 MB');
  assert.equal(formatBytes(2.5 * 1024 ** 3), '2.5 GB');
});

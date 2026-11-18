// Run with: node --test src/pages/sonicHealthHomeLayout.test.mjs   (from local/)
// The Sonic Health Home tab's panels (2026-11-18): the three stacks end on the same line, and (later the same day) sit on SHARED ROWS: the layout has as many rows as the biggest stack has panels, and a stack with fewer
// panels gives its first-needing panels more than one row each (pages/sonicHealthHomeRows.ts), so every panel's top and bottom is a row line the other stacks share. Measured with 12 panels at 1500px: all three stacks end on the same line.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { rowSpans, sharedRows } from './sonicHealthHomeRows.ts';

const css = readFileSync(new URL('./__styles__/SonicHealth.css', import.meta.url), 'utf8').replace(/\r\n/g, '\n');
const home = readFileSync(new URL('./SonicHealthHome.tsx', import.meta.url), 'utf8');

test('the grid stretches its stacks to one height, so every stack ends on the same line', () => {
  assert.match(css, /\.sh-home-layout \{\s+display: grid;\s+grid-template-columns: minmax\(0, 1fr\);\s+gap: 1\.25rem;\s+align-items: stretch;\s+\}/);
  assert.doesNotMatch(css, /\.sh-home-layout \{[^}]*align-items: start;/, 'start let each stack stop wherever its own panels did');
});

test('the panels of a stack share the room that leaves, equally, and the stacks are still plain vertical stacks of panels', () => {
  assert.match(css, /\.sh-home-col,\s+\.sh-home-center \{\s+display: flex;\s+flex-direction: column;\s+gap: 1\.25rem;\s+min-width: 0;\s+\}/);
  assert.match(css, /\.sh-home-col > \*,\s+\.sh-home-center > \* \{\s+flex: 1 1 auto;\s+\}/);
});

test('the three-column, two-column and one-column arrangements are the ones that were there', () => {
  assert.match(css, /@media \(min-width: 1400px\) \{\s+\.sh-home-layout \{\s+grid-template-columns: minmax\(300px, 1fr\) minmax\(560px, 2\.1fr\) minmax\(300px, 1fr\);/);
  assert.match(css, /@media \(min-width: 900px\) \{\s+\.sh-home-layout \{\s+grid-template-columns: repeat\(2, minmax\(0, 1fr\)\);/);
  // and the page still builds its stacks the same way: a left stack, the chart in the middle, a right stack
  assert.equal((home.match(/className="sh-home-col"/g) ?? []).length, 2);
  assert.equal((home.match(/className="sh-home-center"/g) ?? []).length, 1);
});

// ---- shared rows ----

test('a stack gives its spare rows to the panels that need the most height, never fewer than one row each, and always covers exactly the shared rows', () => {
  assert.deepEqual(rowSpans(5, 5), [1, 1, 1, 1, 1]);
  assert.deepEqual(rowSpans(0, 5), []);
  assert.deepEqual(rowSpans(1, 5), [5], 'a lone panel takes every row');
  assert.deepEqual(rowSpans(3, 5), [2, 2, 1], 'with no heights the first panels get them, one each');
  assert.deepEqual(rowSpans(3, 5, [410, 300, 150]), [2, 2, 1]);
  assert.deepEqual(rowSpans(3, 5, [100, 300, 300]), [1, 2, 2], 'a small panel keeps its one row');
  assert.deepEqual(rowSpans(4, 5, [246, 305, 195, 195]), [1, 2, 1, 1]);
  assert.deepEqual(rowSpans(2, 3, [280, 305]), [1, 2]);
  for (const [count, rows] of [[1, 1], [2, 5], [3, 5], [4, 5], [5, 5], [3, 3], [2, 7]]) {
    const heights = Array.from({ length: count }, (_, i) => 100 + i * 70);
    const spans = rowSpans(count, rows, heights);
    assert.equal(spans.length, count);
    assert.equal(spans.reduce((a, b) => a + b, 0), rows, `${count} panels over ${rows} rows`);
    assert.ok(spans.every(n => n >= 1));
  }
  assert.equal(rowSpans(3, 2).reduce((a, b) => a + b, 0), 3, 'more panels than rows still gives each a row');
});

test('the shared rows are as many as the biggest stack has panels, and at least one', () => {
  assert.equal(sharedRows(5, 3, 4), 5);
  assert.equal(sharedRows(2, 3, 3), 3);
  assert.equal(sharedRows(0, 0, 0), 1);
  assert.equal(sharedRows(2), 2);
});

test('the page counts the panels each stack shows by the conditions that draw them, hands every panel its spans, and gives the layout the shared rows', () => {
  assert.match(home, /import \{ rowSpans, sharedRows \} from '\.\/sonicHealthHomeRows';/);
  assert.match(home, /const rows3 = sharedRows\(left\.length, center\.length, right\.length\);/);
  assert.match(home, /const rows2 = sharedRows\(left\.length, right\.length\);/);
  assert.match(home, /\{ '--span3': wide\[i\], '--span2': narrow\[i\] \}/);
  assert.match(home, /<div className="sh-home-layout" style=\{\{ '--sh-rows': rows3, '--sh-rows-pair': rows2 \} as CSSProperties\}>/);
  // every panel takes its spans: each Tile, the chart tile and the who-spent tile
  const tiles = home.match(/<Tile title=/g) ?? [];
  const withRows = home.match(/<Tile title="[^"]+" rows=\{(left|right|center)Rows\(\)\}/g) ?? [];
  assert.equal(tiles.length, 10);
  assert.equal(withRows.length, tiles.length, 'a Tile without its spans would sit on one row and break the shared rows');
  assert.match(home, /<section className="sh-home-tile sh-chart-tile" style=\{centerRows\(\)\}>/);
  assert.match(home, /<WhoSpentTile users=\{health\.users\} onOpen=\{onOpen\} rows=\{centerRows\(\)\} \/>/);
  const who = readFileSync(new URL('./SonicHealthWhoSpent.tsx', import.meta.url), 'utf8');
  assert.equal((who.match(/className="sh-home-tile sh-chart-tile( sh-who)?" style=\{rows\}/g) ?? []).length, 2, 'both the empty and the full who-spent tile take the spans');
  // the left stack has five panels at most, the chart stack three, the right stack four: 12 in all, the same as the panels drawn
  assert.equal((home.match(/rows=\{leftRows\(\)\}/g) ?? []).length, 5);
  assert.equal((home.match(/rows=\{rightRows\(\)\}/g) ?? []).length, 4);
  assert.equal((home.match(/centerRows\(\)/g) ?? []).length, 3);
});

test('each stack is a subgrid over the shared rows where the screen is wide enough, and a plain stack everywhere else', () => {
  const shared = css.slice(css.indexOf('@supports (grid-template-rows: subgrid) {'));
  assert.ok(shared.length > 20, 'the subgrid rules are guarded by @supports, so a browser without subgrid keeps the plain stacks');
  // two stacks under the chart (900px and up): the row above is the chart's; the stacks take the pair rows after it
  assert.match(shared, /@media \(min-width: 900px\) \{\s+\.sh-home-layout \{\s+grid-template-rows: auto repeat\(var\(--sh-rows-pair, 1\), auto\);\s+\}\s+\.sh-home-col \{\s+display: grid;\s+grid-template-rows: subgrid;\s+grid-row: 2 \/ span var\(--sh-rows-pair, 1\);\s+\}\s+\.sh-home-col > \* \{\s+grid-row: span var\(--span2, 1\);/);
  // three stacks side by side (1400px and up): all three start on the first row and cover every shared row
  assert.match(shared, /@media \(min-width: 1400px\) \{\s+\.sh-home-layout \{\s+grid-template-rows: repeat\(var\(--sh-rows, 1\), auto\);\s+\}\s+\.sh-home-col,\s+\.sh-home-center \{\s+display: grid;\s+grid-template-rows: subgrid;\s+grid-row: 1 \/ span var\(--sh-rows, 1\);\s+\}\s+\.sh-home-col > \*,\s+\.sh-home-center > \* \{\s+grid-row: span var\(--span3, 1\);/);
  // below 900px nothing of this applies: the stacks stay flex columns, so a phone's panels keep their own height
  assert.doesNotMatch(css.slice(0, css.indexOf('@supports (grid-template-rows: subgrid) {')), /grid-template-rows: subgrid/);
});

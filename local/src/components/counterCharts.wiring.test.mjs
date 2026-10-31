// Run with: node --test src/components/counterCharts.wiring.test.mjs   (from local/)
// The coding-loop charts are on the Sonic Health behaviour tab, fed from the counters summary's daily series, and every chart can be read without colour.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

const read = name => readFileSync(new URL(name, import.meta.url), 'utf8');

test('the behaviour tab shows the charts above the counters table', () => {
  const page = read('../pages/SonicHealth.tsx');
  assert.match(page, /import CounterCharts from '\.\.\/components\/CounterCharts';/);
  assert.match(page, /tab === 'behavior' && <><CounterCharts counters=\{health\.counters\} \/><CountersCard counters=\{health\.counters\} \/><\/>/);
  assert.match(read('../api.ts'), /daily\?: Array<\{ day: string; counts: Record<string, number> \}>;/);
});

test('every chart has a legend, a caption that says the numbers, and a table', () => {
  const source = read('./CounterCharts.tsx');
  assert.match(source, /<Legend items=/);
  assert.match(source, /aria-live="polite"/);
  assert.match(source, /Show as a table/);
  assert.equal((source.match(/<StackedDays /g) ?? []).length, 2);
  assert.equal((source.match(/<LineDays/g) ?? []).length, 2);
  assert.equal((source.match(/<ComparedBars/g) ?? []).length, 1);
});

test('the charts use only theme colours: no hard-coded hex in the component, and the tokens are defined once in the stylesheet', () => {
  const source = read('./CounterCharts.tsx');
  assert.equal(/#[0-9a-fA-F]{3,6}\b/.test(source), false);
  const css = read('./__styles__/CounterCharts.css');
  for (const token of ['--cc-orange', '--cc-blue', '--cc-gray', '--cc-danger', '--cc-surface', '--cc-ramp-1', '--cc-ramp-2', '--cc-ramp-3', '--cc-ramp-4']) assert.ok(css.includes(`${token}:`), token);
});

test('on a phone the badge sits above the title instead of on top of it', () => {
  const css = read('./__styles__/CounterCharts.css');
  assert.match(css, /@media \(max-width: 640px\) \{\s*\.counter-charts \.card-badge \{ position: static;/);
});

// Run with: node --test src/components/logoMark.test.mjs   (from local/; Node 22.18+ runs the TypeScript module directly)
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { LOGO_DASHED_FROM, LOGO_ORANGE, LOGO_POINTS, LOGO_STROKE, LOGO_VIEWBOX } from './logoMarkData.ts';

// The supplied artwork: the three files are the same drawing in three colours, and the component must draw exactly that drawing.
const supplied = readFileSync(new URL('../assets/sonic_mark_orange.svg', import.meta.url), 'utf8');

test('the mark is the supplied drawing: seven points joined by six lines, the climb to the peak dashed', () => {
  const circles = [...supplied.matchAll(/<circle cx="(\d+)" cy="(\d+)" r="(\d+)"/g)].map(m => m.slice(1).map(Number));
  assert.deepEqual(LOGO_POINTS.map(p => [...p]), circles);
  const lines = [...supplied.matchAll(/<line x1="(\d+)" y1="(\d+)" x2="(\d+)" y2="(\d+)"([^/]*)\/>/g)];
  assert.equal(lines.length, LOGO_POINTS.length - 1);
  lines.forEach((line, index) => {
    assert.deepEqual(line.slice(1, 5).map(Number), [LOGO_POINTS[index][0], LOGO_POINTS[index][1], LOGO_POINTS[index + 1][0], LOGO_POINTS[index + 1][1]]);
    assert.equal(/stroke-dasharray/.test(line[5]), index === LOGO_DASHED_FROM, `segment ${index}`);
    assert.match(line[5], new RegExp(`stroke-width="${LOGO_STROKE}"`));
  });
  assert.ok(supplied.includes(LOGO_ORANGE));
});

test('the three supplied colours are the same drawing, and the box holds every dot with room for the line', () => {
  const shape = text => text.replace(/#[0-9a-f]{6}/gi, '#');
  const orange = shape(supplied);
  for (const name of ['black', 'white']) assert.equal(shape(readFileSync(new URL(`../assets/sonic_mark_${name}.svg`, import.meta.url), 'utf8')), orange, name);
  const [x, y, w, h] = LOGO_VIEWBOX.split(' ').map(Number);
  for (const [cx, cy, r] of LOGO_POINTS) assert.ok(cx - r >= x && cx + r <= x + w && cy - r >= y && cy + r <= y + h, `${cx},${cy}`);
});

test('the component paints the brand orange or the current text colour, and nothing else draws the old swirl', () => {
  const component = readFileSync(new URL('./LogoMark.tsx', import.meta.url), 'utf8');
  assert.match(component, /variant === 'color' \? LOGO_ORANGE : 'currentColor'/);
  assert.ok(!/LOGO_PATH|linearGradient/.test(component));
  const page = readFileSync(new URL('../../index.html', import.meta.url), 'utf8');
  assert.match(page, /rel="icon" type="image\/svg\+xml" href="\/favicon2\.svg"/);
  assert.match(readFileSync(new URL('../../public/favicon2.svg', import.meta.url), 'utf8'), /#f28c28/);
});

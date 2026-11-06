// Run with: node --test src/components/logoMark.test.mjs   (from local/; Node 22.18+ runs the TypeScript module directly)
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { LOGO_DASH, LOGO_EDGES, LOGO_NODES, LOGO_ORANGE, LOGO_STROKE, LOGO_VIEWBOX } from './logoMarkData.ts';

const read = path => readFileSync(new URL(path, import.meta.url), 'utf8');
const circles = text => [...text.matchAll(/<circle cx="(-?[\d.]+)" cy="(-?[\d.]+)" r="([\d.]+)"/g)].map(m => m.slice(1).map(Number));
const lines = text => [...text.matchAll(/<line x1="(-?[\d.]+)" y1="(-?[\d.]+)" x2="(-?[\d.]+)" y2="(-?[\d.]+)"([^/]*)\/>/g)];

test('the mark is a small agent graph: six nodes, a fork and join, a dashed conditional edge and a dashed line back across the middle', () => {
  assert.equal(LOGO_NODES.length, 6);
  assert.equal(LOGO_EDGES.length, 7);
  const edge = (from, to) => LOGO_EDGES.find(e => e[0] === from && e[1] === to);
  for (const [from, to] of [[0, 1], [1, 2], [1, 3], [2, 4], [3, 4], [4, 5]]) assert.ok(edge(from, to), `${from} to ${to}`);          // forward: start, fork into crest and trough, join, end
  assert.deepEqual(LOGO_EDGES.filter(e => e[2]).map(e => [e[0], e[1]]), [[1, 2], [4, 1]]);                                          // exactly two dashed: the conditional step up to the crest, and the return
  const [fork, join] = [LOGO_NODES[1], LOGO_NODES[4]];
  const [crest, trough] = [LOGO_NODES[2], LOGO_NODES[3]];
  assert.ok(Math.abs(fork[1] - join[1]) <= 1, 'the return line is level, straight across the middle');
  assert.ok(crest[1] + crest[2] < fork[1] - 10 && trough[1] - trough[2] > fork[1] + 10, 'the return line passes between the crest and the trough without touching either dot');
  assert.ok(crest[2] > Math.max(...LOGO_NODES.filter(n => n !== crest).map(n => n[2])), 'the crest is the larger dot');
});

test('every dot sits inside the box with room for the line', () => {
  const [x, y, w, h] = LOGO_VIEWBOX.split(' ').map(Number);
  for (const [cx, cy, r] of LOGO_NODES) assert.ok(cx - r - LOGO_STROKE / 2 >= x && cx + r + LOGO_STROKE / 2 <= x + w && cy - r - LOGO_STROKE / 2 >= y && cy + r + LOGO_STROKE / 2 <= y + h, `${cx},${cy}`);
});

test('the three brand files are this drawing in three colours, and the favicon has the same nodes and edges with a heavier line', () => {
  const orange = read('../assets/sonic_mark_orange.svg');
  for (const [name, colour] of [['orange', LOGO_ORANGE], ['white', '#ffffff'], ['black', '#000000']]) {
    const file = read(`../assets/sonic_mark_${name}.svg`);
    assert.deepEqual(circles(file), LOGO_NODES.map(n => [...n]), name);
    const drawn = lines(file);
    assert.equal(drawn.length, LOGO_EDGES.length, name);
    drawn.forEach((line, index) => {
      const [from, to, dashed] = LOGO_EDGES[index];
      assert.deepEqual(line.slice(1, 5).map(Number), [LOGO_NODES[from][0], LOGO_NODES[from][1], LOGO_NODES[to][0], LOGO_NODES[to][1]], `${name} edge ${index}`);
      assert.equal(line[5].includes(`stroke-dasharray="${LOGO_DASH}"`), dashed, `${name} edge ${index}`);
      assert.match(line[5], new RegExp(`stroke="${colour}" stroke-width="${LOGO_STROKE}"`));
    });
  }
  assert.ok(orange.includes(LOGO_ORANGE));
  const favicon = read('../../public/favicon2.svg');
  assert.deepEqual(circles(favicon).map(c => c.slice(0, 2)), LOGO_NODES.map(n => [n[0], n[1]]));
  assert.deepEqual(lines(favicon).map(l => l.slice(1, 5).map(Number)), LOGO_EDGES.map(([from, to]) => [LOGO_NODES[from][0], LOGO_NODES[from][1], LOGO_NODES[to][0], LOGO_NODES[to][1]]));
  assert.equal(lines(favicon).filter(l => l[5].includes('stroke-dasharray')).length, 2);
  assert.ok(favicon.includes(LOGO_ORANGE) && !/#f28c28/i.test(favicon));
});

test('the colour variant is the page accent colour with the brand orange as the fallback, and nothing is an arrow or the old swirl', () => {
  const component = read('./LogoMark.tsx');
  assert.match(component, /variant === 'color' \? \{ color: `var\(--accent-primary, \$\{LOGO_ORANGE\}\)` \} : undefined/);       // var() works in a style, not in a stroke attribute
  assert.match(component, /stroke="currentColor"/);
  assert.match(component, /fill="currentColor"/);
  assert.ok(!/<polygon|marker|LOGO_PATH|linearGradient|f28c28/.test(component));
  assert.equal(LOGO_ORANGE, '#c85a0c');
  assert.match(read('../index.css'), /--accent-primary: #c85a0c;/);                                  // the brand orange is the app's own accent, so the logo and the interface agree
  const page = read('../../index.html');
  assert.match(page, /rel="icon" type="image\/svg\+xml" href="\/favicon2\.svg"/);
});

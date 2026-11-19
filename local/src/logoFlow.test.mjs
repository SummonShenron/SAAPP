// Run with: node --test src/logoFlow.test.mjs   (from local/)
// The home page's animated mark (2026-11-18): the two dashed lines flow RIGHT TO LEFT (components/logoMarkData.ts logoFlowOffset, LogoMark `animated`, index.css .logo-flow), and the home page's phone layout
// puts its buttons on the column's centre line.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { LOGO_DASH, LOGO_DASH_PERIOD, LOGO_EDGES, LOGO_NODES, logoFlowOffset } from './components/logoMarkData.ts';

const read = name => readFileSync(new URL(name, import.meta.url), 'utf8').replace(/\r\n/g, '\n');
const css = read('./index.css');
const mark = read('./components/LogoMark.tsx');
const landing = read('./pages/LandingPage.tsx');

test('one loop moves the dashes by exactly one dash and one gap', () => {
  assert.equal(LOGO_DASH, '6 5');
  assert.equal(LOGO_DASH_PERIOD, 11);
});

test('only the dashed lines move, and each runs right to left: toward its end when it is drawn right to left, toward its start when it is drawn left to right', () => {
  const dashed = LOGO_EDGES.filter(edge => edge[2]);
  assert.equal(dashed.length, 2, 'the diagonal up to the crest and the line across the middle');
  for (const edge of LOGO_EDGES) {
    const offset = logoFlowOffset(edge);
    if (!edge[2]) { assert.equal(offset, null, 'a solid line stays still'); continue; }
    const [from, to] = edge;
    const drawnRightToLeft = LOGO_NODES[to][0] < LOGO_NODES[from][0];
    // dashes move toward an edge's END as the offset decreases: leftward means decreasing when it is drawn right to left, increasing when it is drawn left to right.
    // The one exception, by choice (2026-11-18): the diagonal up to the crest (1 to 2) runs LEFT TO RIGHT, up toward the crest, so its offset decreases although it is drawn left to right.
    const crest = from === 1 && to === 2;
    assert.equal(offset, crest || drawnRightToLeft ? -LOGO_DASH_PERIOD : LOGO_DASH_PERIOD, `edge ${from}-${to}`);
  }
  // the two real ones, by name
  const across = LOGO_EDGES.find(([from, to, dashed]) => dashed && LOGO_NODES[from][1] === LOGO_NODES[to][1]);
  const diagonal = LOGO_EDGES.find(([from, to, dashed]) => dashed && LOGO_NODES[from][1] !== LOGO_NODES[to][1]);
  assert.equal(logoFlowOffset(across), -11, 'across the middle: drawn right to left, offset goes to -11');
  assert.equal(logoFlowOffset(diagonal), -11, 'the diagonal up to the crest: drawn left to right and flowing left to right, so the offset goes to -11');
});

test('the mark is animated only where it asks to be, with its own direction on each line, and the animation holds still for reduced motion', () => {
  assert.match(mark, /animated\?: boolean/);
  assert.match(mark, /const flow = animated \? logoFlowOffset\(edge\) : null;/);
  assert.match(mark, /className=\{flow !== null \? 'logo-flow' : undefined\}/);
  assert.match(mark, /'--logo-flow-to': `\$\{flow\}px`/);
  assert.match(landing, /<LogoMark className="landing-logo" variant="mono" size=\{64\} label="Sonic Graph Technologies" animated \/>/);
  assert.equal((read('./components/Rail.tsx').match(/<LogoMark[^>]*animated/g) ?? []).length, 0, 'the rail\'s small mark stays still');
  assert.match(css, /\.logo-flow \{ animation: logo-flow 1\.4s linear infinite; \}/);
  assert.match(css, /@keyframes logo-flow \{ to \{ stroke-dashoffset: var\(--logo-flow-to\); \} \}/);
  assert.match(css, /@media \(prefers-reduced-motion: reduce\) \{ \.logo-flow, \.logo-pulse \{ animation: none; \} \}/);
});

test('the dots of the home page mark pulse (their opacity goes down to 40% and back every 2 s), and only when the mark is animated', () => {
  assert.match(mark, /className=\{animated \? 'logo-pulse' : undefined\}/);
  assert.match(css, /\.logo-pulse \{ animation: logo-pulse 2s ease-in-out infinite; \}/);
  assert.match(css, /@keyframes logo-pulse \{\s+0%, 100% \{ opacity: 1; \}\s+50% \{ opacity: 0\.4; \}\s+\}/);
  assert.equal((mark.match(/logo-pulse/g) ?? []).length, 1, 'only the dots, not the lines');
});

test('on a desktop both buttons sit on the centre line of their own panel and are the same width, the left panel centred like the right', () => {
  assert.match(css, /\.landing-left \{\s+padding: [^;]+;\s+background: var\(--ld-left-bg\);\s+z-index: 2;[^}]*align-items: center;\s+text-align: center;\s+\}/);
  assert.match(css, /\.landing-left > \.landing-id \{ width: 100%; \}/);
  assert.match(css, /\.landing-signin \{ margin: 1\.2rem 0 1rem; align-items: center; \}/);
  const desktop = css.slice(0, css.indexOf('/* --- Mobile Responsive Overrides --- */'));
  assert.match(desktop, /\.landing-signin \.enter-btn \{ width: 100%; max-width: none; \}/, 'the sign-in button is as wide as the guest button outside the phone layout too');
  assert.match(css, /\.landing-right \{\s+align-items: center;/, 'the right panel was already centred');
  assert.match(css, /\.landing-signin, \.landing-guest \{ display: flex; flex-direction: column; gap: 0\.9rem; width: 100%; max-width: 320px; \}/);          // one width for both button columns
});

test('on two columns the panels share three rows, so both buttons sit on the same row and the title and the logo end on the line above them', () => {
  const wide = css.slice(css.indexOf('@media (min-width: 769px) {\n  .landing-row {'));
  assert.match(wide, /\.landing-row \{\s+display: grid;\s+grid-template-columns: minmax\(0, 1fr\) minmax\(0, 1fr\);\s+grid-template-rows: 1fr auto 1fr;\s+\}/);
  assert.match(wide, /\.landing-left,\s+\.landing-right \{\s+display: grid;\s+grid-template-rows: subgrid;\s+grid-row: 1 \/ span 3;/);
  assert.match(wide, /\.landing-left > \.landing-id,\s+\.landing-brand \{ align-self: end; \}/);
  assert.match(wide, /\.landing-row \.landing-signin,\s+\.landing-row \.landing-guest \{ align-self: start; margin: 1\.3rem 0 0; \}/);          // the same space above both buttons, and stronger than the margins they have below
  assert.match(css, /\.landing-signin \.enter-btn, \.landing-guest \.enter-btn \{ height: 44px; box-sizing: border-box; \}/);          // one height for both: the guest button has a border and the sign-in button none
  assert.match(wide, /\.landing-foot \{ align-self: start;/);
  // the page gives each panel its parts: the title; the sign-in button; the notice and links together; the logo and name together; the guest button
  assert.match(landing, /<div className="landing-brand">\s+<LogoMark[^>]*animated \/>\s+<div className="landing-name">Sonic Graph Technologies<\/div>\s+<\/div>/);
  assert.match(landing, /<div className="landing-foot">\s+\{showNotice && \(\s+<div className="landing-notice">/);
  assert.match(landing, /<div className="landing-links">[\s\S]*?<\/div>\s+<\/div>\s+<\/div>\s+<div className="landing-right">/);
  // a phone is one column in its own order: the wrappers dissolve there
  assert.match(css, /\.landing-brand, \.landing-foot \{ display: contents; \}/);
});

test('on a phone the sign-in button is as wide as the guest button and on the same centre line', () => {
  const mobile = css.slice(css.indexOf('/* --- Mobile Responsive Overrides --- */'));
  assert.match(mobile, /\.landing-signin \{ order: 3; max-width: 100%; margin: 0; align-items: center; \}/);
  assert.match(mobile, /\.landing-signin \.enter-btn \{ width: 100%; max-width: none; \}/);
  assert.match(mobile, /\.landing-guest \{ order: 4; max-width: 100%; margin: 0; \}/);
  assert.match(css, /\.landing-guest \.enter-btn \{ width: 100%; max-width: none; \}/);          // the guest button already was
  assert.match(mobile, /\.landing-row \{[^}]*align-items: center; text-align: center; \}/);
});

// Run with: node --experimental-test-module-mocks --test src/components/stepsStyle.test.mjs   (from local/; Node 22.18+ runs the TypeScript module directly)
// Whether the steps are drawn as a graph (components/stepsStyle.ts rules, components/useStepsGraph.ts hook).
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { GRAPH_MIN_WIDTH_PX, STEPS_STYLE_KEY, readStepsStyle, stepsGraphOn } from './stepsStyle.ts';

const read = name => readFileSync(new URL(name, import.meta.url), 'utf8').replace(/\r\n/g, '\n');

test('the steps are a graph on a wide window unless someone has switched it off, and the plain line on a narrow one', () => {
  assert.equal(stepsGraphOn(null, true), true);
  assert.equal(stepsGraphOn('graph', true), true);
  assert.equal(stepsGraphOn('anything else', true), true, 'only the word "line" switches it off');
  assert.equal(stepsGraphOn('line', true), false);
  assert.equal(stepsGraphOn(null, false), false, 'a phone keeps the width for the text');
  assert.equal(stepsGraphOn('graph', false), false);
  assert.equal(stepsGraphOn('line', false), false);
});

test('a missing or blocked store is no choice, not an error', () => {
  assert.equal(readStepsStyle(), null, 'here there is no window at all');
  globalThis.window = { localStorage: { getItem() { throw new Error('blocked'); } } };
  try {
    assert.equal(readStepsStyle(), null);
    globalThis.window = { localStorage: { getItem: key => (key === STEPS_STYLE_KEY ? 'line' : null) } };
    assert.equal(readStepsStyle(), 'line');
  } finally {
    delete globalThis.window;
  }
  assert.equal(STEPS_STYLE_KEY, 'stepsStyle');
});

test('the hook follows the window\'s width live and reads the stored choice once', () => {
  const hook = read('./useStepsGraph.ts');
  assert.match(hook, /useMediaQuery\(`\(min-width: \$\{GRAPH_MIN_WIDTH_PX\}px\)`\)/);
  assert.match(hook, /const \[stored\] = useState\(readStepsStyle\);/);
  assert.match(hook, /return stepsGraphOn\(stored, wide\);/);
  assert.ok(GRAPH_MIN_WIDTH_PX >= 640 && GRAPH_MIN_WIDTH_PX <= 800, 'a phone is below it, a tablet and up are above');
  // nothing in the app writes the choice: there is no setting for it
  const sources = ['../pages/Chat.tsx', './StepsDisclosure.tsx', './TraceSteps.tsx', './useStepsGraph.ts', './ComposerChips.tsx'].map(read).join('\n');
  assert.doesNotMatch(sources, /setItem\(\s*STEPS_STYLE_KEY|setItem\('stepsStyle'/);
});

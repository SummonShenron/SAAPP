// Run with: node --test src/components/stepsPreference.test.mjs   (from local/)
// 2026-11-18: collapse the steps once and the next messages stay collapsed too (stepsPreference.ts, StepsDisclosure.tsx).
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { STEPS_OPEN_KEY, defaultStepsOpen, parseStepsPreference, readStepsPreference, writeStepsPreference } from './stepsPreference.ts';
import { stepsAreOpen } from './traceFacts.ts';

const read = name => readFileSync(new URL(name, import.meta.url), 'utf8').replace(/\r\n/g, '\n');
const withStorage = (storage, body) => {
  const before = globalThis.window;
  globalThis.window = { localStorage: storage };
  try { return body(); } finally { if (before === undefined) delete globalThis.window; else globalThis.window = before; }
};
const memory = () => { const map = new Map(); return { getItem: key => (map.has(key) ? map.get(key) : null), setItem: (key, value) => { map.set(key, String(value)); }, map }; };

test('what is stored is closed, open or nothing: anything else this page did not write is no choice', () => {
  assert.equal(parseStepsPreference('closed'), false);
  assert.equal(parseStepsPreference('open'), true);
  for (const other of [null, '', 'true', 'false', 'CLOSED', '1', 'collapsed', 'toString']) assert.equal(parseStepsPreference(other), null, String(other));
});

test('the newest turn\'s steps start open unless they were collapsed; any other reply starts closed whatever was chosen', () => {
  assert.equal(defaultStepsOpen(true, null), true, 'nothing chosen: open, as before');
  assert.equal(defaultStepsOpen(true, true), true);
  assert.equal(defaultStepsOpen(true, false), false, 'collapsed once: the next turn starts collapsed');
  for (const preference of [null, true, false]) assert.equal(defaultStepsOpen(false, preference), false, 'an older reply folds');
});

test('this turn\'s own press still wins over the remembered choice, either way', () => {
  assert.equal(stepsAreOpen(true, defaultStepsOpen(true, false)), true, 'opened on a turn that started collapsed');
  assert.equal(stepsAreOpen(false, defaultStepsOpen(true, null)), false, 'collapsed on a turn that started open');
  assert.equal(stepsAreOpen(null, defaultStepsOpen(true, false)), false, 'and with no press it follows the remembered choice');
});

test('the choice is remembered in the browser: written as closed or open, read back, and a later choice replaces it', () => {
  const storage = memory();
  withStorage(storage, () => {
    assert.equal(readStepsPreference(), null, 'nothing before anyone has chosen');
    writeStepsPreference(false);
    assert.equal(storage.map.get(STEPS_OPEN_KEY), 'closed');
    assert.equal(readStepsPreference(), false);
    writeStepsPreference(true);
    assert.equal(storage.map.get(STEPS_OPEN_KEY), 'open');
    assert.equal(readStepsPreference(), true);
  });
  assert.equal(STEPS_OPEN_KEY, 'stepsOpen');
});

test('blocked or missing storage is not an error: no choice is read, and writing does nothing', () => {
  const blocked = { getItem: () => { throw new Error('blocked'); }, setItem: () => { throw new Error('blocked'); } };
  withStorage(blocked, () => {
    assert.equal(readStepsPreference(), null);
    assert.doesNotThrow(() => writeStepsPreference(false));
  });
  assert.equal(globalThis.window, undefined);
  assert.equal(readStepsPreference(), null, 'no window at all');
  assert.doesNotThrow(() => writeStepsPreference(true));
});

test('the steps line uses it: a new turn reads the remembered choice once, and pressing the CURRENT turn\'s line (the running one or the newest reply) writes the new one, an older reply\'s does not', () => {
  const disclosure = read('./StepsDisclosure.tsx');
  assert.match(disclosure, /import \{ defaultStepsOpen, readStepsPreference, writeStepsPreference \} from '\.\/stepsPreference';/);
  assert.match(disclosure, /const \[preference\] = useState\(readStepsPreference\);/, 'read when the turn\'s steps first appear (a lazy read, once)');
  assert.match(disclosure, /const open = stepsAreOpen\(chosen, defaultStepsOpen\(running \|\| latest, preference\)\);/);
  assert.match(disclosure, /const toggle = \(\) => \{\s+setChosen\(!open\);\s+if \(running \|\| latest\) writeStepsPreference\(!open\);\s+\};/);
  assert.equal((disclosure.match(/onToggle=\{toggle\}/g) ?? []).length, 2, 'both the running line and the finished one press the same way');
  assert.equal((disclosure.match(/writeStepsPreference\(/g) ?? []).length, 1, 'written in one place only');
});

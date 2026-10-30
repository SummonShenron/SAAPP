// Run with: node --test src/components/composerChips.test.mjs   (from local/; Node 22.18+ runs the TypeScript module directly)
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { CHIPS, answersId, answersValue, chipLabel, isChanged, modeId, modeValue, optionOf, thinkingId, thinkingValue } from './composerChipsLogic.ts';

test('each setting is one icon with exactly two states, and the quiet one is the ordinary state', () => {
  for (const spec of Object.values(CHIPS)) {
    assert.equal(spec.options.length, 2, spec.key);
    assert.ok(spec.options.some(option => option.id === spec.ordinary), `${spec.key} has its ordinary state among its options`);
    for (const option of spec.options) assert.ok(option.name && option.hint, `${spec.key}/${option.id} says what it is and what it does`);
  }
  assert.deepEqual(Object.values(CHIPS).map(spec => spec.ordinary), ['gemini', 'agent', 'swift', 'grounded']);
});

test('the names the person sees', () => {
  assert.deepEqual(CHIPS.mode.options.map(option => option.name), ['Plan', 'Agent']);
  assert.deepEqual(CHIPS.thinking.options.map(option => option.name), ['Swift', 'Deep']);
  assert.deepEqual(CHIPS.answers.options.map(option => option.name), ['Grounded', 'Open']);
  assert.deepEqual(CHIPS.model.options.map(option => option.name), ['Gemini', 'Claude']);
  assert.equal(chipLabel('thinking', 'swift'), 'Thinking: Swift');
  assert.equal(chipLabel('model', 'claude'), 'Code model: Claude');
});

test('an unknown value reads as the ordinary state, and only the other state is "changed"', () => {
  assert.equal(optionOf('thinking', 'nonsense').id, 'swift');
  assert.equal(isChanged('thinking', 'swift'), false);
  assert.equal(isChanged('thinking', 'deep'), true);
  assert.equal(isChanged('mode', 'agent'), false);
  assert.equal(isChanged('mode', 'plan'), true);
  assert.equal(isChanged('answers', 'grounded'), false);
  assert.equal(isChanged('answers', 'open'), true);
  assert.equal(isChanged('model', 'gemini'), false);
  assert.equal(isChanged('model', 'claude'), true);
});

test('the chips map to the settings the page already keeps, both ways, with the old defaults unchanged', () => {
  assert.equal(modeId(false), 'agent'); assert.equal(modeId(true), 'plan');                    // "Plan first" off is Agent
  assert.equal(modeValue('plan'), true); assert.equal(modeValue('agent'), false);
  assert.equal(thinkingId(false), 'swift'); assert.equal(thinkingId(true), 'deep');            // "Deep Thinking" off is Swift
  assert.equal(thinkingValue('deep'), true); assert.equal(thinkingValue('swift'), false);
  assert.equal(answersId('strict'), 'grounded'); assert.equal(answersId('open'), 'open');       // the server's two modes
  assert.equal(answersId(''), 'grounded');
  assert.equal(answersValue('open'), 'open'); assert.equal(answersValue('grounded'), 'strict');
  for (const id of ['plan', 'agent']) assert.equal(modeId(modeValue(id)), id);
  for (const id of ['swift', 'deep']) assert.equal(thinkingId(thinkingValue(id)), id);
  for (const id of ['grounded', 'open']) assert.equal(answersId(answersValue(id)), id);
});

test('the message box has the four chips with Export and Help at the far right, and no "..." menu outside the embedded widget', () => {
  const chat = readFileSync(new URL('../pages/Chat.tsx', import.meta.url), 'utf8');
  assert.match(chat, /<ComposerChips/);
  assert.match(chat, /\{!isEmbedded && \(\s*<ComposerChips/);                                  // not in the embedded widget
  assert.match(chat, /\{isEmbedded && \(\s*<div style=\{\{ position: "relative" \}\} ref=\{optionsMenuRef\}>/);   // the "..." button and its menu are the embedded widget's only
  assert.match(chat, /onExport=\{handleExportChat\}/);
  assert.doesNotMatch(chat, /Export this conversation/);                                        // no longer a button in the message box row itself
  assert.doesNotMatch(chat, /showScopePicker/);                                                 // the scope lives in the column and the phone strip's lock
  assert.doesNotMatch(chat, /Open Answers:|Deep Thinking:|Plan first:|Code model:/);            // those toggles are the chips now
  const chips = readFileSync(new URL('./ComposerChips.tsx', import.meta.url), 'utf8');
  assert.match(chips, /aria-label="Help"/);                                                      // Help at the far right
  assert.match(chips, /<div className="cc-right">\s*<button[^>]*aria-label="Export this conversation"[\s\S]*?aria-label="Help"/);      // with Export just before it, in the same group
  assert.match(chips, /ClaudeMark/); assert.match(chips, /GeminiMark/); assert.match(chips, /RobotMark/);
});

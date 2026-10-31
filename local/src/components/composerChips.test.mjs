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
  assert.match(chat, /\{!isEmbedded && !columnVisible && !railVisible && composerChips\('bar'\)\}/);            // under the box, not in the embedded widget, and only when the session column is not showing
  assert.match(chat, /controls=\{composerChips\('panel'\)\}/);                                 // otherwise they are the column's "Chat settings" card (never both)
  assert.match(chat, /const chatSettings: ChatSettings = \{/);                                  // the settings are built once ...
  assert.match(chat, /const composerChips = \(layout: 'bar' \| 'panel'\) => <ComposerChips \{\.\.\.chatSettings\} layout=\{layout\} \/>;/);   // ... for the bar and the column's card
  assert.match(chat, /<SessionRail[^>]*settings=\{chatSettings\}|settings=\{chatSettings\}\s*\/>/);       // ... and the phone strip
  assert.match(chat, /\{isEmbedded && \(\s*<div style=\{\{ position: "relative" \}\} ref=\{optionsMenuRef\}>/);   // the "..." button and its menu are the embedded widget's only
  assert.match(chat, /onExport: handleExportChat,/);
  assert.doesNotMatch(chat, /Export this conversation/);                                        // no longer a button in the message box row itself
  assert.doesNotMatch(chat, /showScopePicker/);                                                 // the scope lives in the column and the phone strip's lock
  assert.doesNotMatch(chat, /Open Answers:|Deep Thinking:|Plan first:|Code model:/);            // those toggles are the chips now
  const chips = readFileSync(new URL('./ComposerChips.tsx', import.meta.url), 'utf8');
  assert.match(chips, /aria-label="Help"/);                                                      // Help at the far right
  assert.match(chips, /<div className="cc-right">\s*<button[^>]*aria-label="Export this conversation"[\s\S]*?aria-label="Help"/);      // with Export just before it, in the same group
  assert.match(chips, /ClaudeMark/); assert.match(chips, /GeminiMark/); assert.match(chips, /RobotMark/);
});

test('in the session column the chips are a card below "This conversation", their choices open inline (the column scrolls and is narrower than a popup), and the bar keeps its floating popups', () => {
  const column = readFileSync(new URL('./SessionColumn.tsx', import.meta.url), 'utf8');
  const conversation = column.indexOf('<Card id="conversation"');
  const controls = column.indexOf('<Card id="controls" label="Chat settings" title="Chat settings"');
  assert.ok(conversation > 0 && controls > conversation, 'Chat settings comes after the scope card');
  assert.match(column, /controls\?: React\.ReactNode;/);
  const chips = readFileSync(new URL('./ComposerChips.tsx', import.meta.url), 'utf8');
  assert.match(chips, /layout\?: 'bar' \| 'panel';/);
  assert.match(chips, /\{!inline && open === key && choices\(key, control\)\}/);                  // the bar: the popup floats inside its chip
  assert.match(chips, /\{open && open !== 'help' && openControl && choices\(open, openControl\)\}/);  // the panel: it is listed under the row
  const css = readFileSync(new URL('./__styles__/ComposerChips.css', import.meta.url), 'utf8');
  assert.match(css, /\.composer-chips-panel \.cc-pop, \.composer-chips-panel \.cc-pop-help \{ position: static; width: auto;/);
});

test('on a phone the settings are buttons one under another down the strip, below the information buttons, and one card is open at a time', () => {
  const rail = readFileSync(new URL('./SessionRail.tsx', import.meta.url), 'utf8');
  assert.match(rail, /settings\?: ChatSettings \| null;/);
  assert.match(rail, /const CHIP_ORDER: ChipKey\[\] = \['model', 'mode', 'thinking', 'answers'\];/);
  assert.ok(rail.indexOf("open === 'scope'") < rail.indexOf('{settings && ('), 'the chat settings come after GitHub, the rings and the lock');
  assert.match(rail, /<ChipChoices chipKey=\{key\} control=\{control\} onDone=\{\(\) => setOpen\(null\)\} className="rail-pop rail-choices" \/>/);
  assert.match(rail, /aria-label="Export this conversation"/);
  assert.match(rail, /className="rail-pop rail-help" role="dialog" aria-label="Help"/);
  assert.match(rail, /if \(!repo && !hasGauges && !scope && !settings\) return null;/);       // a guest has none of the information buttons but still has the settings
  assert.match(rail, /type Panel = 'repo' \| 'gauges' \| 'scope' \| ChipKey \| 'help';/);        // one state, so one card at a time
  const css = readFileSync(new URL('./__styles__/SessionRail.css', import.meta.url), 'utf8');
  assert.match(css, /\.rail-btn\.is-changed \{[^}]*accent-primary/);
});

test('in the column the settings are three across in two rows, with Export and Help as cells of the same grid', () => {
  const css = readFileSync(new URL('./__styles__/ComposerChips.css', import.meta.url), 'utf8');
  assert.match(css, /\.composer-chips-panel \.cc-row \{ display: grid; grid-template-columns: repeat\(3, minmax\(0, 1fr\)\); gap: 8px; \}/);
  assert.match(css, /\.composer-chips-panel \.cc-right \{ display: contents; \}/);              // the group at the bar's right end steps aside
  assert.match(css, /\.composer-chips-panel \.cc-btn \{ width: 100%; \}/);
});

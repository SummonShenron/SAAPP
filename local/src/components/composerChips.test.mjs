// Run with: node --test src/components/composerChips.test.mjs   (from local/; Node 22.18+ runs the TypeScript module directly)
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { BASE_MODEL, CHIPS, answersId, answersValue, chipLabel, isChanged, modeId, modeValue, modelChipValue, modelsChanged, modelsLabel, optionOf, specOption, thinkingId, thinkingValue } from './composerChipsLogic.ts';

test('each setting is one icon with its states (two, and four for the code model), and the quiet one is the ordinary state', () => {
  for (const spec of Object.values(CHIPS)) {
    assert.equal(spec.options.length, spec.key === 'model' ? 4 : 2, spec.key);          // Gemini, Claude, Claude Sonnet and GPT-5.4 mini (each offered only where the server has what it needs)
    assert.ok(spec.options.some(option => option.id === spec.ordinary), `${spec.key} has its ordinary state among its options`);
    for (const option of spec.options) assert.ok(option.name && option.hint, `${spec.key}/${option.id} says what it is and what it does`);
  }
  assert.deepEqual(Object.values(CHIPS).map(spec => spec.ordinary), ['gemini', 'agent', 'swift', 'grounded']);
});

test('the names the person sees', () => {
  assert.deepEqual(CHIPS.mode.options.map(option => option.name), ['Plan', 'Agent']);
  assert.deepEqual(CHIPS.thinking.options.map(option => option.name), ['Swift', 'Deep']);
  assert.deepEqual(CHIPS.answers.options.map(option => option.name), ['Grounded', 'Open']);
  assert.deepEqual(CHIPS.model.options.map(option => option.name), ['Gemini', 'Claude', 'Claude Sonnet', 'GPT-5.4 mini']);
  assert.equal(chipLabel('thinking', 'swift'), 'Thinking: Swift');
  assert.equal(chipLabel('model', 'claude'), 'Code model: Claude');
  assert.equal(chipLabel('model', 'sonnet'), 'Code model: Claude Sonnet');
  assert.equal(chipLabel('model', 'gpt'), 'Code model: GPT-5.4 mini');
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
  assert.equal(isChanged('model', 'sonnet'), true);
  assert.equal(isChanged('model', 'gpt'), true);
});

test('Sonnet is left out of the popup unless the server offers it, says what it costs, and draws the Claude mark', () => {
  const chips = readFileSync(new URL('./ComposerChips.tsx', import.meta.url), 'utf8');
  assert.match(chips, /spec\.options\.filter\(option => !control\.hide\?\.includes\(option\.id\)\)/);
  assert.match(chips, /id === 'claude' \|\| id === 'sonnet'\) return <ClaudeMark \/>/);
  const chat = readFileSync(new URL('../pages/Chat.tsx', import.meta.url), 'utf8');
  assert.match(chat, /hide: \[\.\.\.\(claudeAvailable \? \[\] : \['claude'\]\), \.\.\.\(sonnetAvailable \? \[\] : \['sonnet'\]\), \.\.\.\(gptAvailable \? \[\] : \['gpt'\]\)\]/);
  assert.match(chat, /setSonnetAvailable\(data\.sonnet_available === true\)/);
  assert.match(chat, /setGptAvailable\(data\.gpt_available === true\)/);
  assert.match(optionOf('model', 'sonnet').hint, /about 20 times the cost of Claude/);
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
  assert.doesNotMatch(chat, /composerChips\(/);                                              // no builder with a 'bar' layout any more: nothing is drawn under the message box
  assert.match(chat, /controls=\{settingsPanel\}/);                                           // the column's "Chat settings" card
  assert.match(chat, /const chatSettings: ChatSettings = \{/);                                  // the settings are built once ...
  assert.match(chat, /const settingsPanel = <ComposerChips \{\.\.\.chatSettings\} \/>;/);   // ... for the column's card
  assert.match(chat, /<SessionRail[\s\S]*?settings=\{chatSettings\}/);       // ... and the phone strip
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

test('in the session column the chips are a card below "This conversation", their choices open inline (the column scrolls and is narrower than a popup), and there is no floating-popup bar any more', () => {
  const column = readFileSync(new URL('./SessionColumn.tsx', import.meta.url), 'utf8');
  const conversation = column.indexOf('<Card id="conversation"');
  const controls = column.indexOf('<Card id="controls" label="Chat settings" title="Chat settings"');
  assert.ok(conversation > 0 && controls > conversation, 'Chat settings comes after the scope card');
  assert.match(column, /controls\?: React\.ReactNode;/);
  const chips = readFileSync(new URL('./ComposerChips.tsx', import.meta.url), 'utf8');
  assert.doesNotMatch(chips, /layout|inline/);                                                    // the 'bar' layout is gone: nothing is drawn under the message box
  assert.match(chips, /const ComposerChips: React\.FC<ChatSettings> = /);
  assert.match(chips, /\{open && open !== 'help' && open !== 'model' && openControl && choices\(open, openControl\)\}/);  // the open choice is listed under the row
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
  assert.match(rail, /if \(!repo && !hasGauges && !scope && !settings && !connection\) return null;/);       // a guest has none of the information buttons but still has the settings
  assert.match(rail, /type Panel = 'connection' \| 'repo' \| 'gauges' \| 'scope' \| ChipKey \| 'help';/);        // one state, so one card at a time
  const css = readFileSync(new URL('./__styles__/SessionRail.css', import.meta.url), 'utf8');
  assert.match(css, /\.rail-btn\.is-changed \{[^}]*accent-primary/);
});

test('in the column the settings are three across in two rows, with Export and Help as cells of the same grid', () => {
  const css = readFileSync(new URL('./__styles__/ComposerChips.css', import.meta.url), 'utf8');
  assert.match(css, /\.composer-chips-panel \.cc-row \{ display: grid; grid-template-columns: repeat\(3, minmax\(0, 1fr\)\); gap: 8px; \}/);
  assert.match(css, /\.composer-chips-panel \.cc-right \{ display: contents; \}/);              // the group at the bar's right end steps aside
  assert.match(css, /\.composer-chips-panel \.cc-btn \{ width: 100%; \}/);
});

test('the settings are only in the side panel, on every screen: never under the message box, hidden with the strip on a phone and with the column on a wide screen', () => {
  const chat = readFileSync(new URL('../pages/Chat.tsx', import.meta.url), 'utf8');
  assert.doesNotMatch(chat, /layout="bar"|layout=\{layout\}|composerChips\(/);               // no way to draw them under the box
  assert.equal((chat.match(/<ComposerChips /g) ?? []).length, 1);                              // built once, for the column card ...
  assert.match(chat, /settings=\{chatSettings\}/);                                             // ... and the same object feeds the phone strip
  assert.match(chat, /const railVisible = compactScreen && showSessionColumn;/);              // the strip is what the top-bar button shows and hides on a narrow screen
});

test('the model chip is two settings in one popup: the Base model (who writes the reply) and the Code model (who does the code steps)', () => {
  assert.equal(BASE_MODEL.title, 'Base model');
  assert.equal(CHIPS.model.title, 'Code model');
  assert.deepEqual(BASE_MODEL.options.map(option => option.id), ['gemini', 'gpt']);
  assert.equal(BASE_MODEL.ordinary, 'gemini');
  for (const option of [...BASE_MODEL.options, ...CHIPS.model.options]) assert.ok(option.short && option.short.length <= 40, `${option.id} has a short line that fits a half-width column`);
  assert.equal(specOption(BASE_MODEL, 'nonsense').id, 'gemini');                                 // an unknown value reads as Gemini
  assert.match(specOption(BASE_MODEL, 'gpt').hint, /Gemini whatever this says/);                  // the safety rule is stated where the choice is made
});

test('the model chip shows the first model that is not the ordinary one, Base first, and is highlighted when either differs', () => {
  assert.equal(modelChipValue('gemini', 'gemini'), 'gemini');
  assert.equal(modelChipValue('gpt', 'claude'), 'gpt');
  assert.equal(modelChipValue('gemini', 'sonnet'), 'sonnet');
  assert.equal(modelChipValue(null, 'claude'), 'claude');                                         // no base choice offered: only the code model counts
  assert.equal(modelChipValue('gpt', null), 'gpt');
  assert.equal(modelChipValue(null, null), 'gemini');
  assert.equal(modelsChanged('gemini', 'gemini'), false);
  assert.equal(modelsChanged(null, null), false);
  assert.equal(modelsChanged('gpt', 'gemini'), true);
  assert.equal(modelsChanged('gemini', 'gpt'), true);
  assert.equal(modelsChanged(null, 'claude'), true);
});

test('the model chip names both settings, and leaves out one that is not offered', () => {
  assert.equal(modelsLabel('gpt', 'claude'), 'Models: Base GPT-5.4 mini, Code Claude');
  assert.equal(modelsLabel('gemini', 'gemini'), 'Models: Base Gemini, Code Gemini');
  assert.equal(modelsLabel(null, 'sonnet'), 'Models: Code Claude Sonnet');
  assert.equal(modelsLabel('gpt', null), 'Models: Base GPT-5.4 mini');
});

test('the two-column popup and its wiring: ModelChoices in the column card and the phone strip, the base model from its own setting, GPT drawn with its own mark', () => {
  const chips = readFileSync(new URL('./ComposerChips.tsx', import.meta.url), 'utf8');
  assert.match(chips, /export function ModelChoices\(/);
  assert.match(chips, /\{base && <OptionList spec=\{BASE_MODEL\} control=\{base\} onDone=\{onDone\} compact \/>\}/);   // Base is the first column
  assert.match(chips, /\{code && <OptionList spec=\{CHIPS\.model\} control=\{code\} onDone=\{onDone\} compact \/>\}/);
  assert.match(chips, /\{open === 'model' && <ModelChoices base=\{baseModel\} code=\{model\}/);
  assert.match(chips, /id === 'gpt'\) return <GptMark \/>/);
  const rail = readFileSync(new URL('./SessionRail.tsx', import.meta.url), 'utf8');
  assert.match(rail, /<ModelChoices base=\{settings\.baseModel\} code=\{settings\.model\}/);
  const chat = readFileSync(new URL('../pages/Chat.tsx', import.meta.url), 'utf8');
  assert.match(chat, /baseModel: replyModelAvailable \? \{ value: replyModel, onChange: id => \{ void handleReplyModelChange\(id as ReplyModel\); \} \} : null,/);
  assert.match(chat, /setReplyModelAvailable\(data\.available\)/);
  const css = readFileSync(new URL('./__styles__/ComposerChips.css', import.meta.url), 'utf8');
  assert.match(css, /\.cc-cols \{ display: grid; grid-template-columns: repeat\(auto-fit, minmax\(120px, 1fr\)\)/);
  assert.ok(css.indexOf('.cc-pop.cc-cols') < css.indexOf('.composer-chips-panel .cc-pop,'), 'the column card override (width: auto) comes after the popup width, so it wins');
});

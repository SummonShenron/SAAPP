// Run with: node --test src/components/helpMode.test.mjs   (from local/; Node 22.18+ runs the TypeScript module directly)
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { test } from 'node:test';
import { CARD_MARGIN, NO_TIP, helpIdsIn, isTextField, keyOpensHelp, placeCard, tipFor } from './helpModeLogic.ts';
import { HELP_TIPS } from './helpTips.ts';

const SRC = fileURLToPath(new URL('..', import.meta.url));

function sources(dir = SRC) {
  const found = [];
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) found.push(...sources(path));
    else if (/\.tsx$/.test(name) && !/\.test\./.test(name)) found.push(path);
  }
  return found;
}

// Ids are written in the page as data-help="some.id", or as the values of a constant named HELP_...
function idsUsedInPages() {
  const ids = new Set();
  for (const file of sources()) {
    const text = readFileSync(file, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');          // comments are not pages
    for (const id of helpIdsIn(text)) ids.add(id);
    for (const map of text.matchAll(/const HELP_[A-Z_]+\b[^=]*=\s*\{([^}]*)\}/g)) for (const value of map[1].matchAll(/:\s*'([^']+)'/g)) ids.add(value[1]);
  }
  return ids;
}

test('every id the pages use has a tip, and every tip is used by a page', () => {
  const used = idsUsedInPages();
  assert.ok(used.size >= 40, `found only ${used.size} ids in the pages`);
  const missing = [...used].filter(id => !(id in HELP_TIPS));
  const unused = Object.keys(HELP_TIPS).filter(id => !used.has(id));
  assert.deepEqual(missing, [], `ids with no tip: ${missing.join(', ')}`);
  assert.deepEqual(unused, [], `tips no page uses: ${unused.join(', ')}`);
});

test('each tip is short, plain and complete', () => {
  for (const [id, tip] of Object.entries(HELP_TIPS)) {
    assert.ok(tip.title.trim() && tip.what.trim(), id);
    assert.ok(tip.title.length <= 40, `${id}: title is long`);
    assert.ok(tip.what.length <= 420, `${id}: the explanation is long`);
    for (const part of [tip.when, tip.wont]) if (part !== undefined) assert.ok(part.trim().length > 0 && part.length <= 260, id);
    assert.ok(/[.!?]$/.test(tip.what.trim()), `${id}: ends like a sentence`);
    assert.ok(!/\s{2,}|TODO|lorem/i.test(JSON.stringify(tip)), id);
  }
});

test('a press on something with no tip, or no id at all, gets the "no tip yet" card and never nothing', () => {
  assert.equal(tipFor('rail.new', HELP_TIPS).title, 'New chat');
  assert.equal(tipFor('nonsense.id', HELP_TIPS), NO_TIP);
  assert.equal(tipFor(null, HELP_TIPS), NO_TIP);
  assert.equal(tipFor(undefined, HELP_TIPS), NO_TIP);
  assert.equal(tipFor('toString', HELP_TIPS), NO_TIP);                    // an id that is a property of every object is not a tip
  assert.match(NO_TIP.what, /Nothing was pressed/);
});

test('Enter opens the tip of the focused thing; Space does too, except where it is a space in a text field', () => {
  assert.equal(keyOpensHelp('Enter', false), true);
  assert.equal(keyOpensHelp('Enter', true), true);                         // in the message box Enter would send: not in Help Mode
  assert.equal(keyOpensHelp(' ', false), true);
  assert.equal(keyOpensHelp(' ', true), false);
  for (const key of ['a', 'Tab', 'ArrowDown', 'Escape', 'Shift']) assert.equal(keyOpensHelp(key, false), false, key);
});

test('a text field is a field you type in; buttons, checkboxes and the like are not', () => {
  assert.equal(isTextField('TEXTAREA', false), true);
  assert.equal(isTextField('div', true), true);                            // contenteditable
  assert.equal(isTextField('SELECT', false), true);
  assert.equal(isTextField('INPUT', false, 'text'), true);
  assert.equal(isTextField('INPUT', false, ''), true);
  for (const type of ['button', 'submit', 'checkbox', 'radio', 'file', 'range']) assert.equal(isTextField('INPUT', false, type), false, type);
  assert.equal(isTextField('BUTTON', false), false);
});

test('the card sits below what was pressed, above it when there is no room, and always inside the window', () => {
  const viewport = { width: 1000, height: 700 };
  const below = placeCard({ left: 100, top: 100, width: 80, height: 30 }, 200, viewport);
  assert.deepEqual([below.placement, below.left, below.top], ['below', 100, 140]);
  const above = placeCard({ left: 100, top: 600, width: 80, height: 30 }, 200, viewport);
  assert.equal(above.placement, 'above');
  assert.equal(above.top, 600 - 10 - 200);
  const tall = placeCard({ left: 100, top: 300, width: 80, height: 30 }, 690, viewport);
  assert.deepEqual([tall.placement, tall.top], ['top', CARD_MARGIN]);
  assert.equal(placeCard({ left: 900, top: 100, width: 80, height: 30 }, 200, viewport).left, 1000 - 340 - CARD_MARGIN);      // not off the right edge
  assert.equal(placeCard({ left: -50, top: 100, width: 80, height: 30 }, 200, viewport).left, CARD_MARGIN);                     // nor the left
  const phone = placeCard({ left: 10, top: 100, width: 80, height: 30 }, 200, { width: 360, height: 640 });
  assert.equal(phone.left, CARD_MARGIN);
});

test('ids are found in source text, and only plain literals', () => {
  assert.deepEqual(helpIdsIn('<a data-help="rail.new" /><b data-help={x} /><c data-help="chat.trace">'), ['rail.new', 'chat.trace']);
  assert.deepEqual(helpIdsIn('nothing here'), []);
});

test('Help Mode catches every press while on, leaves only its own parts alone, and listens to nothing while off', () => {
  const mode = readFileSync(new URL('./HelpMode.tsx', import.meta.url), 'utf8');
  assert.match(mode, /if \(!active\) return undefined;/);                                          // no listeners at all while off
  assert.match(mode, /document\.addEventListener\(name, catchPress, true\)/);                      // capture phase: before the page's own handlers
  assert.match(mode, /document\.removeEventListener\(name, catchPress, true\)/);
  assert.match(mode, /event\.preventDefault\(\);\s*event\.stopPropagation\(\);/);
  for (const name of ["'click'", "'auxclick'", "'dblclick'", "'contextmenu'", "'mousedown'", "'pointerdown'", "'touchstart'"]) assert.ok(mode.includes(name), name);
  assert.match(mode, /if \(event\.key === 'Escape'\) \{[\s\S]*?if \(cardRef\.current\) setCard\(null\); else setActive\(false\);/);         // Escape closes the card, then the mode
  assert.match(mode, /node instanceof Element && node\.closest\(`\[\$\{HELP_UI_ATTR\}\]`\) !== null/);
  assert.match(mode, /document\.body\.classList\.toggle\('help-mode', active\)/);
  assert.match(mode, /role="dialog" aria-label=\{`Help: \$\{tip\.title\}`\}/);
});

test('the top-right button is the toggle, the guide is still under the user chip, and the whole shell is covered', () => {
  const top = readFileSync(new URL('./TopBar.tsx', import.meta.url), 'utf8');
  assert.match(top, /onClick=\{help\.toggle\}/);
  assert.match(top, /aria-pressed=\{help\.active\} data-help-ui/);                                  // the toggle itself is never caught
  assert.ok(!/onHelp/.test(top));
  const layout = readFileSync(new URL('./Layout.tsx', import.meta.url), 'utf8');
  assert.match(layout, /<HelpModeProvider onOpenGuide=\{\(\) => \{ if \(!showHelp\) toggleHelp\(\); \}\}>/);
  assert.match(layout, /<\/HelpModeProvider>/);
  assert.match(layout, /onHelp=\{toggleHelp\}/);                                                       // the user menu's Help still opens the guide
  const menu = readFileSync(new URL('./UserMenu.tsx', import.meta.url), 'utf8');
  assert.match(menu, /onClick=\{choose\(onHelp\)\}><Icon name="help" size=\{16\} \/>Help<\/button>/);
  const rail = readFileSync(new URL('./Rail.tsx', import.meta.url), 'utf8');
  assert.match(rail, /onHelp=\{\(\) => \{ onHelp\(\); if \(isMobile\) onCloseDrawer\(\); \}\}/);
});

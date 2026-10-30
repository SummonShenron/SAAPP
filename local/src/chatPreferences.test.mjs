// The choices that used to live in the chat box's "..." menu and now live on the Account page (src/chatPreferences.ts, components/ChatSettingsCard.tsx, pages/Account.tsx).
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

const store = new Map();
globalThis.localStorage = { getItem: key => (store.has(key) ? store.get(key) : null), setItem: (key, value) => store.set(key, String(value)) };
const events = [];
globalThis.window = { dispatchEvent: event => { events.push(event.type); return true; } };
const { SHOW_PATCHY_EVENT, loadShowPatchy, saveShowPatchy } = await import('./chatPreferences.ts');

const read = path => readFileSync(new URL(path, import.meta.url), 'utf8');

test('the robot is on unless the person turned him off, and saving tells an open chat page', () => {
  assert.equal(loadShowPatchy(), true);
  saveShowPatchy(false);
  assert.equal(store.get('showPatchy'), 'false');
  assert.equal(loadShowPatchy(), false);
  saveShowPatchy(true);
  assert.equal(loadShowPatchy(), true);
  assert.deepEqual(events, [SHOW_PATCHY_EVENT, SHOW_PATCHY_EVENT]);
});

test('storage that throws gives the default and never an error', () => {
  const saved = globalThis.localStorage;
  globalThis.localStorage = { getItem() { throw new Error('blocked'); }, setItem() { throw new Error('blocked'); } };
  assert.equal(loadShowPatchy(), true);
  assert.doesNotThrow(() => saveShowPatchy(false));
  globalThis.localStorage = saved;
});

test("the three entries are gone from the chat's menu and the trace entry is kept only for the embedded widget", () => {
  const chat = read('./pages/Chat.tsx');
  for (const gone of ['Message First', 'Incident Digest', 'SAAPP: ${showPatchy', 'handleOpsDigestChange', 'handleProactiveOpeningChange', 'togglePatchy', 'setOpsDigest']) {
    assert.ok(!chat.includes(gone), `${gone} should be gone from Chat.tsx`);
  }
  assert.match(chat, /filter\(e => isEmbedded \|\| \(e\.item\.label !== 'Conversations' && e\.item\.label !== 'Trace'\)\)/);
  assert.match(chat, /useState<boolean>\(\(\) => loadShowPatchy\(\)\)/);
  assert.match(chat, /addEventListener\(SHOW_PATCHY_EVENT, reread\)/);
  // what stays in the embedded widget's menu: Help, Export and Delete. The toggles that were above them (scope, open answers, deep thinking, plan first, code model) are the icons under the message box now
  // (components/ComposerChips.tsx) and the scope is in the session column, so they are no longer menu entries.
  for (const kept of ["label: 'Help'", "label: 'Export'", "label: 'Delete'"]) assert.ok(chat.includes(kept), kept);
  for (const moved of ['Open Answers:', 'Deep Thinking:', 'Plan first:', 'Code model:', 'Scope:']) assert.ok(!chat.includes(moved), `${moved} is a chip or the column's now`);
});

test('the Account page holds them: the robot for everyone with a mascot, the digest for admins only, and message first was already there', () => {
  const account = read('./pages/Account.tsx');
  assert.match(account, /<ChatSettingsCard mascotAllowed=\{principal !== 'guest_bty'\} \/>/);
  const card = read('./components/ChatSettingsCard.tsx');
  assert.match(card, /api\.getOpsDigest\(\)/);
  assert.match(card, /const showDigest = !!digest && digest\.available;/);
  assert.match(card, /if \(!mascotAllowed && !showDigest\) return null;/);
  assert.match(card, /saveShowPatchy\(next\)/);
  assert.match(read('./components/ProfileCard.tsx'), /Let Sonic start conversations/);
});

test('the help no longer sends people to a menu entry that is gone', () => {
  const help = read('./components/helpContent.tsx');
  assert.ok(!help.includes('Open the <strong>Trace</strong> in the "…" menu'));
  assert.ok(!help.includes('<strong>Message First:</strong>') && !help.includes('<strong>SAAPP:</strong>') && !help.includes('<strong>Trace:</strong>'));
  assert.match(help, /microscope/);
});

import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const src = fs.readFileSync(path.join(here, 'Rail.tsx'), 'utf8');

// The compact branch of the ternary starts at ') : (' and the expanded Recent list sits before it.
const compactStart = src.indexOf(') : (');
const popupAt = src.indexOf('className="rail-convo-popup"');

test('Conversations popup renders only in the compact branch', () => {
  assert.ok(compactStart > 0, 'compact branch marker found');
  assert.ok(popupAt > compactStart, 'popup is inside the compact branch');
  assert.ok(src.indexOf('rail-recent-list') < compactStart, 'expanded Recent list is outside the compact branch');
});

test('the popup hangs off the Chat button, which says it opens a menu only when the rail is collapsed, and the popup is labelled', () => {
  // The button is the Chat page's own rail item, so the attributes are conditional: only the collapsed rail's Chat item opens a popup.
  assert.match(src, /aria-haspopup=\{compact && p\.path === '\/chat' \? 'menu' : undefined\}/);
  assert.match(src, /aria-expanded=\{compact && p\.path === '\/chat' \? convoOpen : undefined\}/);
  assert.match(src, /ref=\{p\.path === '\/chat' \? convoBtnRef : undefined\}/);
  assert.match(src, /role="menu"/);
  assert.match(src, /aria-label="Recent conversations"/);
});

test('popup closes on an outside mousedown', () => {
  assert.match(src, /addEventListener\('mousedown', onDown\)/);
  assert.match(src, /convoBtnRef\.current\?\.contains\(target\)/);
  assert.match(src, /convoPopupRef\.current\?\.contains\(target\)/);
});

test('popup closes on Escape and returns focus to the button', () => {
  assert.match(src, /addEventListener\('keydown', onKey\)/);
  assert.match(src, /e\.key === 'Escape'/);
  assert.match(src, /convoBtnRef\.current\?\.focus\(\)/);
});

test('selecting a conversation calls onSelect, then closes the popup', () => {
  const selectAt = src.indexOf('conversations.onSelect(c.session_id);', compactStart);
  assert.ok(selectAt > compactStart, 'popup item calls conversations.onSelect');
  const closeAt = src.indexOf('setConvoOpen(false)', selectAt);
  assert.ok(closeAt > selectAt && closeAt - selectAt < 200, 'popup closes right after onSelect');
});

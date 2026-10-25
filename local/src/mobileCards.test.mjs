// On a phone the standalone chat shows all of its example cards, and the embedded widget keeps showing only the first (the rules are plain CSS, so this pins them).
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

const index = readFileSync(new URL('./index.css', import.meta.url), 'utf8');
const shell = readFileSync(new URL('./components/__styles__/AppShell.css', import.meta.url), 'utf8');
const chat = readFileSync(new URL('./pages/Chat.tsx', import.meta.url), 'utf8');

const phoneBlock = css => css.slice(css.indexOf('@media (max-width: 768px)'));

test('index.css still hides every card after the first on a phone, for the widget that has no .chat-page', () => {
  assert.match(index, /@media \(max-width: 768px\) \{[\s\S]*?\.example-cards-container \.query-card:not\(:first-child\) \{\s*display: none !important;/);
});

test('the standalone chat page shows all of them on a phone, and its rule outranks the one that hides them', () => {
  const block = phoneBlock(shell.slice(shell.indexOf('Phones')));
  assert.match(block, /\.chat-page \.example-cards-container \.query-card:not\(:first-child\) \{ display: flex !important; \}/);
  // three classes and a pseudo-class against two classes and a pseudo-class: higher specificity wins between two !important rules
  assert.ok(('.chat-page .example-cards-container .query-card:not(:first-child)'.match(/\./g) ?? []).length > ('.example-cards-container .query-card:not(:first-child)'.match(/\./g) ?? []).length);
});

test('only the standalone page carries the .chat-page class, so the widget is untouched', () => {
  assert.match(chat, /<div className=\{isEmbedded \? undefined : 'chat-page'\}>/);
});

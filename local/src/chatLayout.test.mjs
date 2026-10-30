// Run with: node --test src/chatLayout.test.mjs   (from local/)
// The chat page's layout rule (2026-10-31): opening the rail or showing the session column never moves the conversation. The rail takes only its collapsed width from the layout on the chat page and is
// drawn over the page's margin; the column is always drawn over the right margin; each side of the conversation gets padding only for the part of its margin the rail or column would cover.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

const shell = readFileSync(new URL('./components/__styles__/AppShell.css', import.meta.url), 'utf8');
const column = readFileSync(new URL('./components/__styles__/SessionColumn.css', import.meta.url), 'utf8');

test('the rail has one expanded and one collapsed width, and the difference is what the chat page draws over its margin', () => {
  assert.match(shell, /--rail-w: 252px;/);
  assert.match(shell, /--rail-compact-w: 64px;/);
  assert.match(shell, /--rail-extra: calc\(var\(--rail-w\) - var\(--rail-compact-w\)\);/);
  assert.match(shell, /\.app-rail \{[^}]*width: var\(--rail-w\);/);
  assert.match(shell, /\.app-rail-compact \{ width: var\(--rail-compact-w\);/);
});

test('on the chat page the expanded rail gives its extra width back to the layout, so the page does not move; other pages keep it in the layout', () => {
  assert.match(shell, /\.app-shell:has\(\.chat-page\) \.app-rail:not\(\.app-rail-compact\):not\(\.app-rail-drawer\) \{ margin-right: calc\(-1 \* var\(--rail-extra\)\); \}/);
  assert.match(shell, /~ \.app-main \.app-topbar \{ margin-left: var\(--rail-extra\); \}/);                // the title stays beside the rail
  assert.equal((shell.match(/margin-right: calc\(-1 \* var\(--rail-extra\)\)/g) ?? []).length, 1);          // the one place, and it is the chat page's rule above
});

test('each side of the conversation is padded only by the part of its margin that the rail or column would cover', () => {
  assert.match(shell, /--free: max\(0px, calc\(\(100cqw - 960px\) \/ 2\)\);/);
  assert.match(shell, /padding-left: calc\(var\(--base-pad\) \+ max\(0px, var\(--under-rail\) - var\(--free\)\)\);/);
  assert.match(shell, /padding-right: calc\(var\(--base-pad\) \+ max\(0px, var\(--under-column\) - var\(--free\)\)\);/);
  assert.match(shell, /--under-rail: var\(--rail-extra\);/);
  assert.match(shell, /\.chat-page:has\(\.session-column\) \.portal-body \{ --under-column: 272px; \}/);
  assert.match(shell, /@media \(max-width: 1099px\) \{ \.chat-page:has\(\.session-column\) \.portal-body \{ --under-column: 236px; \} \}/);   // the column is narrower there (SessionColumn.css)
  assert.match(shell, /@media \(max-width: 768px\) \{ \.chat-page \.portal-body \{ --base-pad: 0\.5rem; \} \}/);                          // phones keep their own gutters
});

test('the session column is always drawn over the page, never one of the row\'s items, and the chat page is the size container the conversation measures', () => {
  assert.match(column, /div\.chat-page\.chat-page \{ position: relative; container: chatpage \/ inline-size; \}/);
  assert.match(column, /\.chat-page > \.session-column \{ position: absolute; top: 0; right: 0; bottom: 0; \}/);
  assert.doesNotMatch(column, /@container chatpage/);                                                       // no width below which it goes back into the row and moves the conversation
  const widths = [...column.matchAll(/\.session-column \{[^}]*?width: (\d+)px/g)].map(match => Number(match[1]));
  assert.ok(widths.includes(272), 'the column is 272px wide, the figure the conversation pads for');
  assert.match(column, /@media \(max-width: 1099px\) \{ \.session-column \{ width: 236px;/);                  // and 236px on a narrower window
});

test('the message box draws no scrollbar: a one-line box overflowing by a fraction of a pixel showed a 6px thumb (orange on hover) beside the Send button', () => {
  assert.match(shell, /\.chat-page \.chat-input-wrapper \.chat-textarea \{ scrollbar-width: none; \}/);
  assert.match(shell, /\.chat-page \.chat-input-wrapper \.chat-textarea::-webkit-scrollbar \{ display: none; width: 0; height: 0; \}/);
  assert.doesNotMatch(shell, /\.chat-textarea[^{]*\{[^}]*overflow-y: hidden/);        // it still scrolls (index.css keeps overflow-y: auto); only the bar is hidden
  assert.doesNotMatch(shell, /\.chat-input-wrapper \{[^}]*overflow: hidden/);        // and the wrapper is never clipped: Patchy stands on its top edge
});

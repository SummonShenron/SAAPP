// Run with: node --test src/messageTime.test.mjs   (from local/; Node 22.18+ runs the TypeScript module directly)
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { formatMessageTime, fullMessageTime } from './messageTime.ts';

// Local-time constructors, so the tests do not depend on the machine's time zone.
const at = (y, mo, d, h, mi) => new Date(y, mo - 1, d, h, mi).toISOString();
const now = new Date(2026, 9, 28, 16, 0);

test('a message from today shows just the clock', () => {
  assert.equal(formatMessageTime(at(2026, 10, 28, 15, 42), now, 'en-US'), '3:42 PM');
  assert.equal(formatMessageTime(at(2026, 10, 28, 0, 5), now, 'en-US'), '12:05 AM');
});

test('an earlier day adds the date, and an earlier year adds the year', () => {
  assert.equal(formatMessageTime(at(2026, 10, 27, 9, 3), now, 'en-US'), 'Oct 27, 9:03 AM');
  assert.equal(formatMessageTime(at(2025, 12, 31, 23, 59), now, 'en-US'), 'Dec 31, 2025, 11:59 PM');
});

test('yesterday late at night is not mistaken for today', () => {
  assert.equal(formatMessageTime(at(2026, 10, 27, 23, 59), new Date(2026, 9, 28, 0, 1), 'en-US'), 'Oct 27, 11:59 PM');
});

test('no stamp or an unreadable one shows nothing instead of a guess', () => {
  for (const bad of [undefined, null, '', 'not a date', '2026-13-45']) assert.equal(formatMessageTime(bad, now, 'en-US'), '', String(bad));
  assert.equal(fullMessageTime(undefined, 'en-US'), '');
});

test('the tooltip has the full date', () => {
  assert.match(fullMessageTime(at(2026, 10, 28, 15, 42), 'en-US'), /Wednesday, October 28, 2026.*3:42/);
});

test('the page reads the time the server saved, stamps new messages, and shows hearts and time only on hover', () => {
  const chat = readFileSync(new URL('./pages/Chat.tsx', import.meta.url), 'utf8');
  const css = readFileSync(new URL('./components/__styles__/AppShell.css', import.meta.url), 'utf8');
  assert.match(chat, /sentAt: typeof m\.sent_at === 'string' \? m\.sent_at : undefined/);                      // history and the opening message
  assert.match(chat, /id: userMessageId, sender: 'user', text: textToSend, sentAt: sentNow/);                    // a message sent now
  assert.match(chat, /<span className="feedback-time"/);
  assert.match(css, /\.message-bubble \.feedback-actions\s*\{[^}]*opacity: 0/);                                   // hidden until the message is hovered
  assert.match(css, /\.message-bubble:hover \.feedback-actions/);
  assert.match(css, /\.message-bubble:focus-within \.feedback-actions/);                                         // and for someone using the keyboard
  assert.match(css, /@media \(hover: none\)[^}]*\{[^}]*\.feedback-actions\s*\{[^}]*opacity: 1/s);                // a touch screen has no hover, so the hearts stay reachable there
});

// Run with: node --test src/guestAllowance.test.mjs   (from local/; Node 22.18+ runs the TypeScript module directly)
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { guestAllowanceNote } from './guestAllowanceLogic.ts';
import { ChatSendError, describeChatError } from './chatErrors.ts';

const allowance = (over = {}) => ({ applies: true, used: 3, limit: 8, remaining: 5, nudge_at: 5, blocked: '', ...over });

test('nothing is said to anyone but the shared guest, before a first turn, or when no limit is set', () => {
  assert.equal(guestAllowanceNote(null), null);
  assert.equal(guestAllowanceNote(undefined), null);
  assert.equal(guestAllowanceNote({ applies: false }), null);
  assert.equal(guestAllowanceNote(allowance({ used: 0, remaining: 8 })), null);          // the welcome screen stays clean
  assert.equal(guestAllowanceNote(allowance({ limit: 0, remaining: null })), null);
});

test('before the nudge turn the line only says how many turns are left; from it on it says what an account adds', () => {
  assert.deepEqual(guestAllowanceNote(allowance()), { tone: 'info', text: '5 of 8 free demo turns left today.' });
  const nudge = guestAllowanceNote(allowance({ used: 5, remaining: 3 }));
  assert.equal(nudge.tone, 'nudge');
  assert.match(nudge.text, /3 of 8 free demo turns left today\. An account lifts the limit/);
});

test('the turn the nudge starts at comes from the server, so it changes with a setting there and not with this code', () => {
  assert.equal(guestAllowanceNote(allowance({ used: 2, remaining: 6, nudge_at: 2 })).tone, 'nudge');
  assert.equal(guestAllowanceNote(allowance({ used: 7, remaining: 1, nudge_at: 0 })).tone, 'info');       // 0: no nudge at all
});

test('a blocked visitor is told why in plain words: their own turns, or the whole demo for today', () => {
  assert.equal(guestAllowanceNote(allowance({ blocked: 'visitor', remaining: 0 })).tone, 'blocked');
  assert.match(guestAllowanceNote(allowance({ blocked: 'visitor', remaining: 0 })).text, /used today's free demo turns/);
  assert.match(guestAllowanceNote(allowance({ blocked: 'budget' })).text, /reached its limit for today/);
});

test('a message refused for the guest limit is not offered a retry and offers an account instead, in the server\'s own words', () => {
  const view = describeChatError(new ChatSendError('guest_limit', { status: 429, detail: "You've used today's free demo turns. Sign up for a free account to keep going." }));
  assert.equal(view.retryable, false);
  assert.equal(view.signUp, true);
  assert.match(view.message, /free demo turns/);
  assert.match(describeChatError(new ChatSendError('guest_limit', { status: 429 })).message, /free demo turns/);           // the fallback sentence
  assert.equal(describeChatError(new ChatSendError('rate_limited', { status: 429 })).signUp, undefined);                   // an ordinary 429 is unchanged
});

test('the page sends an anonymous visitor id only as the shared guest, turns a guest_limit 429 into that kind, and shows the line and the sign-up button', () => {
  const api = readFileSync(new URL('./api.ts', import.meta.url), 'utf8');
  assert.match(api, /if \(principal === 'guest'\) headers\["x-visitor-id"\] = getVisitorId\(\);/);
  assert.match(api, /\(body as \{ code\?: unknown \}\)\.code === 'guest_limit'/);
  assert.match(api, /guestLimit \? 'guest_limit' : kindForStatus\(response\.status\)/);
  assert.match(api, /\/\^\[A-Za-z0-9-\]\{16,64\}\$\/\.test\(saved\)/);                                          // the same shape the server accepts
  const chat = readFileSync(new URL('./pages/Chat.tsx', import.meta.url), 'utf8');
  assert.match(chat, /<GuestAllowance enabled=\{!isEmbedded && isPortfolioGuest\(principal, isEmbedded\)\} refreshKey=\{messages\.length\} \/>/);
  assert.match(chat, /msg\.failure\.signUp && \(/);
  const strip = readFileSync(new URL('./components/GuestAllowance.tsx', import.meta.url), 'utf8');
  assert.match(strip, /note\.tone !== 'info' && \(/);                                                           // the sign-up button comes with the nudge, not before
});

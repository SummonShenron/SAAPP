// Run with: node --test src/components/planPlacement.test.mjs   (from local/)
// Where the plan card goes: above the newest reply while an approved plan is carried out, so the work appears underneath it (Jack, 2026-10-31: every step snapped the page to the card at the bottom and hid the steps).
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { planAnchorId } from './planPlacement.ts';

const chat = [{ id: 'u1', sender: 'user' }, { id: 'a1', sender: 'ai' }, { id: 'u2', sender: 'user' }, { id: 'a2', sender: 'ai' }];

test('an approved plan is drawn above the newest reply, so its steps, edits and answer come underneath', () => {
  assert.equal(planAnchorId(chat, 'approved'), 'a2');
  assert.equal(planAnchorId(chat.slice(0, 2), 'approved'), 'a1');
});

test('a plan that is only proposed, or finished, stays after the reply, where it is what the reply points at', () => {
  assert.equal(planAnchorId(chat, 'proposed'), null);
  assert.equal(planAnchorId(chat, 'done'), null);
  assert.equal(planAnchorId(chat, undefined), null);
  assert.equal(planAnchorId(chat, null), null);
});

test('with no reply yet (the last message is the person\'s), the card goes at the end', () => {
  assert.equal(planAnchorId([{ id: 'u1', sender: 'user' }], 'approved'), null);
  assert.equal(planAnchorId([], 'approved'), null);
});

test('the list draws the card above the anchored reply, and at the end only when no anchored reply is on screen', () => {
  const page = readFileSync(new URL('../pages/Chat.tsx', import.meta.url), 'utf8');
  assert.match(page, /\{planCard && msg\.id === planAnchorId && planCard\}\s*\{bubble\}/);                          // above the reply's bubble
  assert.match(page, /\{planCard && !\(planAnchorId && messages\.some\(m => m\.id === planAnchorId\)\) && planCard\}\s*<div ref=\{messagesEndRef\} \/>/);   // never twice, never lost
  assert.match(page, /planAnchorId=\{codePlan && workspace\.canEdit \? planAnchorId\(messages, codePlan\.state\) : null\}/);
});

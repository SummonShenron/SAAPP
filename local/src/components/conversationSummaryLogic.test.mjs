// Run with: node --test src/components/conversationSummaryLogic.test.mjs   (from local/; Node 22.18+ runs the TypeScript module directly)
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { canCompact, compactMessage, itemCount, progressText, shouldShow } from './conversationSummaryLogic.ts';

const view = (over = {}) => ({ empty: true, unfolded: 0, fold_at: 14, keep: 6, sections: [], ...over });

test('nothing shows for a new conversation, and the control appears once a summary exists or there is something to fold', () => {
  assert.equal(shouldShow(null), false);
  assert.equal(shouldShow(undefined), false);
  assert.equal(shouldShow(view({ unfolded: 7 })), false, 'seven messages: all of them are kept word for word anyway');
  assert.equal(shouldShow(view({ unfolded: 8 })), true, 'two messages beyond the six kept: a fold has something to do');
  assert.equal(shouldShow(view({ empty: false, unfolded: 3 })), true, 'a summary exists, so it can be read and edited');
});

test('a fold needs at least two messages beyond the newest six', () => {
  assert.equal(canCompact(view({ unfolded: 7 })), false);
  assert.equal(canCompact(view({ unfolded: 8 })), true);
  assert.equal(canCompact(view({ unfolded: 40 })), true);
});

test('the progress line says how far the next fold is, and that it is coming once it is due', () => {
  assert.equal(progressText(view({ unfolded: 9 })), '9 of 14 messages kept word for word before the next fold.');
  assert.equal(progressText(view({ unfolded: 14 })), 'The next reply folds the older messages into this summary.');
  assert.equal(progressText(view({ unfolded: 20 })), 'The next reply folds the older messages into this summary.');
});

test('the badge counts every item across sections', () => {
  assert.equal(itemCount({ sections: [{ items: [1, 2] }, { items: [] }, { items: [3] }] }), 3);
  assert.equal(itemCount({ sections: [] }), 0);
});

test('the compact button says plainly what happened', () => {
  assert.match(compactMessage(true), /Folded/);
  assert.match(compactMessage(false), /Nothing to fold yet/);
});

test('the control is mounted in the chat header for signed-in people, reads again after a reply, and never loses what the person wrote', () => {
  const chat = readFileSync(new URL('../pages/Chat.tsx', import.meta.url), 'utf8');
  assert.match(chat, /import ConversationSummaryControl from '\.\.\/components\/ConversationSummaryControl'/);
  assert.match(chat, /!isGuestPrincipal\(principal\) && <ConversationSummaryControl sessionId=\{sessionId\} busy=\{loading\} turns=\{userTurnCount\} \/>/);
  const control = readFileSync(new URL('./ConversationSummaryControl.tsx', import.meta.url), 'utf8');
  assert.match(control, /api\.getConversationSummary\(sessionId\)/);
  assert.match(control, /api\.editConversationSummary\(sessionId, itemId, text, kind\)/);
  assert.match(control, /api\.compactConversationSummary\(sessionId\)/);
  assert.match(control, /api\.clearConversationSummary\(sessionId\)/);
  assert.match(control, /RECHECK_MS = \[6000, 16000\]/);
  assert.match(control, /item\.by === 'person'/);
  const api = readFileSync(new URL('../api.ts', import.meta.url), 'utf8');
  for (const name of ['getConversationSummary', 'editConversationSummary', 'clearConversationSummary', 'compactConversationSummary']) {
    assert.match(api, new RegExp(`export const ${name} =`));
    assert.match(api, new RegExp(`\\n  ${name},\\r?\\n`));
  }
  assert.match(api, /\/api\/conversation-summary\/compact\?session_id=/);
});

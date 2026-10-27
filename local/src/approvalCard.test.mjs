// Run with: node --test src/approvalCard.test.mjs   (from local/; Node 22.18+ runs the TypeScript module directly)
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { APPROVE_TEXT, REJECT_TEXT, isApprovalCard, showsApprovalButtons } from './approvalCard.ts';

const CARD = '**Approval Required**\n\nReady to create a Pull Request\n\n*Please Approve, Modify parameters, or Reject this action.*';
const COMMIT_CARD = '**Approval Required**\n\nA commit of 3 files.\n\n*Nothing is committed until you approve. Please Approve or Reject.*';

test('the server\'s approval cards are recognised, plain replies are not', () => {
  assert.equal(isApprovalCard(CARD), true);
  assert.equal(isApprovalCard(COMMIT_CARD), true);
  assert.equal(isApprovalCard('I need your approval before I do that.'), false);
  assert.equal(isApprovalCard('**Approval Required** is the heading the server uses.'), false);
  assert.equal(isApprovalCard(undefined), false);
  assert.equal(isApprovalCard(null), false);
});

test('buttons show on the newest card only, and not while a reply is on its way', () => {
  assert.equal(showsApprovalButtons(CARD, true, false), true);
  assert.equal(showsApprovalButtons(CARD, false, false), false);
  assert.equal(showsApprovalButtons(CARD, true, true), false);
  assert.equal(showsApprovalButtons('hello', true, false), false);
});

test('the words they send are ones the server reads as approving and rejecting', () => {
  // the same patterns as APPROVAL_PATTERN and REJECTION_PATTERN in backend/services/agent_workflow.py
  const source = readFileSync(new URL('../../backend/services/agent_workflow.py', import.meta.url), 'utf8');
  assert.match(source, /APPROVAL_PATTERN = re\.compile\(\s*r"[^"]*\bapprove\[d\]\?/);
  assert.match(source, /REJECTION_PATTERN = re\.compile\(\s*r"[^"]*reject\(\?:ed\)\?/);
  assert.match(APPROVE_TEXT.toLowerCase(), /\b(approve[d]?|confirm(?:ed)?|yes)\b/);
  assert.match(REJECT_TEXT.toLowerCase(), /\b(reject(?:ed)?|cancel|no)\b/);
  assert.doesNotMatch(REJECT_TEXT.toLowerCase(), /\b(approve[d]?|confirm(?:ed)?|yes|yeah|yep|yup|lgtm|sure|do it|go ahead|sounds good)\b/);
});

test('the chat shows the buttons under the latest approval card and answers by sending the word', () => {
  const chat = readFileSync(new URL('./pages/Chat.tsx', import.meta.url), 'utf8');
  assert.match(chat, /showsApprovalButtons\(msg\.text, messages\[messages\.length - 1\]\?\.id === msg\.id, loading\)/);
  assert.match(chat, /<ApprovalButtons onAnswer=\{text => handleSendMessageRef\.current\(text, attachmentsRef\.current\)\} \/>/);
});

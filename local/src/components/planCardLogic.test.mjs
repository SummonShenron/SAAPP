// Run with: node --test src/components/planCardLogic.test.mjs   (from local/)
// What the plan card says when Continue has stopped getting anywhere. The logic is a .ts file, so the tests read its source: the figures and sentences are the contract.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

const logic = readFileSync(new URL('./planCardLogic.ts', import.meta.url), 'utf8');
const card = readFileSync(new URL('./PlanCard.tsx', import.meta.url), 'utf8');
const api = readFileSync(new URL('../api.ts', import.meta.url), 'utf8');
const server = readFileSync(new URL('../../../backend/utils/code_plan.py', import.meta.url), 'utf8');

// The logic is three tiny pure functions; run them by stripping the types.
const js = logic.replace(/^import type .*$/m, '').replace(/export const STALL_NOTICE_AT/, 'const STALL_NOTICE_AT')
  .replace(/export function (\w+)\(plan: [^)]*\): [^{]+\{/g, 'function $1(plan) {') + '\nreturn { isStalled, stallNotice, markDoneStepId, STALL_NOTICE_AT };';
const { isStalled, stallNotice, markDoneStepId, STALL_NOTICE_AT } = new Function(js)();

const steps = [{ id: 's1', status: 'done' }, { id: 's2', status: 'todo' }, { id: 's3', status: 'todo' }];

test('the card changes its tune at the same figure the server counts to', () => {
  assert.equal(STALL_NOTICE_AT, 2);
  assert.match(server, /STALL_NOTICE_AT = 2/);
});

test('a plan is stuck only when it is approved and two or more turns in a row finished no step', () => {
  assert.equal(isStalled({ state: 'approved', stalled: 1 }), false);
  assert.equal(isStalled({ state: 'approved', stalled: 2 }), true);
  assert.equal(isStalled({ state: 'approved' }), false);                       // an older server sends no count
  assert.equal(isStalled({ state: 'proposed', stalled: 5 }), false);
  assert.equal(isStalled({ state: 'done', stalled: 5 }), false);
});

test('the notice says how many tries and what to do, and is absent while the plan is moving', () => {
  assert.equal(stallNotice({ state: 'approved', stalled: 1 }), null);
  const text = stallNotice({ state: 'approved', stalled: 3 });
  assert.match(text, /last 3 tries made no progress/);
  assert.match(text, /mark the step done/);
});

test('Mark done belongs to the first open step, and only while the plan is stuck', () => {
  assert.equal(markDoneStepId({ state: 'approved', stalled: 2, steps }), 's2');
  assert.equal(markDoneStepId({ state: 'approved', stalled: 1, steps }), null);
  assert.equal(markDoneStepId({ state: 'approved', stalled: 2, steps: [{ id: 's1', status: 'done' }] }), null);
});

test('the card shows the notice, demotes Continue while stuck, and calls the mark_done action', () => {
  assert.match(card, /stallNotice\(plan\)/);
  assert.match(card, /className=\{stuckNotice \? 'plan-link' : 'plan-approve'\}/);
  assert.match(card, /act\('mark_done', step\.id\)/);
  assert.match(card, /step\.id === markDoneId/);
  assert.match(api, /'remove_step' \| 'mark_done'/);
  assert.match(api, /stalled\?: number;/);
});

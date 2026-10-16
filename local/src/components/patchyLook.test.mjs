// Run with: node --test src/components/patchyLook.test.mjs   (from local/; Node 22.18+ runs the TypeScript module directly)
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { TURN_SENT_STATUS, budgetFromLevel, lookForNode, resolveMood } from './patchyLook.ts';

const calm = { laughing: false, nudged: false, faulted: false, celebrating: false, watching: false, drowsy: false, look: 'idle', budget: 'ok' };

test('a turn that has just been sent, and the planning steps, look like thinking', () => {
  for (const node of ['', null, undefined, TURN_SENT_STATUS, 'coordinator_node', 'reasoner_node', 'memory_node']) {
    assert.equal(lookForNode(node), 'thinking', String(node));
  }
});

test('the searching steps get the magnifying glass', () => {
  for (const node of ['retrieve_node', 'grade_documents_node', 'web_search_node', 'github_search', 'pr_summary']) {
    assert.equal(lookForNode(node), 'searching', node);
  }
});

test('the retry steps look puzzled', () => {
  assert.equal(lookForNode('rewrite_query_node'), 'puzzled');
  assert.equal(lookForNode('reward_evaluator'), 'puzzled');
});

test('the steps that write the answer, run code or draft a PR are typing, and so is any step we have never heard of', () => {
  for (const node of ['conversational_node', 'generate_node', 'formatter_node', 'summarizer_node', 'code_interpreter_node', 'draft_pr_node', 'execute_pr_node', 'some_new_node']) {
    assert.equal(lookForNode(node), 'writing', node);
  }
});

test('the budget level maps to what Patchy shows, and anything unexpected counts as fine', () => {
  assert.equal(budgetFromLevel('notice'), 'notice');
  assert.equal(budgetFromLevel('throttled'), 'throttled');
  assert.equal(budgetFromLevel('blocked'), 'blocked');
  for (const level of ['ok', 'off', '', null, undefined, 'weird']) assert.equal(budgetFromLevel(level), 'ok', String(level));
});

test('nothing going on is idle', () => {
  assert.equal(resolveMood(calm), 'idle');
});

test('a poke is answered over everything, including a failure and a running turn', () => {
  assert.equal(resolveMood({ ...calm, laughing: true, faulted: true, look: 'writing' }), 'laugh');
  assert.equal(resolveMood({ ...calm, nudged: true, faulted: true }), 'soft');
});

test('a failure outranks a running turn, a celebration and a pause', () => {
  assert.equal(resolveMood({ ...calm, faulted: true, look: 'writing', celebrating: true, budget: 'blocked' }), 'error');
});

test('a running turn shows its look and is not interrupted by watching the box or sleepiness', () => {
  assert.equal(resolveMood({ ...calm, look: 'searching', watching: true, drowsy: true }), 'active');
});

test('a finished turn celebrates, even over a blocked budget, but not over a failure', () => {
  assert.equal(resolveMood({ ...calm, celebrating: true, budget: 'blocked' }), 'done');
});

test('a blocked budget puts him to sleep, over watching and drowsiness', () => {
  assert.equal(resolveMood({ ...calm, budget: 'blocked', watching: true, drowsy: true }), 'asleep');
});

test('engaging with the box makes him watch, and a long quiet makes him drowsy, in that order', () => {
  assert.equal(resolveMood({ ...calm, watching: true, drowsy: true }), 'watching');
  assert.equal(resolveMood({ ...calm, drowsy: true }), 'drowsy');
});

test('a throttled or notice-level budget does not change the mood by itself', () => {
  assert.equal(resolveMood({ ...calm, budget: 'throttled' }), 'idle');
  assert.equal(resolveMood({ ...calm, budget: 'notice' }), 'idle');
});

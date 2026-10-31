// Run with: node --test src/sonicHealthRows.test.mjs   (from local/)
// The four coding rows added for the stall guard and the claim check (backend/utils/code_plan.py, backend/utils/claim_check.py): each reads its own keys, with the figures the backend sends
// (backend/utils/self_counters.py `summarize_counters`: totals, previous_totals, rates, previous_rates, change). The page itself is a large React file, so this reads its source.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

const page = readFileSync(new URL('./pages/SonicHealth.tsx', import.meta.url), 'utf8');
const row = (label) => page.split('\n').find(line => line.includes('<tr><td>') && line.includes(label)) ?? '';

test('plan turns that finished no step, and replies that claimed a passing check no run backed, show this period, last period and the change, with their denominators', () => {
  for (const [label, key, denominator] of [
    ['Plan turns that finished no step', 'code_plan_stalled', 'code_plan_exec_turns'],
    ['Replies that claimed a passing check no run backed', 'claim_unproven', 'code_turns'],
    ['Answers sent back once to run the check', 'claim_sent_back', 'code_turns'],
  ]) {
    const line = row(label);
    assert.ok(line, `${label} has a row`);
    assert.ok(line.includes(`pct(rates.${key})`) && line.includes(`pct(previous.${key})`) && line.includes(`change(changes.${key})`), `${key}: this period, last period and change`);
    assert.ok(line.includes(`totals.${key} ?? 0`) && line.includes(`totals.${denominator} ?? 0`), `${key}: says how many of how many`);
  }
});

test('steps the person marked done by hand is a plain count for this period and the one before', () => {
  const line = row('Plan steps the person marked done by hand');
  assert.ok(line.includes('totals.code_plan_marked_done ?? 0') && line.includes('previousTotals.code_plan_marked_done ?? 0'));
});

test('the rows sit with the other plan rows, after "Plans the person approved"', () => {
  assert.ok(page.indexOf('Plans the person approved') < page.indexOf('Plan turns that finished no step'));
  assert.ok(page.indexOf('Answers sent back once to run the check') < page.indexOf('Turns carrying out a plan that ran out of steps'));
});

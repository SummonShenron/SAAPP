// Run with: node --test src/components/budgetText.test.mjs   (from local/; Node 22.18+ runs the TypeScript module directly)
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { budgetChipText, ownUsageText } from './budgetText.ts';

test('a person is told how far along their own limit is, in their own words and with no amount', () => {
  assert.equal(ownUsageText({ level: 'ok', own_fraction: 0.54, own_scope: 'my_day' }), 'Your usage: 54% of your day');
  assert.equal(ownUsageText({ level: 'ok', own_fraction: 0.8, own_scope: 'my_five' }), 'Your usage: 80% of your 5 hours');
  assert.equal(ownUsageText({ level: 'ok', own_fraction: 1.2, own_scope: 'my_day' }), 'Your usage: 120% of your day');
});

test('a missing scope still reads as the day, and a missing number reads as nothing used', () => {
  assert.equal(ownUsageText({ level: 'ok', own_fraction: 0.1 }), 'Your usage: 10% of your day');
  assert.equal(ownUsageText({ level: 'ok' }), 'Your usage: 0% of your day');
});

test('an admin still gets the budget that is furthest along', () => {
  assert.equal(budgetChipText({ level: 'ok', fraction: 0.54, scope: 'month' }), 'Budget 54% of month');
  assert.equal(budgetChipText({ level: 'throttled', fraction: 1.05, scope: 'my_day', extended_by: 0.25 }), 'Budget 105% of your day (+25%)');
});

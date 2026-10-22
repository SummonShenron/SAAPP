// Run with: node --test src/components/budgetText.test.mjs   (from local/; Node 22.18+ runs the TypeScript module directly)
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { budgetChipText, ownUsageText, ringView } from './budgetText.ts';

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

test('an admin with a limit of their own sees the figure furthest along on the ring (the month at 60% beats their day at 20%), and both in the words', () => {
  const view = ringView({ level: 'ok', fraction: 0.6, scope: 'month', own_fraction: 0.2, own_scope: 'my_day', own_level: 'ok' });
  assert.equal(view.fraction, 0.6);
  assert.equal(view.level, 'ok');
  assert.equal(view.text, 'Budget 60% of month · Your usage: 20% of your day');
});

test('the ring follows the month at 61% even when their own day is almost unused and the shared budget is below its notice line', () => {
  const view = ringView({ level: 'ok', fraction: 0.6139, scope: 'month', own_fraction: 0.0092, own_scope: 'my_day', own_level: 'ok' });
  assert.equal(view.fraction, 0.6139);
});

test('when the shared budget is at notice the ring still follows it, and the words still give both', () => {
  const view = ringView({ level: 'notice', fraction: 0.85, scope: 'day', own_fraction: 0.3, own_scope: 'my_day', own_level: 'ok' });
  assert.equal(view.fraction, 0.85);
  assert.equal(view.level, 'notice');
  assert.equal(view.text, 'Budget 85% of day · Your usage: 30% of your day');
});

test('when the figure furthest along is the admin\'s own limit there is one figure, not the same one twice', () => {
  const view = ringView({ level: 'throttled', fraction: 1.05, scope: 'my_day', own_fraction: 1.05, own_scope: 'my_day', own_level: 'throttled' });
  assert.equal(view.fraction, 1.05);
  assert.equal(view.level, 'throttled');
  assert.equal(view.text, 'Budget 105% of your day');
});

test('everyone else sees only their own limit, and nobody sees a ring with nothing to show', () => {
  const view = ringView({ level: 'ok', own_fraction: 0.54, own_scope: 'my_day', own_level: 'notice' });
  assert.deepEqual(view, { fraction: 0.54, level: 'notice', text: 'Your usage: 54% of your day' });
  assert.equal(ringView({ level: 'ok' }), null);
});

test('an admin with no limit of their own sees the shared budget as before', () => {
  const view = ringView({ level: 'ok', fraction: 0.54, scope: 'month' });
  assert.deepEqual(view, { fraction: 0.54, level: 'ok', text: 'Budget 54% of month' });
});

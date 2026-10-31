// Run with: node --test src/components/counterSeries.test.mjs   (from local/)
// The data behind the Sonic Health coding-loop charts (2026-10-31): every day of the period is drawn, shares add up, a day with nothing is a gap and never a false zero.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { OPENING_GROUPS, PEAK_GROUPS, RANK_GROUPS, averageLine, hasLine, hasStack, niceMax, periodDays, percent, ratioLine, stackPoints, stackTotals } from './counterSeries.ts';

const today = new Date(Date.UTC(2026, 9, 31, 15, 0));
const days = periodDays(7, today);

test('the period is every calendar day, oldest first, ending today, in UTC', () => {
  assert.deepEqual(days, ['2026-10-25', '2026-10-26', '2026-10-27', '2026-10-28', '2026-10-29', '2026-10-30', '2026-10-31']);
  assert.equal(periodDays(1, today).length, 1);
  assert.equal(periodDays(0, today).length, 1);
  assert.deepEqual(periodDays(3, new Date(Date.UTC(2026, 10, 1))), ['2026-10-30', '2026-10-31', '2026-11-01']);
});

test('a stacked day adds its groups up and the shares sum to one; a day with nothing has no shares', () => {
  const daily = [{ day: '2026-10-29', counts: { loop_open_read_window: 2, loop_open_edit: 1, loop_open_search: 3, loop_open_tree: 1, loop_open_read_top: 1, loop_open_other: 9 } }];
  const points = stackPoints(daily, days, OPENING_GROUPS);
  assert.equal(points.length, 7);
  const day = points.find(point => point.day === '2026-10-29');
  assert.deepEqual(day.values, [3, 4, 1]);
  assert.equal(day.total, 8);                                    // "other" is not one of the groups
  assert.ok(Math.abs(day.shares.reduce((a, b) => a + b, 0) - 1) < 1e-9);
  const empty = points.find(point => point.day === '2026-10-26');
  assert.equal(empty.total, 0);
  assert.deepEqual(empty.shares, [0, 0, 0]);
  assert.equal(hasStack(points), true);
  assert.equal(hasStack(stackPoints([], days, OPENING_GROUPS)), false);
  assert.equal(hasStack(stackPoints(undefined, days, PEAK_GROUPS)), false);
});

test('a bar over a whole period uses the same groups, and negative or missing counts are ignored', () => {
  const bar = stackTotals({ orient_rank_first: 6, orient_rank_second: 2, orient_rank_absent: 2, orient_rank_third: -4 }, RANK_GROUPS);
  assert.deepEqual(bar.values, [6, 2, 0, 0, 2]);
  assert.equal(percent(bar.shares[0]), '60%');
  assert.equal(stackTotals(undefined, RANK_GROUPS).total, 0);
});

test('an average line is a sum over a count and a day with no turns is a gap, not zero', () => {
  const daily = [{ day: '2026-10-28', counts: { code_first_edit_steps: 9, code_first_edit_turns: 3 } }, { day: '2026-10-30', counts: { code_first_edit_steps: 5, code_first_edit_turns: 1 } }];
  const line = averageLine(daily, days, 'code_first_edit_steps', 'code_first_edit_turns');
  assert.equal(line.find(p => p.day === '2026-10-28').value, 3);
  assert.equal(line.find(p => p.day === '2026-10-28').n, 3);
  assert.equal(line.find(p => p.day === '2026-10-29').value, null);
  assert.equal(line.find(p => p.day === '2026-10-30').value, 5);
  assert.equal(hasLine(line), true);
  assert.equal(hasLine(averageLine([], days, 'a', 'b')), false);
});

test('a ratio line is a share of the turns of the day and never goes over 100%', () => {
  const daily = [{ day: '2026-10-29', counts: { code_cap_hit: 1, code_turns: 4 } }, { day: '2026-10-30', counts: { code_cap_hit: 5, code_turns: 2 } }];
  const line = ratioLine(daily, days, 'code_cap_hit', 'code_turns');
  assert.equal(line.find(p => p.day === '2026-10-29').value, 0.25);
  assert.equal(line.find(p => p.day === '2026-10-30').value, 1);
  assert.equal(line.find(p => p.day === '2026-10-31').value, null);
});

test('the axis ends on a round number', () => {
  assert.equal(niceMax(0), 1);
  assert.equal(niceMax(3.2), 5);
  assert.equal(niceMax(7), 10);
  assert.equal(niceMax(12), 20);
  assert.equal(niceMax(2.4), 2.5);
});

test('every group names its counters with letters only and the same keys the server keeps', () => {
  for (const group of [...OPENING_GROUPS, ...PEAK_GROUPS, ...RANK_GROUPS]) {
    assert.ok(group.keys.length > 0 && group.label && group.color.startsWith('var(--cc-'), group.id);
    for (const key of group.keys) assert.match(key, /^(loop_open_|code_ctx_peak_|orient_rank_)[a-z_]+$/, key);
  }
});

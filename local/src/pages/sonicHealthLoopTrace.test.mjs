// Run with: node --test src/pages/sonicHealthLoopTrace.test.mjs   (from local/)
// The Sonic Health rows for where a coding turn goes wrong (2026-10-31): the arithmetic behind each cell, and that the page renders them.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { loopTraceRows } from './sonicHealthLoopTrace.ts';

const cell = (rows, id, which = 'current') => rows.find(row => row.id === id)[which];

test('with no counters every row is a dash, never NaN or a divide by zero', () => {
  const rows = loopTraceRows({}, {});
  assert.equal(rows.length, 9);
  for (const row of rows) {
    assert.equal(row.current, '-', row.id);
    assert.equal(row.previous, '-', row.id);
  }
});

test('the opening move shares add up from the fixed list of moves, listed and found counted together', () => {
  const rows = loopTraceRows({ loop_open_read_top: 2, loop_open_read_window: 2, loop_open_search: 4, loop_open_tree: 1, loop_open_find: 1 }, {});
  assert.equal(cell(rows, 'open'), 'from the top 20% / at a window 20% / searched 40% / listed or found 20% / edited 0%');
});

test('the block rows use only the turns that were given the block', () => {
  const rows = loopTraceRows({
    loop_open_search: 10, loop_open_block_read_window: 3, loop_open_block_search: 1,
    orient_turns: 4, orient_read_turns: 4, orient_first_read_in_block: 3,
    orient_rank_first: 2, orient_rank_second: 1, orient_rank_absent: 1,
  }, {});
  assert.equal(cell(rows, 'open-block'), 'from the top 0% / at a window 75% / searched 25% / listed or found 0% / edited 0%');
  assert.equal(cell(rows, 'block-use'), '4 turns; first read was a listed file in 75%');
  assert.equal(cell(rows, 'block-rank'), '1st 50% / 2nd 25% / 3rd 0% / 4th 0% / not listed 25%');
});

test('the first edit step is an average and a share that acted by step 4, with the block slice beside it', () => {
  const rows = loopTraceRows({ code_first_edit_turns: 4, code_first_edit_steps: 14, code_first_edit_at_very_early: 1, code_first_edit_at_early: 1, code_first_edit_at_very_late: 2, orient_first_edit_turns: 2, orient_first_edit_steps: 5 }, {});
  assert.equal(cell(rows, 'first-edit'), 'step 3.5 on average, by step 4 in 50%');
  assert.equal(cell(rows, 'first-edit-block'), 'step 2.5 on average');
});

test('top reads, prompt size and the cache read the way the counters say', () => {
  const rows = loopTraceRows({ reading_top_reads: 1, reading_window_reads: 3, code_ctx_turns: 20, code_ctx_peak_huge: 5, code_ctx_calls_huge: 10, code_ctx_input: 1000, code_ctx_cached: 650 }, {});
  assert.equal(cell(rows, 'top-reads'), '25%');
  assert.equal(cell(rows, 'peak'), 'over 100K in 25% of turns, 0.50 such calls per turn');
  assert.equal(cell(rows, 'cache'), '65%');
});

test('the previous period is computed from its own totals', () => {
  const rows = loopTraceRows({ reading_top_reads: 1, reading_window_reads: 1 }, { reading_top_reads: 3, reading_window_reads: 1 });
  assert.equal(cell(rows, 'top-reads'), '50%');
  assert.equal(cell(rows, 'top-reads', 'previous'), '75%');
});

test('Sonic Health renders the rows from the current and the previous totals', () => {
  const page = readFileSync(new URL('./SonicHealth.tsx', import.meta.url), 'utf8');
  assert.match(page, /import \{ loopTraceRows \} from '\.\/sonicHealthLoopTrace';/);
  assert.match(page, /loopTraceRows\(totals, previousTotals\)\.map\(row => \(/);
});

import assert from 'node:assert/strict';
import { test } from 'node:test';
import { logoFlowOffset, LOGO_DASH_PERIOD } from './logoMarkData.ts';

test('the top angled line (1 to 2) moves left to right', () => {
  // The edge from 1 to 2 is dashed.
  // We forced it to return -LOGO_DASH_PERIOD in logoFlowOffset.
  const offset = logoFlowOffset([1, 2, true]);
  assert.equal(offset, -LOGO_DASH_PERIOD, 'Top angled line should move left to right (-LOGO_DASH_PERIOD)');
});

test('other dashed lines maintain their original behavior', () => {
  // Edge 4 to 1 is dashed.
  // LOGO_NODES[1][0] (50) < LOGO_NODES[4][0] (126), so it is drawn right to left.
  // Original logic: LOGO_NODES[to][0] < LOGO_NODES[from][0] ? -LOGO_DASH_PERIOD : LOGO_DASH_PERIOD
  // 50 < 126 is true, so it returns -LOGO_DASH_PERIOD.
  const offset = logoFlowOffset([4, 1, true]);
  assert.equal(offset, -LOGO_DASH_PERIOD, 'Other dashed line should maintain original behavior');
});

// Run with: node --test src/pages/sonicHealthRates.wiring.test.mjs   (from local/)
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

const page = readFileSync(new URL('./SonicHealth.tsx', import.meta.url), 'utf8');

test('the behaviour table has rows for the turn-end, last-step and choice-card rates', () => {
  for (const key of ['turn_ran_out_progress', 'turn_ran_out_lost', 'last_step_progressing', 'last_step_stalled', 'choice_asked', 'choice_picked']) {
    assert.ok(page.includes(`rates.${key})}`), key);
  }
});

test('the going-in-circles row is labelled that way, not as context lost', () => {
  assert.ok(page.includes('Turns that ran out going in circles'));
  assert.equal(page.includes('context lost'), false);
});

// Run with: node --test src/components/runnerNudge.test.mjs   (from local/; Node 22.18+ runs the TypeScript module directly)
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { RUNNER_NUDGE_DAYS, RUNNER_NUDGE_KEY, RUNNER_NUDGE_TEXT, RUNNER_NUDGE_TITLE, nudgeDismissedUntil, parseNudgeDismissed, runnerNudgeVisible } from './sessionColumnLogic.ts';

const DAY = 24 * 60 * 60 * 1000;
const NOW = Date.parse('2026-11-05T12:00:00Z');
const runner = (over = {}) => ({ id: 'r1', name: 'my pc', connected: true, last_seen: '2026-11-05T11:59:50Z', projects: [], ...over });

test('the nudge shows only where the person can have a runner, none is set up, and they have not said "Not now" this week', () => {
  assert.equal(runnerNudgeVisible(true, [], null, NOW), true);
  assert.equal(runnerNudgeVisible(false, [], null, NOW), false);                       // a guest has no runner to set up
  assert.equal(runnerNudgeVisible(true, null, null, NOW), false);                      // the page has not asked yet: no flash of a nudge for someone who has one
  assert.equal(runnerNudgeVisible(true, [runner()], null, NOW), false);               // connected
  assert.equal(runnerNudgeVisible(true, [runner({ connected: false })], null, NOW), false);        // set up but not answering: that has its own Start button, and the Account page is not the fix
});

test('"Not now" keeps it away for a week and then it comes back', () => {
  const until = nudgeDismissedUntil(NOW);
  assert.equal(until, NOW + RUNNER_NUDGE_DAYS * DAY);
  assert.equal(RUNNER_NUDGE_DAYS, 7);
  assert.equal(runnerNudgeVisible(true, [], until, NOW + DAY), false);
  assert.equal(runnerNudgeVisible(true, [], until, until - 1), false);
  assert.equal(runnerNudgeVisible(true, [], until, until), true);
  assert.equal(runnerNudgeVisible(true, [], until, until + DAY), true);
});

test('what was stored is read back as a time and anything else as nothing', () => {
  assert.equal(parseNudgeDismissed(String(NOW)), NOW);
  for (const bad of [null, '', 'soon', '-5', '0', 'NaN', 'Infinity']) assert.equal(parseNudgeDismissed(bad), null, String(bad));
});

test('the words say what the runner is, that it asks first, and where the steps are, and make no promise', () => {
  assert.equal(RUNNER_NUDGE_TITLE, 'Set up the runner');
  assert.match(RUNNER_NUDGE_TEXT, /run your tests and checks on your own computer, after you approve each one/);
  assert.match(RUNNER_NUDGE_TEXT, /Account settings/);
});

test('the page, the column and the phone strip are wired: the nudge opens the Account settings and the dismissal is remembered', () => {
  const chat = readFileSync(new URL('../pages/Chat.tsx', import.meta.url), 'utf8');
  assert.match(chat, /visible: runnerNudgeVisible\(!isGuestPrincipal\(principal\), runnerList, nudgeDismissed, Date\.now\(\)\)/);
  assert.match(chat, /onOpen: \(\) => routeNavigate\('\/account'\)/);
  assert.match(chat, /localStorage\.setItem\(RUNNER_NUDGE_KEY, String\(until\)\)/);
  assert.match(chat, /const runnerSetup = isGuestPrincipal\(principal\) \? null :/);
  assert.match(chat, /runnerSetup=\{runnerSetup\}/);                                       // the column
  assert.match(chat, /folderControls,\s*runnerSetup,\s*onRunnerAsked: checkRunnerSoon,\s*\};/);      // the phone strip's connection props (and the quick re-check after "Let the runner run checks here")
  const column = readFileSync(new URL('./SessionColumn.tsx', import.meta.url), 'utf8');
  assert.match(column, /\{runnerSetup\?\.visible && <RunnerNudge setup=\{runnerSetup\} \/>\}/);
  assert.match(column, /runners !== null && runners\.length === 0 && runnerSetup/);       // the Connection panel's own button, always there while no runner is set up
  assert.match(column, /Set up the runner<\/button>/);
  const rail = readFileSync(new URL('./SessionRail.tsx', import.meta.url), 'utf8');
  assert.match(rail, /open !== 'connection' && connection\.runnerSetup\?\.visible/);
  assert.match(rail, /if \(connection\.runnerSetup\?\.visible\) return 'warn';/);
  assert.equal(RUNNER_NUDGE_KEY, 'runner_nudge_dismissed_until');
});

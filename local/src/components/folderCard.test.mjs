// Run with: node --test src/components/folderCard.test.mjs   (from local/; Node 22.18+ runs the TypeScript module directly)
// The one folder card (2026-11-12): an open folder is one thing with two abilities, Files (the browser's upload) and Run (the runner), instead of two unrelated rows.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { folderCard, folderLine, runnerLine } from './sessionColumnLogic.ts';

const NOW = new Date('2026-11-12T12:00:00Z');
const runner = (over = {}) => ({ id: 'r1', name: 'my pc', connected: true, last_seen: '2026-11-12T11:59:50Z', projects: [], requests: [], ...over });
const project = (name) => ({ id: `p-${name}`, name });

test('no folder open means no card, so the runner row and "No folder connected" stand as they were', () => {
  assert.equal(folderCard([runner()], 'disconnected', null, null, NOW), null);
  assert.equal(folderCard([runner()], 'connected', '  ', 5, NOW), null);
  assert.equal(folderCard([runner()], 'something-new', 'app', 5, NOW), null);
  assert.equal(runnerLine([runner()], NOW).text, 'Runner connected');            // the old functions are untouched: the phone strip's dots still use them
  assert.equal(folderLine('disconnected', null, null).text, 'No folder connected');
});

test('the Files line says what the folder sync is doing, with the folder name and size', () => {
  const files = (status, count = 12) => folderCard([runner()], status, 'store-api', count, NOW).files;
  assert.deepEqual(files('connected'), { tone: 'ok', text: 'Files · in sync', detail: 'store-api · 12 files' });
  assert.equal(files('syncing').text, 'Files · syncing');
  assert.equal(files('syncing').tone, 'warn');
  assert.equal(files('not-uploaded').text, 'Files · not on the server yet');
  assert.match(files('not-uploaded').detail, /uploads when you send a message/);
  assert.equal(files('needs-permission').text, 'Files · reconnect the folder');
  assert.equal(files('error').text, 'Files · sync failed');
});

test('the Run line follows the runner: checking, none set up, not answering, not added yet, ready', () => {
  const run = (runners) => folderCard(runners, 'connected', 'store-api', 12, NOW);
  assert.deepEqual([run(null).runKind, run(null).run.text], ['checking', 'Run · checking the runner']);
  assert.deepEqual([run([]).runKind, run([]).run.tone, run([]).run.text], ['none', 'off', 'Run · no runner set up']);
  const asleep = run([runner({ connected: false, last_seen: '2026-11-12T11:50:00Z' })]);
  assert.deepEqual([asleep.runKind, asleep.run.tone, asleep.run.text, asleep.run.detail], ['not_answering', 'warn', 'Run · runner not answering', 'Last seen 10 min ago']);
  const unknown = run([runner({ projects: [project('another'), project('third')] })]);
  assert.deepEqual([unknown.runKind, unknown.run.tone, unknown.run.text], ['needs_project', 'warn', 'Run · not added yet']);
  assert.equal(unknown.run.detail, 'my pc does not know "store-api"');
  const ready = run([runner({ projects: [project('Store-API')] })]);
  assert.deepEqual([ready.runKind, ready.run.tone, ready.run.text, ready.run.detail], ['ready', 'ok', 'Run · ready', 'Store-API · 10s ago']);       // the name matches case aside, as the server does
});

test('any connected runner with the project makes it ready; one runner with one OTHER project is not ready for this folder and says where checks would go', () => {
  const card = folderCard([runner({ id: 'r1', projects: [] }), runner({ id: 'r2', name: 'desktop', projects: [project('store-api')] })], 'connected', 'store-api', 1, NOW);
  assert.equal(card.runKind, 'ready');
  const fallback = folderCard([runner({ projects: [project('different-name')] })], 'connected', 'store-api', 1, NOW);       // runner_jobs.connected_runner sends a check to the only project, whatever its name
  assert.deepEqual([fallback.runKind, fallback.run.tone, fallback.run.text, fallback.run.detail], ['needs_project', 'warn', 'Run · would use "different-name"', 'my pc does not know "store-api"']);
  const two = folderCard([runner({ projects: [project('a'), project('b')] })], 'connected', 'store-api', 1, NOW);          // no fallback with two projects
  assert.equal(two.runKind, 'needs_project');
});

test('the Run line agrees with the button under it: the offer exists exactly when the card says this folder is not added', async () => {
  const { runnerProjectOffer } = await import('./sessionColumnLogic.ts');
  for (const runners of [null, [], [runner({ connected: false })], [runner()], [runner({ projects: [project('store-api')] })], [runner({ projects: [project('other')] })], [runner({ projects: [project('a'), project('b')] })]]) {
    const card = folderCard(runners, 'connected', 'store-api', 1, NOW);
    const offer = runnerProjectOffer(runners, 'connected', 'store-api');
    assert.equal(Boolean(offer), card.runKind === 'needs_project', `the button and the Run line agree for ${JSON.stringify(runners)}`);
  }
});

test('the panel draws the card while a folder is open (hidden Run line for a guest) and the old rows when none is', () => {
  const column = readFileSync(new URL('./SessionColumn.tsx', import.meta.url), 'utf8');
  assert.match(column, /const card = folderCard\(runners, folder\.status, folder\.name, folder\.fileCount, now\);/);
  assert.match(column, /\{card \? <Row line=\{card\.files\} \/> : null\}/);
  assert.match(column, /\{card \? \(runnerEnabled \? <Row line=\{card\.run\} \/> : null\) : \(runner && <Row line=\{runner\} \/>\)\}/);
  assert.match(column, /\{!card && <Row line=\{folderRow\} \/>\}/);
  assert.match(column, /\{projectOffer && <RunnerProjectAsk offer=\{projectOffer\} onAsked=\{onRunnerAsked\} \/>\}/);
});

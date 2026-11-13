// Run with: node --test src/components/runnerProjectOffer.test.mjs   (from local/; Node 22.18+ runs the TypeScript module directly)
// The "Let the runner run checks here" button in the Connection card: one pick for the folder instead of two (2026-11-12).
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { runnerProjectHint, runnerProjectOffer } from './sessionColumnLogic.ts';

const runner = (over = {}) => ({ id: 'r1', name: 'my pc', connected: true, last_seen: '2026-11-12T12:00:00Z', projects: [], requests: [], ...over });

test('a connected runner that does not know the open folder is offered it, by the folder name alone', () => {
  const offer = runnerProjectOffer([runner()], 'connected', 'store-api');
  assert.deepEqual(offer, { kind: 'offer', runnerId: 'r1', runnerName: 'my pc', folderName: 'store-api' });
  assert.ok(runnerProjectOffer([runner()], 'not-uploaded', 'store-api'));           // any state in which a folder is open
  assert.ok(runnerProjectOffer([runner()], 'needs-permission', 'store-api'));
});

test('nothing is offered without a folder, without a runner answering, or when the runner already has the folder', () => {
  assert.equal(runnerProjectOffer([runner()], 'disconnected', 'store-api'), null);      // no folder open
  assert.equal(runnerProjectOffer([runner()], 'connected', null), null);
  assert.equal(runnerProjectOffer([runner()], 'connected', '   '), null);
  assert.equal(runnerProjectOffer(null, 'connected', 'store-api'), null);              // the page has not asked yet
  assert.equal(runnerProjectOffer([], 'connected', 'store-api'), null);                // no runner set up: the nudge handles that
  assert.equal(runnerProjectOffer([runner({ connected: false })], 'connected', 'store-api'), null);   // not answering: the Start button handles that
  assert.equal(runnerProjectOffer([runner({ projects: [{ id: 'p1', name: 'Store-API' }] })], 'connected', 'store-api'), null);   // case aside, as the server matches it
  assert.equal(runnerProjectOffer([runner({ connected: false, projects: [] }), runner({ id: 'r2', projects: [{ id: 'p2', name: 'store-api' }] })], 'connected', 'store-api'), null);
});

test('with several runners the first connected one is asked, and any of them having the folder is enough', () => {
  const offer = runnerProjectOffer([runner({ id: 'r0', connected: false }), runner({ id: 'r1', name: 'laptop' }), runner({ id: 'r2', name: 'desktop' })], 'connected', 'app');
  assert.equal(offer.runnerId, 'r1');
  assert.equal(runnerProjectOffer([runner({ id: 'r1' }), runner({ id: 'r2', projects: [{ id: 'p', name: 'app' }] })], 'connected', 'app'), null);
});

test('an ask that is open shows as waiting; one that ended shows how, so the person can try again', () => {
  const ask = (state, name = 'store-api') => runnerProjectOffer([runner({ requests: [{ id: 'q', name, state }] })], 'connected', 'store-api')?.kind;
  assert.equal(ask('waiting'), 'waiting');
  assert.equal(ask('asked'), 'waiting');
  assert.equal(ask('declined'), 'declined');
  assert.equal(ask('no_picker'), 'no_picker');
  assert.equal(ask('error'), 'error');
  assert.equal(ask('expired'), 'expired');
  assert.equal(ask('added'), 'offer');                 // listed in a moment; if it was removed later the offer is right
  assert.equal(ask('waiting', 'another-project'), 'offer');      // another project's ask says nothing about this folder
});

test('the latest ask for the folder decides', () => {
  const requests = [{ id: '1', name: 'store-api', state: 'declined' }, { id: '2', name: 'other', state: 'waiting' }, { id: '3', name: 'STORE-API', state: 'waiting' }];
  assert.equal(runnerProjectOffer([runner({ requests })], 'connected', 'store-api').kind, 'waiting');
});

test('each state has its own sentence, the first names the folder, and none of them asks for a path', () => {
  const base = { runnerId: 'r1', runnerName: 'my pc', folderName: 'store-api' };
  assert.match(runnerProjectHint({ ...base, kind: 'offer' }), /does not know "store-api" yet/);
  assert.match(runnerProjectHint({ ...base, kind: 'offer' }), /only the folder's name is sent/);
  assert.match(runnerProjectHint({ ...base, kind: 'waiting' }), /window on your computer asks you to choose the folder/);
  assert.match(runnerProjectHint({ ...base, kind: 'declined' }), /closed without choosing/);
  assert.match(runnerProjectHint({ ...base, kind: 'no_picker' }), /saapp-runner add-project --root/);
  assert.match(runnerProjectHint({ ...base, kind: 'expired' }), /timed out/);
  assert.match(runnerProjectHint({ ...base, kind: 'error' }), /could not add that folder/);
  const words = new Set(['offer', 'waiting', 'declined', 'no_picker', 'expired', 'error'].map(kind => runnerProjectHint({ ...base, kind })));
  assert.equal(words.size, 6);
});

test('the card draws it only for a signed-in person, sends only the folder name, and the page refreshes the runner list after the ask', () => {
  const column = readFileSync(new URL('./SessionColumn.tsx', import.meta.url), 'utf8');
  const chat = readFileSync(new URL('../pages/Chat.tsx', import.meta.url), 'utf8');
  assert.match(column, /const projectOffer = runnerEnabled \? runnerProjectOffer\(runners, folder\.status, folder\.name\) : null;/);   // a guest has no runner
  assert.match(column, /\{projectOffer && <RunnerProjectAsk offer=\{projectOffer\} onAsked=\{onRunnerAsked\} \/>\}/);
  assert.match(column, /await api\.addRunnerProject\(offer\.runnerId, offer\.folderName\);/);        // the name, never a path
  assert.match(column, /folderControls=\{folderControls\} runnerSetup=\{runnerSetup\} onRunnerAsked=\{onRunnerAsked\} \/>/);   // the column hands it on
  assert.match(chat, /onRunnerAsked: checkRunnerSoon,/);                  // the phone strip's Connection card
  assert.match(chat, /onRunnerAsked=\{checkRunnerSoon\}/);                // the column
});

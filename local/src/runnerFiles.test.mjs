// Run with: node --test src/runnerFiles.test.mjs   (from local/; Node 22.18+ runs the TypeScript module directly)
// "Let Sonic read this project's files": the runner sends a project's text files after a window on the person's computer says yes (docs/runner-files-plan.md).
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { FILES_MIN_VERSION, canShareFiles, filesRequestFor, requestSentence, requestsOpen, requestsToShow, runnerCanShareFiles, sharingLine } from './runnerLogic.ts';
import { runnerProjectOffer } from './components/sessionColumnLogic.ts';
import { HELP_TIPS } from './components/helpTips.ts';

const files = (state, name = 'store-api', project_id = 'p1') => ({ id: `f-${state}`, name, state, kind: 'files', project_id });

test('each state of a request to send files says what happens, and says nothing is sent unless the person says yes on their computer', () => {
  assert.match(requestSentence(files('waiting')), /pick up the request to share the files of "store-api"/);
  assert.match(requestSentence(files('asked')), /window on your computer asks whether to send the files of "store-api".*Nothing is sent unless you say yes there/);
  assert.match(requestSentence(files('shared')), /were sent\. Sonic can read them now/);
  assert.match(requestSentence(files('declined')), /said no.*nothing was sent/);
  assert.match(requestSentence(files('no_picker')), /can't show a window.*nothing was sent/);
  assert.match(requestSentence(files('error')), /could not be sent/);
  assert.match(requestSentence(files('expired')), /Nobody answered.*nothing was sent/);
  assert.equal(new Set(['waiting', 'asked', 'shared', 'declined', 'no_picker', 'error', 'expired'].map(state => requestSentence(files(state)))).size, 7);
});

test('a request to add a project reads exactly as before', () => {
  assert.match(requestSentence({ id: 'a', name: 'app', state: 'asked' }), /choose the folder for "app"/);
  assert.match(requestSentence({ id: 'a', name: 'app', state: 'declined' }), /"app" was not added/);
  assert.equal(requestSentence({ id: 'a', name: 'app', state: 'added' }), '');
});

test('the page keeps looking while a files request is open and shows the ones that are not "added"', () => {
  assert.equal(requestsOpen([files('waiting')]), true);
  assert.equal(requestsOpen([files('shared')]), false);
  assert.deepEqual(requestsToShow([files('shared'), { id: 'x', name: 'app', state: 'added' }, files('declined')]).map(r => r.state), ['shared', 'declined']);
});

test('the button is pressable only while the runner answers and no ask for that project is still open', () => {
  const requests = [files('waiting', 'store-api', 'p1'), files('declined', 'other', 'p2')];
  assert.equal(canShareFiles(true, [], 'p1'), true);
  assert.equal(canShareFiles(false, [], 'p1'), false);
  assert.equal(canShareFiles(true, requests, 'p1'), false);           // already asked: the window is open on the computer
  assert.equal(canShareFiles(true, requests, 'p2'), true);            // an answered ask does not block asking again
  assert.equal(canShareFiles(true, [files('waiting', 'store-api', 'p1'), files('asked', 'store-api', 'p1')], 'p1'), false);
  assert.equal(filesRequestFor([files('declined'), files('asked')], 'p1').state, 'asked');                // the latest one
  assert.equal(filesRequestFor([{ id: 'a', name: 'app', state: 'asked' }], 'p1'), undefined);             // an ask to add a project is not one of these
});

test('the line under a project says how many files Sonic reads and when they were sent, only for a copy the runner filled', () => {
  const NOW = Date.parse('2026-11-12T12:00:00Z');
  const copy = (over = {}) => ({ connected: true, name: 'store-api', file_count: 142, synced_at: (NOW - 5 * 60000) / 1000, source: 'runner', ...over });
  assert.equal(sharingLine(copy(), 'store-api', NOW), 'Sonic is reading 142 files from this project (sent 5 min ago).');
  assert.equal(sharingLine(copy({ file_count: 1, synced_at: NOW / 1000 }), 'Store-API', NOW), 'Sonic is reading 1 file from this project (sent just now).');
  assert.equal(sharingLine(copy({ synced_at: (NOW - 3 * 3600000) / 1000 }), 'store-api', NOW), 'Sonic is reading 142 files from this project (sent 3 h ago).');
  assert.equal(sharingLine(copy({ source: 'browser' }), 'store-api', NOW), null);                 // the browser's folder is not the runner's
  assert.equal(sharingLine(copy(), 'another', NOW), null);                                         // a different project's copy
  assert.equal(sharingLine(copy({ connected: false }), 'store-api', NOW), null);
  assert.equal(sharingLine(null, 'store-api', NOW), null);
});

test('a files request with the same name as the open folder is not mistaken for an ask to add that project', () => {
  const runner = (requests) => [{ id: 'r1', name: 'my pc', connected: true, last_seen: '2026-11-12T12:00:00Z', projects: [], requests }];
  assert.equal(runnerProjectOffer(runner([files('waiting')]), 'connected', 'store-api').kind, 'offer');           // not "waiting": nobody asked to add it
  assert.equal(runnerProjectOffer(runner([files('declined')]), 'connected', 'store-api').kind, 'offer');
  assert.equal(runnerProjectOffer(runner([{ id: 'q', name: 'store-api', state: 'waiting' }, files('declined')]), 'connected', 'store-api').kind, 'waiting');
  assert.equal(runnerProjectOffer(runner([files('shared')]), 'connected', 'store-api').kind, 'offer');
});

test('the card asks through the runner endpoint with the project id (never a path), reads where the copy came from, and can stop sharing', () => {
  const card = readFileSync(new URL('./components/RunnerSettingsCard.tsx', import.meta.url), 'utf8');
  const api = readFileSync(new URL('./api.ts', import.meta.url), 'utf8');
  assert.match(card, /await api\.shareRunnerFiles\(runner\.id, project\.id\);/);
  assert.match(card, /await disconnectLocalWorkspace\(\);/);
  assert.match(card, /setWorkspace\(await getLocalWorkspaceStatus\(\)\);/);
  assert.match(card, /disabled=\{!canShareFiles\(runner\.connected, runner\.requests, project\.id, runner\.version\) \|\| sharing === project\.id\}/);
  assert.match(card, /data-help="runner\.shareFiles"/);
  assert.match(api, /`\/api\/runner\/runners\/\$\{encodeURIComponent\(id\)\}\/share-files`, \{ method: 'POST', body: JSON\.stringify\(\{ project_id: projectId \}\) \}/);
  assert.match(api, /source\?: 'browser' \| 'runner';/);
  assert.match(api, /export type RunnerRequestState = 'waiting' \| 'asked' \| 'added' \| 'shared'/);
  assert.ok(HELP_TIPS['runner.shareFiles'].what.includes('nothing is sent unless you say yes'));
});

test('a runner older than the first that can send files cannot be asked, and the page says to update it', () => {
  assert.equal(FILES_MIN_VERSION, '0.2.2');
  for (const [version, expected] of [['0.2.2', true], ['0.2.10', true], ['0.3.0', true], ['1.0.0', true], ['0.2.1', false], ['0.1.9', false], ['', false], [undefined, false], ['0.2', false], ['x.y.z', false]]) {
    assert.equal(runnerCanShareFiles(version), expected, String(version));
  }
  assert.equal(canShareFiles(true, [], 'p1', '0.2.1'), false);
  assert.equal(canShareFiles(true, [], 'p1', '0.2.2'), true);
  const card = readFileSync(new URL('./components/RunnerSettingsCard.tsx', import.meta.url), 'utf8');
  assert.match(card, /canShareFiles\(runner\.connected, runner\.requests, project\.id, runner\.version\)/);
  assert.match(card, /\{!runnerCanShareFiles\(runner\.version\) && <span className="account-hint runner-warning">This runner is too old to send files\./);
  const privacy = readFileSync(new URL('./pages/Privacy.tsx', import.meta.url), 'utf8');
  assert.match(privacy, /Let Sonic read this project's files/);
  assert.match(privacy, /Nothing is sent unless you say yes in that window/);
  assert.match(privacy, /Stop sharing removes the copy/);
});

// Run with: node --test src/runnerLook.test.mjs   (from local/; Node 22.18+ runs the TypeScript module directly)
// The card for starting a project's server and looking at a page on the person's own computer (components/RunnerCheckCard.tsx, runnerLogic.ts; plan in docs/local-browser-plan.md).
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { SESSION_IDLE_MINUTES, SESSION_LOOKS, browserLine, canStopServer, deviceLabel, headline, isLook, isServe, lookLines, lookNote, pageHeadline, serveNote, serverExposed, verdict } from './runnerLogic.ts';

const base = { id: 'j1', runner_id: 'r1', check: 'dev', command_shown: 'npm run dev', files: [], status: 'awaiting_approval', created_at: null, expires_at: null, finished_at: null, exit_code: null, output_tail: '', truncated: false, timed_out: false, duration: null, error: '' };
const serve = (over = {}) => ({ ...base, kind: 'serve', ...over });
const look = (over = {}) => ({ ...base, kind: 'look', check: 'look', command_shown: 'http://localhost:5173/pricing', url: 'http://localhost:5173/pricing', device: 'phone', ...over });

test('a server card and a look card are told apart from checks and commands', () => {
  assert.equal(isServe(serve()), true);
  assert.equal(isLook(look()), true);
  assert.equal(isServe(look()) || isLook(serve()), false);
  assert.equal(isServe({ kind: 'command' }) || isLook({ kind: 'check' }) || isLook({}), false);
});

test('approving a server card is said to cover a preview session: how many looks, how long, and that the person can stop it', () => {
  const note = serveNote(serve());
  assert.match(note, new RegExp(`up to ${SESSION_LOOKS} times without asking again`));
  assert.match(note, new RegExp(`${SESSION_IDLE_MINUTES} minutes unused or you stop it`));
  assert.match(note, /own privileges/);
  assert.equal(SESSION_LOOKS, 10);
  assert.equal(SESSION_IDLE_MINUTES, 30);
});

test('a look says where it goes and where the picture and text go, and a session look says it is part of the approval', () => {
  const alone = lookNote(look());
  assert.match(alone, /hidden browser on your computer opens only this address/);
  assert.match(alone, /every request to anywhere else is blocked/);
  assert.match(alone, /sent to Sonic and its AI model/);
  assert.doesNotMatch(alone, /preview session/);
  assert.match(lookNote(look({ session: true })), /^Part of the preview session you approved\./);
});

test('the window sizes are named the way the runner has them', () => {
  assert.equal(deviceLabel('phone'), 'phone (390 × 844)');
  assert.equal(deviceLabel('tablet'), 'tablet (820 × 1180)');
  assert.equal(deviceLabel('desktop'), 'desktop (1280 × 800)');
  assert.equal(deviceLabel(undefined), 'desktop');
});

test('the lead line follows the server job from waiting to running on a port, or why it did not start', () => {
  assert.equal(headline(serve()), 'Waiting for you to press Run');
  assert.equal(headline(serve({ status: 'approved' })), 'Approved. Waiting for your runner to pick it up...');
  assert.equal(headline(serve({ status: 'running' })), 'Starting the server...');
  assert.equal(headline(serve({ status: 'done', exit_code: 0, server: { status: 'running', port: 5173 } })), 'Running on port 5173');
  assert.equal(headline(serve({ status: 'failed', error: 'The server stopped by itself.' })), 'Did not start: The server stopped by itself.');
  assert.equal(headline(serve({ status: 'cancelled' })), 'Cancelled');
  assert.equal(headline(serve({ status: 'expired', error: 'Nobody approved it in time.' })), 'Not started: Nobody approved it in time.');
});

test('the lead line for a look says when it started by itself inside a session', () => {
  assert.equal(headline(look()), 'Waiting for you to press Run');
  assert.match(headline(look({ status: 'approved', auto_approved: true, session: true })), /Part of your preview session, so it started by itself/);
  assert.equal(headline(look({ status: 'running' })), 'Looking at the page...');
  assert.equal(headline(look({ status: 'done', exit_code: 0, duration: 2.4 })), 'Looked (2.4 s)');
  assert.match(headline(look({ status: 'failed', error: 'The page could not be opened (net::ERR_CONNECTION_REFUSED). Is the server running on that port?' })), /^Could not look: The page could not be opened/);
  assert.equal(pageHeadline(look({ status: 'cancelled' })), 'Cancelled');
  assert.equal(verdict(serve({ status: 'done', exit_code: 0 })), 'passed');
});

test('what a look read is listed problems first, with an overflow named by the elements that stick out', () => {
  const lines = lookLines(look({
    look: { viewport: [390, 844], scroll_width: 600, overflow_x: true, wide_elements: [{ el: 'div#banner', right: 600, width: 580 }, { el: 'table.t', right: 540, width: 500 }], viewport_meta: false,
            console: ['error: boom'], failed: ['404 /missing.json'], blocked: ['fonts.googleapis.com'] },
  }));
  assert.equal(lines[0], 'Wider than the window: the content is 600 px wide in a 390 px window (div#banner reaches 600 px; table.t reaches 540 px).');
  assert.match(lines[1], /^No viewport tag: a phone shows this page zoomed out/);
  assert.deepEqual(lines.slice(2), ['Console: error: boom', 'Request: 404 /missing.json', 'Blocked (outside addresses, by design): fonts.googleapis.com']);
});

test('a page that fits says so, and a desktop look never mentions a viewport tag', () => {
  assert.deepEqual(lookLines(look({ device: 'desktop', look: { viewport: [1280, 800], overflow_x: false, viewport_meta: false } })), ['Fits the width of the window (1280 px).']);
  assert.deepEqual(lookLines(look({ look: undefined })), []);
  assert.deepEqual(lookLines(look({ look: {} })), []);
});

test('the stop button is offered only for a server that is up and not already stopped, and the network warning only when the runner said so', () => {
  const up = serve({ status: 'done', exit_code: 0, server: { status: 'running', port: 5173 } });
  assert.equal(canStopServer(up), true);
  assert.equal(canStopServer({ ...up, session_stopped: true }), false);
  assert.equal(canStopServer({ ...up, status: 'failed' }), false);
  assert.equal(canStopServer(look({ status: 'done' })), false);
  assert.equal(canStopServer(serve({ status: 'done', server: { status: 'exited' } })), false);
  assert.equal(serverExposed({ server: { exposed: true } }), true);
  assert.equal(serverExposed({ server: { exposed: false } }) || serverExposed({}), false);
});

test('Settings says which browser the runner found, or what is missing, and nothing for a runner that cannot look yet', () => {
  assert.match(browserLine({ version: '0.2.0', browser: 'edge', looks: true }), /look at your pages and start your project's server here \(using Edge\)/);
  assert.match(browserLine({ version: '0.2.0', browser: 'chrome', looks: true }), /using Chrome/);
  assert.match(browserLine({ version: '0.2.0', browser: 'custom', looks: true }), /using your own browser/);
  assert.match(browserLine({ version: '0.2.0', browser: '', looks: false }), /No Chrome or Edge was found/);
  assert.equal(browserLine({ version: '0.1.3', browser: '', looks: false }), '');
  assert.equal(browserLine({ version: 'weird', browser: '', looks: false }), '');
});

test('the card shows the picture through the login, lets go of it, offers Stop for a running server, and shows everything as plain text', () => {
  const card = readFileSync(new URL('./components/RunnerCheckCard.tsx', import.meta.url), 'utf8');
  assert.match(card, /api\.getLookPicture\(job\.id\)/);
  assert.match(card, /URL\.revokeObjectURL\(/);
  assert.match(card, /api\.stopRunnerServer\(job\.runner_id, job\.project_id\)/);
  assert.match(card, /canStopServer\(job\) && \(/);
  assert.match(card, /aria-label=\{look \? 'The address that will be opened' : 'The command that will run'\}/);
  assert.doesNotMatch(card, /dangerouslySetInnerHTML/);
  const api = readFileSync(new URL('./api.ts', import.meta.url), 'utf8');
  assert.match(api, /\/api\/runner\/jobs\/\$\{encodeURIComponent\(jobId\)\}\/screenshot/);
  assert.match(api, /URL\.createObjectURL\(await res\.blob\(\)\)/);
  assert.match(api, /\/api\/runner\/runners\/\$\{encodeURIComponent\(runnerId\)\}\/stop-server/);
  const settings = readFileSync(new URL('./components/RunnerSettingsCard.tsx', import.meta.url), 'utf8');
  assert.match(settings, /runner\.connected && browserLine\(runner\)/);
});

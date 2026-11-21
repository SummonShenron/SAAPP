// Run with: node --test src/components/providerAlertLogic.test.mjs   (from local/)
// What an admin is told when a model provider is refusing Sonic's API key (2026-11-20): the pure rules (providerAlertLogic.ts) and where the strip is wired in.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { ALERT_POLL_MS, alertsOf, headline, sinceText } from './providerAlertLogic.ts';

const read = name => readFileSync(new URL(name, import.meta.url), 'utf8').replace(/\r\n/g, '\n');
const claude = { provider: 'claude', label: 'Claude (Anthropic)', since: '2026-11-17T20:00:00+00:00', last: '2026-11-20T01:00:00+00:00', failures: 41, message: 'Claude (Anthropic) is rejecting Sonic\'s API key, so its calls are falling back to Gemini (which costs more).' };
const openai = { ...claude, provider: 'openai', label: 'OpenAI', message: 'OpenAI is rejecting Sonic\'s API key.' };

test('the alerts are the well-formed items the server sent and nothing else', () => {
  assert.deepEqual(alertsOf({ provider_alerts: [claude] }), [claude]);
  assert.deepEqual(alertsOf({ provider_alerts: [claude, openai] }), [claude, openai]);
  for (const odd of [null, undefined, {}, 'text', 7, { provider_alerts: null }, { provider_alerts: 'claude' }, { provider_alerts: {} }, { provider_alerts: [] }]) assert.deepEqual(alertsOf(odd), [], String(odd));
  assert.deepEqual(alertsOf({ provider_alerts: [null, 3, {}, { provider: 'claude' }, { ...claude, message: 4 }, claude] }), [claude], 'a half-made item is dropped, a good one kept');
});

test('the headline says which provider, or how many', () => {
  assert.equal(headline([]), '');
  assert.equal(headline([claude]), 'Claude (Anthropic) is rejecting Sonic\'s API key');
  assert.equal(headline([claude, openai]), '2 model providers are rejecting Sonic\'s API keys (Claude (Anthropic), OpenAI)');
});

test('how long it has been going on is said in plain words', () => {
  const now = Date.parse('2026-11-20T12:00:00Z');
  assert.equal(sinceText('2026-11-20T12:00:00Z', now), 'since just now');
  assert.equal(sinceText('2026-11-20T11:59:30Z', now), 'since just now');
  assert.equal(sinceText('2026-11-20T11:59:00Z', now), 'for 1 minute');
  assert.equal(sinceText('2026-11-20T11:15:00Z', now), 'for 45 minutes');
  assert.equal(sinceText('2026-11-20T11:00:00Z', now), 'for 1 hour');
  assert.equal(sinceText('2026-11-20T04:00:00Z', now), 'for 8 hours');
  assert.equal(sinceText('2026-11-17T12:00:00Z', now), 'for 3 days');
  assert.equal(sinceText('not a time', now), '');
  assert.equal(sinceText('2026-11-20T13:00:00Z', now), 'since just now', 'a clock a little ahead is not negative');
});

test('it asks once a minute', () => {
  assert.equal(ALERT_POLL_MS, 60_000);
});

test('it is wired in: the admin budget view carries it, the strip is mounted under the top bar for admins only, and it asks, keeps what it knew when an ask fails, and stops when it goes', () => {
  const api = read('../api.ts');
  assert.match(api, /provider_alerts\?: ProviderAlertItem\[\];/);
  assert.match(api, /import type \{ ProviderAlertItem \} from '\.\/components\/providerAlertLogic';/);
  const layout = read('./Layout.tsx');
  assert.match(layout, /import ProviderAlert from "\.\/ProviderAlert";/);
  assert.match(layout, /<TopBar [^\n]*\/>\n\s+<ProviderAlert enabled=\{isAdmin && authReady\} \/>\n\s+<div className="app-content">/, 'right under the top bar, only for an admin who is signed in');
  const strip = read('./ProviderAlert.tsx');
  assert.match(strip, /api\.getBudget\(\)\.then\(view => \{ if \(!cancelled\) setAlerts\(alertsOf\(view\)\); \}\)\.catch\(/);
  assert.match(strip, /window\.setInterval\(ask, ALERT_POLL_MS\)/);
  assert.match(strip, /return \(\) => \{ cancelled = true; window\.clearInterval\(timer\); \};/);
  assert.match(strip, /if \(!enabled\) return undefined;/, 'not an admin: nothing is asked');
  assert.match(strip, /if \(!enabled \|\| alerts\.length === 0\) return null;/);
  assert.match(strip, /role="alert"/);
  assert.match(read('./__styles__/ProviderAlert.css'), /\.provider-alert \{/);
});

// Run with: node --test src/pages/sonicHealthGuest.test.mjs   (from local/; Node 22.18+ runs the TypeScript module directly)
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { ACTIVATION_STEPS, SIGNUP_AFTER, SIGNUP_SOURCES, VOICE_REASONS, WELCOME_KINDS, activationRows, activationTotal, cheaperWrote, nudgesShown, share, signupAfterRows, signupRows, signupTotal, voiceRows, voiceTotal, welcomeRows, welcomeTotal } from './sonicHealthGuest.ts';

const counters = readFileSync(new URL('../../../backend/utils/self_counters.py', import.meta.url), 'utf8');
const group = name => {
  const found = counters.match(new RegExp(`"${name}": \\(([^)]*)\\)`));
  assert.ok(found, `${name} is a counter group`);
  return [...found[1].matchAll(/"([a-z0-9_]+)"/g)].map(m => m[1]);
};

test('the page lists exactly the counter groups the server records: every sign-up button, every free-turn bucket and every reason a voice was chosen', () => {
  assert.deepEqual([...SIGNUP_SOURCES.map(([key]) => key)].sort(), [...group('guest_signup')].sort());
  assert.deepEqual(SIGNUP_AFTER.map(([key]) => key), group('guest_signup_after'));
  assert.deepEqual([...VOICE_REASONS.map(([key]) => key)].sort(), [...group('reply_tier')].sort());
});

test('the sign-up presses are summed by button and by how many free turns had been used, and the nudges are summed over the four account features', () => {
  const totals = { guest_signup_banner: 3, guest_signup_card_fix: 2, guest_signup_nudge_memory: 1, guest_signup_after_none: 1, guest_signup_after_three_to_five: 5, guest_nudge_fix: 4, guest_nudge_tidy: 1, turns_total: 90 };
  assert.equal(signupTotal(totals), 6);
  assert.deepEqual(signupRows(totals).filter(row => row.count).map(row => [row.key, row.count]), [['banner', 3], ['nudge_memory', 1], ['card_fix', 2]]);
  assert.deepEqual(signupAfterRows(totals).filter(row => row.count).map(row => [row.key, row.count]), [['none', 1], ['three_to_five', 5]]);
  assert.equal(nudgesShown(totals), 5);
  assert.equal(signupTotal({}), 0);
  assert.equal(nudgesShown({}), 0);
});

test('a missing, negative or non-numeric counter reads as zero, never as a gap or a NaN on the page', () => {
  const junk = { guest_signup_banner: -4, guest_signup_limit: 'x', guest_signup_demo_diff: undefined, reply_tier_auto_plain: NaN };
  assert.equal(signupTotal(junk), 0);
  assert.equal(voiceTotal(junk), 0);
});

test('the voice rows say which replies a cheaper model wrote and the share, and a page with nothing counted shows no share', () => {
  const totals = { reply_tier_lite_setting: 2, reply_tier_haiku_setting: 1, reply_tier_auto_plain: 7, reply_tier_checkin: 3, reply_tier_emotional: 5, reply_tier_standard_setting: 12 };
  assert.equal(voiceTotal(totals), 30);
  assert.equal(cheaperWrote(totals), 10);
  assert.equal(share(cheaperWrote(totals), voiceTotal(totals)), '33%');
  assert.equal(share(0, 0), '-');
  assert.deepEqual(voiceRows(totals).filter(row => row.count).map(row => row.key), ['lite_setting', 'haiku_setting', 'auto_plain', 'standard_setting', 'emotional', 'checkin']);
  assert.ok(voiceRows(totals).find(row => row.key === 'checkin').label.startsWith('Kept on the standard voice'));
});

test('the Turns tab shows both cards from the raw totals, and each shows nothing until something was counted', () => {
  const page = readFileSync(new URL('./SonicHealth.tsx', import.meta.url), 'utf8');
  assert.match(page, /<ReplyVoicesCard totals=\{health\.counters\.totals\} previous=\{health\.counters\.previous_totals\} \/>/);
  assert.match(page, /<GuestFunnelCard totals=\{health\.counters\.totals\} previous=\{health\.counters\.previous_totals\} \/>/);
  assert.match(page, /const total = voiceTotal\(totals\);\s*if \(total <= 0\) return null;/);
  assert.match(page, /if \(pressed <= 0 && nudges <= 0\) return null;/);
  assert.match(page, /never what they wrote/);                                                  // the card says plainly what is and is not recorded
});

test('the new-accounts card lists exactly the first steps and welcome cards the server counts, summed once each', () => {
  assert.deepEqual(ACTIVATION_STEPS.map(([key]) => key), group('activation'));
  assert.deepEqual(WELCOME_KINDS.map(([key]) => key), group('welcome_card'));
  const totals = { activation_folder: 3, activation_memory: 2, welcome_card_demo: 5, welcome_card_document: 1, turns_total: 40 };
  assert.equal(activationTotal(totals), 5);
  assert.equal(welcomeTotal(totals), 6);
  assert.deepEqual(activationRows(totals).filter(row => row.count).map(row => [row.key, row.count]), [['folder', 3], ['memory', 2]]);
  assert.deepEqual(welcomeRows(totals).filter(row => row.count).map(row => [row.key, row.count]), [['demo', 5], ['document', 1]]);
  assert.equal(activationTotal({}), 0);
  const page = readFileSync(new URL('./SonicHealth.tsx', import.meta.url), 'utf8');
  assert.match(page, /<NewAccountsCard totals=\{health\.counters\.totals\} previous=\{health\.counters\.previous_totals\} \/>/);
  assert.match(page, /if \(steps <= 0 && presses <= 0\) return null;/);
});

// Run with: node --test src/pages/voiceReview.test.mjs   (from local/; Node 22.18+ runs the TypeScript module directly)
// The "Its voice" tab on Sonic Health: an admin approves or removes voice examples and situation facts, and sees how often each was matched (backend/utils/voice_review.py).
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { ACTION_LABELS, FILTERS, FIRST_ROWS, actionsFor, filterItems, stateLine, summaryLine, thinWarning, usageLine } from './voiceReviewLogic.ts';
import { HELP_TIPS } from '../components/helpTips.ts';
import { GROUPS, TABS } from './sonicHealthTabs.ts';

const tab = readFileSync(new URL('./SonicHealthVoice.tsx', import.meta.url), 'utf8');
const api = readFileSync(new URL('../api.ts', import.meta.url), 'utf8');
const page = readFileSync(new URL('./SonicHealth.tsx', import.meta.url), 'utf8');

const item = (over = {}) => ({ kind: 'example', id: 'x', situation: 'a moment', asks: ['a message'], approved: true, default: true, decision: null, used: 0, tone: 'light', text: ['a reply'], ...over });
const set = [
  item({ id: 'a', used: 3 }), item({ id: 'b', approved: false, default: false }), item({ id: 'c', approved: true, default: false, decision: 'approved', used: 1 }),
  item({ id: 'd', approved: false, default: true, decision: 'removed', used: 2 }), item({ id: 'e' }),
  item({ kind: 'fact', id: 'f', approved: false, default: false, tone: '', text: ['A fact.'] }), item({ kind: 'fact', id: 'g', approved: true, default: false, decision: 'approved', tone: '', text: ['Another.'], used: 4 }),
];

test('a list narrows to one kind and then by whether it is in use or was ever matched', () => {
  assert.deepEqual(filterItems(set, 'example', 'all').map(i => i.id), ['a', 'b', 'c', 'd', 'e']);
  assert.deepEqual(filterItems(set, 'example', 'approved').map(i => i.id), ['a', 'c', 'e']);
  assert.deepEqual(filterItems(set, 'example', 'drafts').map(i => i.id), ['b', 'd']);
  assert.deepEqual(filterItems(set, 'example', 'unused').map(i => i.id), ['e']);                                  // in use and never matched: the question the counts are for
  assert.deepEqual(filterItems(set, 'fact', 'all').map(i => i.id), ['f', 'g']);
  assert.deepEqual(filterItems([], 'fact', 'unused'), []);
  assert.deepEqual(FILTERS.map(f => f.id), ['all', 'approved', 'drafts', 'unused']);
});

test('the summary says how many may be used and how many of those were matched', () => {
  assert.equal(summaryLine(set, 'example', 30), '3 of 5 examples may be used. 2 of those were matched to a message in the last 30 days.');
  assert.equal(summaryLine(set, 'fact', 30), '1 of 2 facts may be used. 1 of those was matched to a message in the last 30 days.');
  assert.equal(summaryLine([], 'fact', 30), 'None yet.');
});

test('a row says who decided and what that means', () => {
  assert.equal(stateLine(item()), 'Approved in the registry: it is used.');
  assert.equal(stateLine(item({ approved: false, default: false })), 'A draft: it is never used until you approve it.');
  assert.equal(stateLine(item({ approved: true, default: false, decision: 'approved' })), 'Approved here (the registry has it as a draft): it is used.');
  assert.equal(stateLine(item({ approved: false, default: true, decision: 'removed' })), 'Removed here (the registry has it approved): it is not used.');
  assert.equal(stateLine(item({ approved: false, default: false, decision: 'removed' })), 'Removed here: it is not used.');
});

test('a row says how often it was matched, and does not call a draft "never matched"', () => {
  assert.equal(usageLine({ approved: true, used: 1 }, 30), 'Matched 1 time in the last 30 days.');
  assert.equal(usageLine({ approved: true, used: 5 }, 30), 'Matched 5 times in the last 30 days.');
  assert.equal(usageLine({ approved: true, used: 0 }, 30), 'In use, but no message matched it in the last 30 days.');
  assert.equal(usageLine({ approved: false, used: 0 }, 30), '');                                           // stateLine already says a draft or removed item is not used
  assert.equal(usageLine({ approved: false, used: 2 }, 30), 'It was matched 2 times in the last 30 days, before it stopped being used.');
});

test('the buttons are remove for an item in use, approve for one that is not, and a way back once a person has decided', () => {
  assert.deepEqual(actionsFor({ approved: true, decision: null }), ['remove']);
  assert.deepEqual(actionsFor({ approved: false, decision: null }), ['approve']);
  assert.deepEqual(actionsFor({ approved: true, decision: 'approved' }), ['remove', 'reset']);
  assert.deepEqual(actionsFor({ approved: false, decision: 'removed' }), ['approve', 'reset']);
  assert.deepEqual(ACTION_LABELS, { approve: 'Approve', remove: 'Remove', reset: 'Back to the registry' });
});

test('removals that take the set under the registry floors (6 light, 5 hurting) are said', () => {
  const many = (tone, n) => Array.from({ length: n }, (_, k) => item({ id: `${tone}${k}`, tone }));
  assert.equal(thinWarning([...many('light', 6), ...many('hurting', 5)]), '');
  assert.equal(thinWarning([...many('light', 6), ...many('hurting', 4)]), 'Only 4 hurting (at least 5) examples are in use, so the voice has less to match against.');
  assert.match(thinWarning([...many('light', 2), ...many('hurting', 1)]), /^Only 2 light .*and 1 hurting/);
  assert.equal(thinWarning([...many('light', 6), ...many('hurting', 5), item({ kind: 'fact', approved: true, tone: '' })]), '');          // facts do not count toward the examples' floors
  assert.equal(thinWarning([...many('light', 6), ...many('hurting', 4), item({ id: 'off', tone: 'hurting', approved: false })]), 'Only 4 hurting (at least 5) examples are in use, so the voice has less to match against.');     // a draft is not in use
  assert.ok(FIRST_ROWS >= 5);
});

test('the tab asks the admin endpoints, decides one item at a time, and never edits the registry itself', () => {
  assert.match(api, /'\/api\/admin\/voice-review', 'GET'/);
  assert.match(api, /`\/api\/admin\/voice-review\/\$\{kind\}\/\$\{encodeURIComponent\(id\)\}\/\$\{action\}`, 'POST'/);
  assert.match(tab, /api\.getVoiceReview\(\)/);
  assert.match(tab, /api\.decideVoiceReview\(item\.kind, item\.id, action\)/);
  assert.match(tab, /disabled=\{busy === key\}/);
  assert.match(tab, /it does not edit the registry file/);
  assert.match(tab, /none is used until you approve it here/);
});

test('the tab is mounted in Sonic Health under the Sonic menu, and each card carries a help tip', () => {
  assert.match(page, /import \{ VoiceTab \} from '\.\/SonicHealthVoice'/);
  assert.match(page, /\{tab === 'voice' && <VoiceTab \/>\}/);
  assert.ok(TABS.some(t => t.id === 'voice' && t.label === 'Its voice'));
  assert.ok(GROUPS.find(g => g.id === 'sonic').tabs.includes('voice'));
  assert.match(tab, /const HELP_VOICE = \{ example: 'voice\.examples', fact: 'voice\.facts' \}/);
  assert.ok(HELP_TIPS['voice.examples'].what.includes('Approve or remove one here'));
  assert.ok(HELP_TIPS['voice.facts'].what.includes('Every fact is a draft until you approve it here'));
});

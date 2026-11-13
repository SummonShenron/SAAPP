// Run with: node --test src/pages/patternTemplates.test.mjs   (from local/; Node 22.18+ runs the TypeScript module directly)
// The "Pattern stories" card on Sonic Health: a person approves each template once; code writes the notes (backend/utils/pattern_notes.py).
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { backfillMessage, canBackfill, limitsLine, templateState } from './patternTemplatesLogic.ts';
import { HELP_TIPS } from '../components/helpTips.ts';

const card = readFileSync(new URL('./SonicHealthExperiences.tsx', import.meta.url), 'utf8');
const api = readFileSync(new URL('../api.ts', import.meta.url), 'utf8');

test('a template says whether it writes, and what it has written', () => {
  assert.equal(templateState({ approved: false, notes: 0, latest: null }), 'Not approved: nothing is written for it.');
  assert.equal(templateState({ approved: false, notes: 4, latest: '2026-11-08' }), 'Not approved: nothing is written for it.');      // revoked: the notes stay, but it writes no more
  assert.equal(templateState({ approved: true, notes: 0, latest: null }), 'Approved: it writes a note when a finished day fits. No note yet.');
  assert.equal(templateState({ approved: true, notes: 1, latest: '2026-11-08' }), 'Approved: it writes a note when a finished day fits. 1 note written, the latest for 2026-11-08.');
  assert.match(templateState({ approved: true, notes: 4, latest: '2026-11-08' }), /4 notes written/);
});

test('the earlier-days button is offered only while a template is approved', () => {
  assert.equal(canBackfill([]), false);
  assert.equal(canBackfill([{ approved: false }, { approved: false }]), false);
  assert.equal(canBackfill([{ approved: false }, { approved: true }]), true);
});

test('what the earlier-days button reports', () => {
  assert.equal(backfillMessage({ written: 0, capped: 0 }), 'No earlier day fits an approved template, or they were all written already.');
  assert.equal(backfillMessage({ written: 1, capped: 0 }), '1 note written for earlier days.');
  assert.equal(backfillMessage({ written: 4, capped: 0 }), '4 notes written for earlier days.');
  assert.match(backfillMessage({ written: 10, capped: 5 }), /10 notes written for earlier days; 5 more days fit but were left out.*Press it again/);
  assert.match(backfillMessage({ written: 10, capped: 1 }), /1 more day fit/);
});

test('the limits are said in one sentence from the numbers the server sends', () => {
  assert.equal(limitsLine({ per_week: 2, recent_days: 14, backfill: 10 }), 'At most 2 a week and one a day, looking at the last 14 days; "write the earlier days" writes up to 10 at once and ignores the weekly limit.');
});

test('the card asks the admin endpoints, approves per template, never writes a note itself, and hides Edit on a pattern note', () => {
  assert.match(api, /'\/api\/admin\/pattern-templates', 'GET'/);
  assert.match(api, /`\/api\/admin\/pattern-templates\/\$\{encodeURIComponent\(id\)\}\/approve`, 'POST'/);
  assert.match(api, /`\/api\/admin\/pattern-templates\/\$\{encodeURIComponent\(id\)\}\/revoke`, 'POST'/);
  assert.match(api, /'\/api\/admin\/pattern-templates\/backfill', 'POST'/);
  assert.match(api, /key\?: string; template\?: string;/);
  assert.match(card, /approve \? await api\.approvePatternTemplate\(template\.id\) : await api\.revokePatternTemplate\(template\.id\)/);
  assert.match(card, /disabled=\{busy === 'backfill' \|\| !canBackfill\(patterns\.templates\)\}/);
  assert.match(card, /\{note\.signal !== 'pattern' && <button type="button" className="sh-btn" disabled=\{busy === note\.id\} onClick=\{\(\) => setEditing/);          // the words belong to the template
  assert.match(card, /pattern: "a finished day's counters matched an approved template"/);
  assert.match(card, /nothing is written for one you have not approved/);
  assert.match(card, /a note you\s+delete is never written again/);
  assert.ok(HELP_TIPS['record.patterns'].what.includes('nothing is written for one you have not approved'));
});

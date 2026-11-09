// Run with: node --test src/pages/sonicHealthLearned.test.mjs   (from local/)
// The Learned knowledge section of Sonic Health (backend/utils/knowledge_base.py): where it sits in the menu, what its buttons call, and what it tells the admin.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { GROUPS, TABS, groupOf, isTab, labelOf } from './sonicHealthTabs.ts';

const read = path => readFileSync(new URL(path, import.meta.url), 'utf8');
const page = read('./SonicHealthLearned.tsx');
const health = read('./SonicHealth.tsx');
const api = read('../api.ts');

test('Learned knowledge is one section, in the Knowledge group beside the reading shelf', () => {
  assert.ok(isTab('learned'));
  assert.equal(labelOf('learned'), 'Learned knowledge');
  assert.equal(groupOf('learned').id, 'know');
  assert.deepEqual(GROUPS.find(g => g.id === 'know').tabs, ['knowledge', 'shelf', 'learned', 'routines']);
  assert.equal(TABS.filter(t => t.id === 'learned').length, 1);
  assert.match(health, /\{tab === 'learned' && <LearnedTab \/>\}/);
  assert.match(health, /import \{ LearnedTab \} from '\.\/SonicHealthLearned';/);
});

test('the three decisions call the three admin endpoints with POST, the card key encoded, and are on the api object the page uses', () => {
  const start = api.indexOf('export function decideLearnedCard(');
  const body = api.slice(start, api.indexOf('\n}', start));
  assert.match(body, /action: 'approve' \| 'reject' \| 'remove'/);
  assert.match(body, /`\/api\/admin\/knowledge\/\$\{encodeURIComponent\(key\)\}\/\$\{action\}`, 'POST'/);
  assert.match(api, /adminRequest<LearnedKnowledge>\('\/api\/admin\/knowledge', 'GET'\)/);
  assert.match(api, /getLearnedKnowledge,\r?\n\s+decideLearnedCard,/);
});

test('each button asks for exactly its own decision, and a rejection or removal says the page is never proposed again', () => {
  assert.match(page, /onClick=\{\(\) => decide\(card\.key, 'approve'\)\}/);
  assert.match(page, /onClick=\{\(\) => decide\(card\.key, 'reject'\)\}[^]*?never propose this page again/);
  assert.match(page, /onClick=\{\(\) => decide\(card\.key, 'remove'\)\}[^]*?never learn this page again/);
  assert.match(page, /setData\(await api\.decideLearnedCard\(key, action\)\)/);                 // the screen shows what the server says is now true, not a guess
});

test('the admin is told that a card is read by everyone before approving, and the ceiling and expiry are on the screen', () => {
  assert.match(page, /Check it against the page before you approve it: everyone's Sonic will read it\./);
  assert.match(page, /card\.by === 'member' && ' A member asked for this one, so read the page itself, not just the card\.'/);
  assert.match(page, /ceiling of \{data\.max_cards\} cards/);
  assert.match(page, /Articles expire after \{data\.expiry_days\.article\} days and profiles after \{data\.expiry_days\.entity\}/);
  assert.match(page, /never used/);
  assert.match(page, /anyone signed in can say "learn this page"/);                    // anyone may ask; the approval buttons are on the admin-only screen
  assert.match(api, /by\?: 'admin' \| 'member';/);
});

test('pages Sonic read on its own are listed most-read first with Learn and Dismiss, which call the two candidate endpoints and show the server\'s new view', () => {
  const start = api.indexOf('export function decideCandidate(');
  const body = api.slice(start, api.indexOf('\n}', start));
  assert.match(body, /action: 'learn' \| 'dismiss'/);
  assert.match(body, /`\/api\/admin\/knowledge\/candidates\/\$\{encodeURIComponent\(key\)\}\/\$\{action\}`, 'POST'/);
  assert.match(api, /decideLearnedCard,\r?\n\s+decideCandidate,/);
  assert.match(api, /candidates: CandidatePage\[\];/);
  assert.match(page, /onClick=\{\(\) => decideCandidate\(page\.key, 'learn'\)\}/);
  assert.match(page, /onClick=\{\(\) => decideCandidate\(page\.key, 'dismiss'\)\}[^]*?do not suggest it again/);
  assert.match(page, /setData\(await api\.decideCandidate\(key, action\)\)/);
  assert.match(page, /data\.candidates\.map\(candidate\)/);
});

test('a candidate shows only an address, a read count and dates, says nothing was fetched yet, and says the card still needs approval', () => {
  assert.match(page, /read \{page\.seen\} time\{page\.seen === 1 \? '' : 's'\}/);
  assert.match(page, /Nothing has been fetched or written yet\./);
  assert.match(page, /the card that comes out still has to be approved below before anyone's Sonic can read it/);
  assert.match(page, /Could not be learned: \$\{page\.problem\}/);
  assert.doesNotMatch(page, /page\.(?:user|username|by|question)\b/);                           // never who read it or why
});

test('a failed Learn shows the reason and reloads, because a page that was already known is gone from the list', () => {
  const start = page.indexOf('const decideCandidate = async');
  const body = page.slice(start, page.indexOf('};', start));
  assert.match(body, /catch \(err\)[^]*?setNotice\(err instanceof Error \? err\.message[^]*?await load\(\)/);
});

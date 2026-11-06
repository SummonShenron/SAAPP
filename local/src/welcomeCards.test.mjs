// Run with: node --test src/welcomeCards.test.mjs   (from local/; Node 22.18+ runs the TypeScript module directly)
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync } from 'node:fs';
import { GUEST_LANES, accountCardFor, cardsFromQuestions, isPortfolioGuest, portfolioCards } from './welcomeCards.ts';

test('only the shared portfolio guest gets the portfolio cards; the embedded guest and signed-in people do not', () => {
  assert.equal(isPortfolioGuest('guest', false), true);
  assert.equal(isPortfolioGuest('guest', true), false);
  assert.equal(isPortfolioGuest('guest_bty', false), false);
  assert.equal(isPortfolioGuest('jack@example.com', false), false);
  assert.equal(isPortfolioGuest(null, false), false);
});

test('the front lane shows Sonic working: three cards work on the demo project (fix a bug, add a feature, trace a setting) and one reads its own repository', () => {
  assert.deepEqual(GUEST_LANES.map(l => l.id), ['demo', 'builder', 'account']);
  const demo = portfolioCards();                                                             // the front lane is the default
  assert.deepEqual(demo, portfolioCards('demo'));
  assert.deepEqual(demo.map(c => c.label), ['Fix a bug', 'Add a feature', 'Trace a setting', 'Watch it work']);
  assert.deepEqual(demo.map(c => c.demo === true), [true, true, true, false]);              // only the demo cards tell the server to work on the demo project
  const wording = [/overdue a day late.*fix it.*tests pass/i, /late-fee line.*tests pass/i, /rate limit.*which one wins/i, /pull request/i];
  demo.forEach((card, i) => assert.match(card.text, wording[i]));
  assert.ok(demo.slice(0, 3).every(c => /demo project/.test(c.text)), 'each demo card says which project it is about');
  assert.ok(!demo.some(c => /Jack/.test(c.text)), 'the front lane is not about the person');
  assert.ok(![...portfolioCards('builder'), ...portfolioCards('account')].some(c => c.demo), 'no other lane works on the demo project');
});

test('the page tells the server a conversation is about the demo project until another kind of card is chosen or a new chat starts, and the diff card offers sign-up instead of Apply', () => {
  const chat = readFileSync(new URL('./pages/Chat.tsx', import.meta.url), 'utf8');
  assert.match(chat, /const demoProjectRef = useRef\(false\)/);
  assert.match(chat, /demoProjectRef\.current = card\.demo === true;\s*handleSendMessage\(card\.text/);
  assert.equal((chat.match(/demoProjectRef\.current = false;/g) ?? []).length, 2, 'both ways of starting a new conversation reset it');
  assert.match(chat, /true,\s*demoProjectRef\.current\s*\);/);
  assert.match(chat, /demo: payload\.demo === true,/);
  assert.match(chat, /proposal\.demo \? \{ auto: false \} : decideAutoApply/);        // nothing is applied to the demo, whatever the approval level says
  assert.match(chat, /onSignUp=\{\(\) => \{ void api\.logout\(\); \}\}/);
  const api = readFileSync(new URL('./api.ts', import.meta.url), 'utf8');
  assert.match(api, /runnerCards = false,\s*demoProject = false/);
  assert.match(api, /demo_project: demoProject,/);
  const card = readFileSync(new URL('./components/LocalEditCard.tsx', import.meta.url), 'utf8');
  assert.match(card, /\{proposal\.demo && \(/);
  assert.match(card, /Sign up to apply this to your own project/);
  assert.match(card, /\{!proposal\.demo && \(phase === 'pending' \|\| phase === 'applying'\) && \(/);   // Apply and Decline are not shown on the demo
  assert.match(card, /\{!proposal\.demo && phase === 'pending' && declining && \(/);
});

test('the lane about the builder keeps the plain questions about the person and the project, and the technical one last', () => {
  const cards = portfolioCards('builder');
  assert.deepEqual(cards.map(c => c.label), ['About Jack', 'About this project', 'The story', 'Under the hood']);
  assert.match(cards[0].text, /Jack Harper/);
});

test('every card in both lanes has a question and a label, and none is repeated', () => {
  const cards = [...portfolioCards('demo'), ...portfolioCards('builder'), ...portfolioCards('account')];
  for (const card of cards) assert.ok(card.text.trim().endsWith('?') || card.text.trim().endsWith('.'), card.text);
  for (const card of cards) assert.ok(card.label, card.text);
  assert.equal(new Set(cards.map(c => c.text)).size, cards.length);
});

test('the lane about an account shows what a guest cannot do: every card sends nothing, says what an account adds, and no demo card is one of them', () => {
  const cards = portfolioCards('account');
  assert.deepEqual(cards.map(c => c.label), ['Fix a bug', 'Run your checks', 'Be remembered', 'Tidy a folder']);
  for (const card of cards) {
    assert.equal(card.action, 'signup', card.label);
    assert.ok(card.detail && card.detail.length > 40 && card.detail.length < 200, card.label);
  }
  const ordinary = [...portfolioCards('demo'), ...portfolioCards('builder')];
  assert.ok(ordinary.every(c => !c.action), 'the cards a guest can use send a question');
});

test('the page sends nothing for an account card: it shows the detail with a sign-up button, and changing lane clears it', () => {
  const chat = readFileSync(new URL('./pages/Chat.tsx', import.meta.url), 'utf8');
  assert.match(chat, /if \(card\.action === 'signup'\) \{[^}]*setAccountCard\(card\);\s*return;\s*\}/);
  assert.match(chat, /setGuestLane\(lane\.id\); setAccountCard\(null\);/);
  assert.match(chat, /guestLane === 'account' && accountCard && \(\s*<div className="guest-account-detail"/);
  assert.match(chat, /\{accountCard\.detail\}[\s\S]{0,300}Create a free account/);
});

test('a word the server sends in an account_nudge event finds its card, and a word the page does not know finds none', () => {
  for (const key of ['checks', 'fix', 'tidy', 'memory']) {
    const card = accountCardFor(key);
    assert.equal(card.key, key);
    assert.equal(card.action, 'signup');
    assert.ok(card.detail);
  }
  assert.deepEqual(portfolioCards('account').map(c => c.key).sort(), ['checks', 'fix', 'memory', 'tidy']);
  for (const bad of ['', 'merge', null, undefined, 3]) assert.equal(accountCardFor(bad), null);
  accountCardFor('fix').detail = 'changed';
  assert.notEqual(accountCardFor('fix').detail, 'changed');
});

test('the page puts the card under the reply the server named, with a sign-up button, and only on the reply being written', () => {
  const chat = readFileSync(new URL('./pages/Chat.tsx', import.meta.url), 'utf8');
  assert.match(chat, /payload\.event === 'account_nudge'[\s\S]{0,200}accountCardFor\(payload\.feature\)/);
  assert.match(chat, /updated\[lastIndex\] = \{ \.\.\.updated\[lastIndex\], accountNudge: card \};/);
  assert.match(chat, /\{msg\.accountNudge && \([\s\S]{0,400}\{msg\.accountNudge\.detail\}[\s\S]{0,300}Create a free account/);
});

test('the cards are a copy, so a caller cannot change what the next visitor sees', () => {
  portfolioCards()[0].text = 'changed';
  portfolioCards('builder')[0].text = 'changed';
  assert.notEqual(portfolioCards()[0].text, 'changed');
  assert.notEqual(portfolioCards('builder')[0].text, 'changed');
});

test('the page shows the lane tabs only to the shared guest, starts on the front lane, and the guest starts with the steps shown', () => {
  const chat = readFileSync(new URL('./pages/Chat.tsx', import.meta.url), 'utf8');
  assert.match(chat, /useState<GuestLane>\('demo'\)/);
  assert.match(chat, /\{!hasChatted && isPortfolioGuest\(principal, isEmbedded\) && \(\s*<div className="guest-lanes"/);
  assert.match(chat, /isPortfolioGuest\(principal, isEmbedded\) \? portfolioCards\(guestLane\) : currentExampleQuestions/);
  assert.match(chat, /return isGuestPrincipal\(getEffectivePrincipal\(\)\);/);                  // no saved choice: the guest sees the tool calls; a saved choice still wins above it
  assert.match(chat, /const saved = localStorage.getItem\('showTracePanel'\);\s*if \(saved !== null\) return JSON.parse\(saved\);/);
});

test('plain questions become unlabelled cards in the same order', () => {
  assert.deepEqual(cardsFromQuestions(['a?', 'b?']), [{ text: 'a?' }, { text: 'b?' }]);
  assert.deepEqual(cardsFromQuestions([]), []);
});

test('the tidy-up card replaces an ordinary "Try this" card, never the photo card, and is never added twice', async () => {
  const { withTidyCard } = await import('./welcomeCards.ts');
  const photo = { label: 'Try this', text: 'Photograph a letter', action: 'attach', prompt: 'What does this say?' };
  const cards = [{ label: 'From your stuff', text: 'a?' }, photo, { label: 'Try this', text: 'Draft an email?' }, { label: "Sonic's pick", text: 'b?' }];
  const out = withTidyCard(cards);
  assert.equal(out.length, 4);
  assert.deepEqual(out[1], photo);
  assert.equal(out[2].action, 'open');
  assert.equal(out[2].path, '/tidy');
  assert.deepEqual(withTidyCard(out), out);
  assert.deepEqual(cards[2], { label: 'Try this', text: 'Draft an email?' });      // the originals are not changed
});

test('with no ordinary "Try this" card to replace, the tidy-up card is added only if there is room', async () => {
  const { withTidyCard } = await import('./welcomeCards.ts');
  const photoOnly = [{ label: 'Try this', text: 'Photo', action: 'attach' }];
  assert.equal(withTidyCard(photoOnly).length, 2);
  const full = ['a', 'b', 'c', 'd'].map(t => ({ label: 'From your stuff', text: `${t}?` }));
  assert.equal(withTidyCard(full).length, 4);
  assert.ok(!withTidyCard(full).some(c => c.action === 'open'));
});

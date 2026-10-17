// Run with: node --test src/welcomeCards.test.mjs   (from local/; Node 22.18+ runs the TypeScript module directly)
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { cardsFromQuestions, isPortfolioGuest, portfolioCards } from './welcomeCards.ts';

test('only the shared portfolio guest gets the portfolio cards; the embedded guest and signed-in people do not', () => {
  assert.equal(isPortfolioGuest('guest', false), true);
  assert.equal(isPortfolioGuest('guest', true), false);
  assert.equal(isPortfolioGuest('guest_bty', false), false);
  assert.equal(isPortfolioGuest('jack@example.com', false), false);
  assert.equal(isPortfolioGuest(null, false), false);
});

test('the portfolio cards lead with the plain questions about the person and the project, and the technical one is last', () => {
  const cards = portfolioCards();
  assert.deepEqual(cards.map(c => c.label), ['About Jack', 'About this project', 'Watch it work', 'Under the hood']);
  assert.match(cards[0].text, /Jack Harper/);          // on a phone only the first card shows
});

test('every card has a question and a label, and none is repeated', () => {
  const cards = portfolioCards();
  for (const card of cards) assert.ok(card.text.trim().endsWith('?') && card.label, card.text);
  assert.equal(new Set(cards.map(c => c.text)).size, cards.length);
});

test('the portfolio cards are a copy, so a caller cannot change what the next visitor sees', () => {
  portfolioCards()[0].text = 'changed';
  assert.notEqual(portfolioCards()[0].text, 'changed');
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

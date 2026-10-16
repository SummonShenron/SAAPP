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

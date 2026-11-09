// Run with: node --test src/components/choiceCardLogic.test.mjs   (from local/; Node 22.18+ runs the TypeScript module directly)
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { MAX_OTHER_CHARS, answerText, cleanOther, parseChoiceCard } from './choiceCardLogic.ts';

const good = {
  choice_id: 'abc123',
  question: 'Which should it be?',
  options: [
    { label: 'Show it at 80%', description: 'at the notice line', recommended: true },
    { label: 'Only when blocked', description: 'as it is now', recommended: false },
  ],
};

test('a card from the server is read as it is, the recommendation kept and the text trimmed', () => {
  const card = parseChoiceCard({ ...good, question: '  Which should it be?  ' });
  assert.equal(card.choice_id, 'abc123');
  assert.equal(card.question, 'Which should it be?');
  assert.deepEqual(card.options[0], { label: 'Show it at 80%', description: 'at the notice line', recommended: true });
  assert.equal(card.options[1].recommended, false);
  assert.equal(parseChoiceCard({ ...good, options: [{ label: 'A' }, { label: 'B' }] }).options[0].description, '');
});

test('what cannot be drawn is refused rather than half drawn', () => {
  assert.equal(parseChoiceCard(null), null);
  assert.equal(parseChoiceCard('x'), null);
  assert.equal(parseChoiceCard({ ...good, choice_id: '' }), null);
  assert.equal(parseChoiceCard({ ...good, question: '   ' }), null);
  assert.equal(parseChoiceCard({ ...good, options: [{ label: 'only one' }] }), null);
  assert.equal(parseChoiceCard({ ...good, options: [1, 2, 3, 4, 5].map(n => ({ label: `o${n}` })) }), null);
  assert.equal(parseChoiceCard({ ...good, options: [{ label: 'A' }, { label: '  ' }] }), null);
  assert.equal(parseChoiceCard({ ...good, options: [{ label: 'A' }, 'B'] }), null);
});

test('the sent words are tidied and cut to what the server accepts, and nothing is sent for blanks', () => {
  assert.equal(cleanOther('  show   it\n at 80%  '), 'show it at 80%');
  assert.equal(cleanOther('   \n  '), '');
  assert.equal(cleanOther('x'.repeat(MAX_OTHER_CHARS + 50)).length, MAX_OTHER_CHARS);
});

test('an answered card says what was chosen or written', () => {
  const card = parseChoiceCard(good);
  assert.equal(answerText(card, { index: 0 }), 'You chose: Show it at 80%');
  assert.equal(answerText(card, { index: 1 }), 'You chose: Only when blocked');
  assert.equal(answerText(card, { other: 'neither, remove it' }), 'You answered: neither, remove it');
  assert.equal(answerText(card, { index: 9 }), 'You answered.');
});

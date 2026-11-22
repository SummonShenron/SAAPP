// Run with: node --test src/components/changeCardLogic.test.mjs   (from local/; Node 22.18+ runs the TypeScript module directly)
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { outcomeLine, parseChangeCard } from './changeCardLogic.ts';

// The shape the server streams (backend/services/change_cards.py card_payload).
const good = {
  proposal_id: 'abc123',
  action: 'update',
  heading: 'Change an event',
  apply_label: 'Save changes',
  title: 'Dentist',
  rows: [
    { label: 'Now', value: 'Tue Nov 24, 2:00 PM - 3:00 PM' },
    { label: 'New time', value: 'Wed Nov 25, 10:00 AM - 11:00 AM (America/Chicago)' },
  ],
  notice: 'Nobody else is emailed.',
  destructive: false,
};

test('a card from the server is read as it is, with the text trimmed', () => {
  const card = parseChangeCard({ ...good, title: '  Dentist  ', notice: ' Nobody else is emailed. ' });
  assert.equal(card.proposal_id, 'abc123');
  assert.equal(card.action, 'update');
  assert.equal(card.title, 'Dentist');
  assert.equal(card.notice, 'Nobody else is emailed.');
  assert.deepEqual(card.rows[0], { label: 'Now', value: 'Tue Nov 24, 2:00 PM - 3:00 PM' });
  assert.equal(card.destructive, false);
});

test('what cannot be drawn is refused rather than half drawn', () => {
  assert.equal(parseChangeCard(null), null);
  assert.equal(parseChangeCard('x'), null);
  assert.equal(parseChangeCard({ ...good, proposal_id: '' }), null);
  assert.equal(parseChangeCard({ ...good, title: '   ' }), null);
  assert.equal(parseChangeCard({ ...good, action: '' }), null);
  assert.equal(parseChangeCard({ ...good, action: undefined }), null);
  assert.equal(parseChangeCard({ ...good, action: 'Drop Table;' }), null);
});

test('the heading and the button say what the server wrote, for any kind of change', () => {
  const card = parseChangeCard({ ...good, action: 'append', heading: '  Add to a document ', apply_label: ' Add text ' });
  assert.equal(card.action, 'append');
  assert.equal(card.heading, 'Add to a document');
  assert.equal(card.applyLabel, 'Add text');
  assert.equal(parseChangeCard({ ...good, action: 'insert_after_heading' }).action, 'insert_after_heading');
});

test('a card from a server that sent no words still has a heading and a button', () => {
  const card = parseChangeCard({ ...good, heading: undefined, apply_label: '  ' });
  assert.equal(card.heading, 'Change');
  assert.equal(card.applyLabel, 'Apply');
});

test('rows without a label or a value are dropped, and a card with no rows is still a card', () => {
  const card = parseChangeCard({ ...good, rows: [{ label: 'When', value: '' }, { label: '', value: 'x' }, 'nope', null, { label: 'Where', value: 'Room 4' }] });
  assert.deepEqual(card.rows, [{ label: 'Where', value: 'Room 4' }]);
  assert.deepEqual(parseChangeCard({ ...good, rows: undefined }).rows, []);
  assert.deepEqual(parseChangeCard({ ...good, rows: 'many' }).rows, []);
});

test('a delete is always drawn as the careful one, even if the server forgot to say so', () => {
  assert.equal(parseChangeCard({ ...good, action: 'delete', destructive: undefined }).destructive, true);
  assert.equal(parseChangeCard({ ...good, action: 'replace', destructive: true }).destructive, true);
  assert.equal(parseChangeCard({ ...good, action: 'create', destructive: true }).destructive, true);
  assert.equal(parseChangeCard({ ...good, action: 'create', destructive: 'yes' }).destructive, false);
});

test('every answer to a press has a sentence, and a failure says nothing was changed', () => {
  assert.equal(outcomeLine('applied', 'Deleted "Dentist" from your calendar.'), 'Done. Deleted "Dentist" from your calendar.');
  assert.equal(outcomeLine('applied', ''), 'Done.');
  assert.equal(outcomeLine('declined', ''), 'Declined. Nothing was changed.');
  assert.equal(outcomeLine('failed', 'That event is no longer on your calendar.'), 'Nothing was changed. That event is no longer on your calendar.');
  assert.equal(outcomeLine('failed', ''), 'Nothing was changed.');
  assert.match(outcomeLine('expired', ''), /no longer waiting.*nothing was changed/);
  assert.equal(outcomeLine('invalid', ''), 'That could not be sent.');
});

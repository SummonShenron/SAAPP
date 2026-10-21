// Run with: node --test src/pages/usersDayLayout.test.mjs   (from local/; Node 22.18+ runs the TypeScript module directly)
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { BAR_LIMIT, barFraction, barItems, dayHeadline, money, percent, shortDay, shortName, tableRows } from './usersDayLayout.ts';

function person(user, cost, extra = {}) {
  return { user, you: false, cost, tokens: 1000, calls: 3, turns: 2, share: Math.round((cost / 1) * 10000) / 10000, ...extra };
}

const day = {
  day: '2026-10-20',
  total: 1.0,
  people: [person('jack', 0.57, { you: true }), person('sam', 0.2), person('lee', 0.05)],
  more_people: 0,
  evals: { cost: 0.1, tokens: 500, calls: 4, share: 0.1 },
  background: { cost: 0.08, share: 0.08 },
};

test('the bars are the accounts biggest first, then the evals and the background jobs, with you named "You"', () => {
  const items = barItems(day);
  assert.deepEqual(items.map(i => i.kind), ['you', 'person', 'person', 'evals', 'background']);
  assert.deepEqual(items.map(i => i.label), ['You', 'sam', 'lee', 'Testing and evals', 'Background jobs']);
  assert.match(items[0].title, /jack \(you\) · \$0\.57 · 57% of the day/);
});

test('only the biggest ten are named and the rest are added up, never dropped', () => {
  const many = { ...day, people: Array.from({ length: 14 }, (_, n) => person(`u${String(n).padStart(2, '0')}`, 0.1 - n * 0.005)), evals: null, background: null, total: 1.0 };
  const items = barItems(many);
  assert.equal(items.filter(i => i.kind === 'person').length, BAR_LIMIT);
  const others = items.find(i => i.kind === 'others');
  assert.equal(others.label, '4 accounts more');
  assert.equal(others.cost, Math.round(many.people.slice(BAR_LIMIT).reduce((s, p) => s + p.cost, 0) * 10000) / 10000);
});

test('you are always shown, even when you are not among the biggest ten', () => {
  const people = Array.from({ length: 12 }, (_, n) => person(`u${String(n).padStart(2, '0')}`, 0.2 - n * 0.01));
  people[11] = { ...people[11], you: true };
  const items = barItems({ ...day, people, evals: null, background: null });
  assert.equal(items.filter(i => i.kind === 'person' || i.kind === 'you').length, BAR_LIMIT);
  assert.ok(items.some(i => i.kind === 'you'));
  assert.equal(items.find(i => i.kind === 'others').label, '2 accounts more');
});

test('accounts beyond the server cap are counted in the "more" bar even when no cost is known for them', () => {
  const items = barItems({ ...day, people: [person('jack', 0.5, { you: true })], more_people: 7, evals: null, background: null });
  const others = items.find(i => i.kind === 'others');
  assert.equal(others.label, '7 accounts more');
  assert.equal(others.share, null);
});

test('a bar is its share of the whole day, never more than the day, and zero stays zero', () => {
  const items = barItems(day);
  assert.equal(barFraction(items[0], day), 0.57);
  assert.equal(barFraction({ ...items[0], cost: 5 }, day), 1);
  assert.equal(barFraction({ ...items[0], cost: 0 }, day), 0);
  assert.equal(barFraction(items[0], { ...day, total: 0 }), 0);
});

test('the headline gives the day, the total, the number of accounts and your share', () => {
  assert.equal(dayHeadline(day, 'jack', 'Today'), 'Today: $1.00 in all across 3 accounts. You used $0.57 (57% of the day).');
  assert.equal(dayHeadline({ ...day, people: day.people.slice(1) }, 'jack', 'Oct 19'), 'Oct 19: $1.00 in all across 2 accounts. You did not use Sonic that day.');
  assert.equal(dayHeadline({ ...day, people: day.people.slice(1) }, null, 'Oct 19'), 'Oct 19: $1.00 in all across 2 accounts.');
  assert.equal(dayHeadline({ ...day, total: 0, people: [] }, 'jack', 'Oct 18'), 'Oct 18: nothing was spent.');
});

test('the table lists every account then the evals and the background jobs; the evals and background have no turns', () => {
  const rows = tableRows(day);
  assert.deepEqual(rows.map(r => r.account), ['jack', 'sam', 'lee', 'Testing and evals', 'Background jobs and anything not tied to an account']);
  assert.equal(rows[0].you, true);
  assert.deepEqual(rows.map(r => r.isAccount), [true, true, true, false, false]);      // words, not account ids: shown whole, never shortened
  assert.equal(rows[3].turns, null);
  assert.equal(rows[4].calls, null);
});

test('money, percent and names read plainly', () => {
  assert.equal(money(0.57), '$0.57');
  assert.equal(money(0.004), '$0.004');
  assert.equal(money(0), '$0.00');
  assert.equal(money(NaN), '$0.00');
  assert.equal(percent(0.5712), '57%');
  assert.equal(percent(null), '');
  assert.equal(shortName('sam'), 'sam');
  assert.match(shortName('user_2abcdefghijklmnopqrstuvwxyz'), /^user_2abcdef….{8}$/);
  assert.equal(shortDay('2026-10-20'), 'Oct 20');
});

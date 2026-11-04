// Run with: node --test src/components/billingCardLogic.test.mjs   (from local/; Node 22.18+ runs the TypeScript module directly)
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { FINE_PRINT, SUPPORT_EMAIL, balanceLine, money, parseOwnLimit, parseTopUp, quoteLine, rowLine, topUpBlocked, usageLine } from './billingCardLogic.ts';

const view = (over = {}) => ({
  enabled: true, balance_usd: 9.5, paid: true, daily_ceiling_usd: 5, own_ceiling_usd: null, spent_today_usd: 0.5, min_topup_usd: 5, fee_percent: 2.9, fee_fixed_usd: 0.3,
  frozen: false, topups_paused: false, can_top_up: true, recent: [], ...over,
});

test('money is always two places and never NaN', () => {
  assert.equal(money(10.61), '$10.61');
  assert.equal(money(5), '$5.00');
  assert.equal(money(0.005), '$0.01');
  assert.equal(money(Number.NaN), '$0.00');
});

test('a top-up is a whole number of cents, at least the minimum, and the person is told what is wrong', () => {
  assert.deepEqual(parseTopUp('10', 5), { amount: 10 });
  assert.deepEqual(parseTopUp(' $12.50 ', 5), { amount: 12.5 });
  assert.deepEqual(parseTopUp('5', 5), { amount: 5 });
  assert.match(parseTopUp('4.99', 5).problem, /smallest top-up is \$5\.00 of credit/);
  for (const bad of ['', 'lots', '-3', '1.234', '1e3', '$', '10,5']) assert.match(parseTopUp(bad, 5).problem, /Enter an amount in dollars/, bad);
  assert.match(parseTopUp('5000', 5).problem, /smaller amount/);
});

test('the processing line says what the card is charged and how it splits', () => {
  assert.equal(quoteLine({ credit_usd: 10, fee_usd: 0.61, total_usd: 10.61 }), 'You pay $10.61: $10.00 of credit and a $0.61 processing fee.');
});

test('the balance and the day\'s use read plainly, and a limit the person set is said to be theirs', () => {
  assert.equal(balanceLine(view()), '$9.50 of credit left.');
  assert.equal(usageLine(view()), 'Used today: $0.50 of your $5.00 daily limit.');
  assert.equal(usageLine(view({ own_ceiling_usd: 3, daily_ceiling_usd: 3 })), 'Used today: $0.50 of your $3.00 daily limit (the lower one you set).');
});

test('a top-up is refused for a frozen account, while top-ups are paused, and while payments are not switched on, each with its own words', () => {
  assert.equal(topUpBlocked(view()), null);
  assert.match(topUpBlocked(view({ frozen: true })), /paused on this account/);
  assert.match(topUpBlocked(view({ topups_paused: true })), /paused for today/);
  assert.match(topUpBlocked(view({ can_top_up: false })), /not switched on yet/);
  assert.match(topUpBlocked(view({ frozen: true, topups_paused: true })), /this account/);                    // the account's own problem is said first
});

test('a person may set a limit of their own only below the server\'s, and empty clears it', () => {
  assert.deepEqual(parseOwnLimit('', 5), { amount: null });
  assert.deepEqual(parseOwnLimit('3', 5), { amount: 3 });
  assert.deepEqual(parseOwnLimit('5', 5), { amount: 5 });
  assert.match(parseOwnLimit('6', 5).problem, /can not be set above \$5\.00/);
  for (const bad of ['0', '-1', 'lots', '1.234']) assert.match(parseOwnLimit(bad, 5).problem, /above zero/, bad);
});

test('recent rows read as what happened, with a sign and the day', () => {
  assert.equal(rowLine({ kind: 'topup', usd: 10, at: null, day: '2026-11-05' }), 'Added credit +$10.00 (2026-11-05)');
  assert.equal(rowLine({ kind: 'spend', usd: -0.42, at: null, day: null }), 'Used -$0.42');
  assert.equal(rowLine({ kind: 'refund', usd: -2.5, at: null, day: '2026-11-06' }), 'Refund -$2.50 (2026-11-06)');
  assert.equal(rowLine({ kind: 'mystery', usd: 1, at: null, day: null }), 'Entry +$1.00');
});

test('the fine print states the refund policy Jack chose and does not promise more', () => {
  const text = FINE_PRINT.join(' ');
  assert.match(text, /one dollar of credit is one dollar of use/);
  assert.match(text, /processing fee is shown separately and is not refundable/);
  assert.ok(text.includes(`Unused credit can be refunded on request: email ${SUPPORT_EMAIL}`) && /Credit you have used is not refundable/.test(text));
  assert.match(text, /credit is untouched/);
});

test('the card shows only where billing is on, takes the quote from the server, and sends the person to the provider\'s page', () => {
  const card = readFileSync(new URL('./BillingCard.tsx', import.meta.url), 'utf8');
  assert.match(card, /if \(!view\) return null;/);                                                         // 404 from the server: nothing at all
  assert.match(card, /api\.getBillingQuote\(typed\)/);
  assert.match(card, /latest\.current === ticket/);                                                        // an answer for an amount since changed is ignored
  assert.match(card, /window\.location\.href = started\.url;/);
  assert.ok(!/card number|cardnumber|cvc/i.test(card));                                                    // no card is ever typed here
  const api = readFileSync(new URL('../api.ts', import.meta.url), 'utf8');
  assert.match(api, /if \(res\.status === 404 \|\| res\.status === 403\) return null;/);
  for (const path of ['/api/billing/me', '/api/billing/quote', '/api/billing/checkout', '/api/billing/ceiling']) assert.ok(api.includes(path), path);
  const account = readFileSync(new URL('../pages/Account.tsx', import.meta.url), 'utf8');
  assert.match(account, /\{!guest && <BillingCard \/>\}/);
});

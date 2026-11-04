// The words and the arithmetic of the Billing card (components/BillingCard.tsx), kept apart from React so they can be tested. Everything is in dollars, as the server sends it.
// The card only exists where the server has billing switched on (GET /api/billing/me answers 404 otherwise). Nothing here promises anything the server does not do.

import type { BillingQuote, BillingRow, BillingView } from '../api';

export const SUPPORT_EMAIL = 'jackharper0517@outlook.com';

/** "$10.61", always two places. */
export function money(usd: number): string {
  return `$${(Math.round((Number.isFinite(usd) ? usd : 0) * 100) / 100).toFixed(2)}`;
}

/** What a person typed as a top-up: the amount in dollars, or why it can not be used. A whole number of cents; at least the minimum. */
export function parseTopUp(text: string, minUsd: number): { amount: number } | { problem: string } {
  const cleaned = text.trim().replace(/^\$/, '');
  if (!/^\d+(\.\d{1,2})?$/.test(cleaned)) return { problem: 'Enter an amount in dollars, like 10 or 12.50.' };
  const amount = Math.round(Number(cleaned) * 100) / 100;
  if (amount < minUsd) return { problem: `The smallest top-up is ${money(minUsd)} of credit.` };
  if (amount > 1000) return { problem: 'Enter a smaller amount.' };
  return { amount };
}

/** The processing line, said plainly: what the card is charged and how it splits. */
export function quoteLine(quote: BillingQuote): string {
  return `You pay ${money(quote.total_usd)}: ${money(quote.credit_usd)} of credit and a ${money(quote.fee_usd)} processing fee.`;
}

/** The balance and today's use in one sentence each. */
export function balanceLine(view: BillingView): string {
  return `${money(view.balance_usd)} of credit left.`;
}

export function usageLine(view: BillingView): string {
  return `Used today: ${money(view.spent_today_usd)} of your ${money(view.daily_ceiling_usd)} daily limit${view.own_ceiling_usd !== null ? ' (the lower one you set)' : ''}.`;
}

/** Why a top-up can not be started now, or null when it can. */
export function topUpBlocked(view: BillingView): string | null {
  if (view.frozen) return 'Top-ups are paused on this account. Please contact us.';
  if (view.topups_paused) return 'New top-ups are paused for today. Please try again tomorrow.';
  if (!view.can_top_up) return 'Payments are not switched on yet.';
  return null;
}

const KIND_LABEL: Record<string, string> = { topup: 'Added credit', spend: 'Used', refund: 'Refund', adjust: 'Adjustment' };

/** One recent row: "Added credit +$10.00" or "Used -$0.42", with its day. */
export function rowLine(row: BillingRow): string {
  const sign = row.usd >= 0 ? '+' : '-';
  return `${KIND_LABEL[row.kind] ?? 'Entry'} ${sign}${money(Math.abs(row.usd))}${row.day ? ` (${row.day})` : ''}`;
}

/** What a person types as their own daily limit: dollars, lower than the server's; empty clears it. */
export function parseOwnLimit(text: string, serverUsd: number): { amount: number | null } | { problem: string } {
  const cleaned = text.trim().replace(/^\$/, '');
  if (cleaned === '') return { amount: null };
  if (!/^\d+(\.\d{1,2})?$/.test(cleaned) || Number(cleaned) <= 0) return { problem: 'Enter an amount above zero, or leave it empty to clear your limit.' };
  const amount = Math.round(Number(cleaned) * 100) / 100;
  if (amount > serverUsd) return { problem: `Your daily limit can not be set above ${money(serverUsd)}. Ask us if you need more.` };
  return { amount };
}

/** The fine print under the card: what is charged, what is refundable, and who to ask. */
export const FINE_PRINT = [
  'Credit is used at what the models cost: one dollar of credit is one dollar of use. The processing fee is shown separately and is not refundable.',
  `Unused credit can be refunded on request: email ${SUPPORT_EMAIL}. Credit you have used is not refundable.`,
  'Your daily limit is a safety limit on paid use. When you reach it Sonic pauses until the next day, and your credit is untouched.',
];

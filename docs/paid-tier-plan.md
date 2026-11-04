# Paid tier (pay as you go), built switched off

Written 2026-11-05 from Jack's request. Status: PLAN. Nothing here is built. It is a safety valve: if traffic outgrows what Jack will pay for (a $5 a day hard cap overall, $1 a day per person), people who want more can pay for their own use. It is not a revenue plan.

## What Jack asked for
- An env var flag (`BILLING_ENABLED`, off) that turns billing on.
- Pay as you go at a flat 1:1 rate: a person pays what their own use costs, no profit.
- Not on for the full release. The warning messages change to say a paid option is planned, which bypasses the global limits and the daily and five-hour caps.
- Jack would have to set up an LLC first; the infrastructure should exist before that so it is ready if traffic arrives early.

## Principles
1. **Off means exactly as today.** With the flag off there is no new endpoint, no new screen and no change in what the guard decides. A test pins this.
2. **Prepaid, never owed.** A person buys credit first; spend draws it down; at zero they are back under the free limits. Nobody ever owes Jack money, so there is nothing to collect and a person's balance is their own hard ceiling.
3. **Nobody's paid use may starve anybody else, and nobody's free use may spend paid credit.** Paid spend is kept out of the global figure (below); credit is only drawn by the owner's own turns.
4. **Say only what is true.** Until it exists the warnings say "planned", with no date. They never promise it.
5. **No text in the money store.** The ledger holds amounts, ids and times. No prompt, reply or file name (the same rule as `user_usage.py`).

## What already exists and is reused
- `backend/utils/user_usage.py`: one row per person per minute and per billing day with `cost_micro` (millionths of a dollar, priced per call from `LLM_PRICES`). This is the metered cost a charge is based on.
- `backend/utils/budget_guard.py`: per-person limits (`LLM_USER_BUDGET_DAILY_USD`, `LLM_USER_BUDGET_5H_USD`), levels ok / notice / throttled / blocked, a per-person raise when an admin approves a request (`request_increase`, `decide_request`, `budget_user_extensions`), and the messages (`blocked_reply`, `public_view`).
- Stripe is NOT in the codebase yet.

## Design

### 1. A credit ledger (new, `backend/utils/credits.py`)
Collection `billing_ledger`, one row per event, append-only: `{id, user, kind: "topup" | "spend" | "refund" | "adjust", micro: int (signed), at, ref}`. `ref` is the Stripe event id for a top-up, the turn id for a spend, so a repeated webhook or a retried flush cannot count twice (unique index on `(kind, ref)`). The balance is the sum of the person's rows (cached per person for a few seconds). Amounts are integers in millionths of a dollar, as in `cost_micro`.

### 2. The charge is the metered cost
At the end of a turn the person's `cost_micro` for that turn is debited as a `spend` row, times `BILLING_RATE` (default 1.0, so 1:1). Only turns made while paid mode was in force for that person are debited (see 3). A turn that is not counted because the process died is simply not charged; that errs in the person's favour, and it is said so in the terms.

### 3. The guard hook (the only change to `budget_guard`)
`current_state(username)` gains one input: `paid` (the flag is on AND the person's balance is above zero). When it is true the PERSON'S limits (daily and five-hour) are not applied to them, and neither is the global block level: they get full power. When the balance reaches zero the next turn is judged by the free limits again, and the reply says why. A turn already running is allowed to finish; the overshoot is bounded by the per-turn step ceiling, and is debited in full (the balance may go slightly negative, and the next top-up covers it).
`public_view` adds `paid: true/false` and the balance to the person's own view only.

### 3b. Limits on paid people too (asked 2026-11-05: "do we need a budget limit for paid users?")
Yes, a different kind from the free caps. The prepaid balance already stops anyone spending more than they bought, so there is no budget to protect Jack from an overdraft. These limits exist for what a balance does not cover:
- **A paid daily ceiling per person, kept LOW** (`BILLING_DAILY_CEILING_USD`; Jack, 2026-11-05: "it should remain low to prevent the chargeback issue"). It limits the damage from a chargeback (someone buys credit with a stolen card, spends it, and the card owner disputes it: Jack still owes the model providers, plus the dispute fee), a runaway (a bug or a stuck loop burning a balance) and a stolen funded account. The ceiling is the most a chargeback can cost per account per day, so it stays low by design, not as a launch setting to loosen later. It is judged on the person's own paid spend for the billing day, from the same `cost_micro` rows as everything else.
- **Raised only by Jack, per person, on request.** Nobody's ceiling rises by itself or by paying more. A person who needs more asks, and an admin approves a higher ceiling for them, using the same request and approval flow the free limits already have (`request_increase`, `decide_request`), after Jack has looked at their payment history. Approvals are saved with who and when.
- **Lower still for a new paid account** (`BILLING_NEW_ACCOUNT_CEILING_USD` for the first `BILLING_NEW_ACCOUNT_DAYS`), since a stolen-card buyer spends everything at once. It lifts to the ordinary low ceiling by itself, never above it.
- **A ceiling the person can set lower themselves** (never higher than the server's), so a balance cannot be drained in a day by accident.
- **A global paid-spend ceiling and an alert** (`BILLING_GLOBAL_PAID_CEILING_USD` a day). Paying bypasses Jack's $5 cap but NOT the providers' own: Google's monthly cap is a cliff and Anthropic has rate limits, so one heavy paid person could take the service down for the free ones. At the ceiling new top-ups pause (a person with a balance keeps what they bought, but new purchases wait) and an admin is told; below it, nothing changes.
- **What stays as it is:** the per-turn limits (the 30-step ceiling, the step budgets) apply to paid turns exactly as to free ones.
When a paid ceiling is reached the reply says which one and when it resets, and that the balance is untouched. A paid ceiling is a pause, never a charge.

### 4. Keeping paid spend out of the global figure (the hard part)
`llm_usage` (what the global caps are judged against) deliberately keeps no user, so it cannot say whose dollars a row is. Plan: at the end of a paid turn, also write the turn's tokens and dollars to a small `llm_usage_paid_minutely` collection (same shape, no user), and have `budget_guard.spent()` subtract it from the global day, month and shared five-hour totals. Free users are then judged on free spend only. Google's own caps are provider-side and still apply to everyone: this plan cannot lift them.
Test: a paid turn changes the global figure by zero and the person's own figures by its cost.

### 5. Stripe, behind an interface
`backend/services/payments.py` defines `PaymentProvider` (`create_checkout(user, amount_micro) -> url`, `parse_webhook(body, signature) -> event`); the Stripe class is the only file that imports Stripe. Everything else is tested with a fake provider. Checkout (hosted page): the person never enters a card in SAAPP. Endpoints (all 404 with the flag off): `POST /api/billing/checkout`, `POST /api/billing/webhook` (verifies the signature, ignores everything but a completed checkout, writes one `topup` row by event id), `GET /api/billing/me` (balance, last ten ledger rows, whether paid mode is on).

### 6. The flag
`BILLING_ENABLED` (default false). Also `BILLING_RATE` (default 1.0), `BILLING_MIN_TOPUP_USD` (default 5), `BILLING_DAILY_CEILING_USD`, `BILLING_NEW_ACCOUNT_CEILING_USD`, `BILLING_NEW_ACCOUNT_DAYS`, `BILLING_GLOBAL_PAID_CEILING_USD` (3b), `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`. With the flag on but no Stripe keys, the endpoints answer "not set up" and paid mode never starts. The frontend learns the state from the existing settings bundle, never from a build-time constant.

### 7. What a person sees
- Flag off (the release): only the warning copy changes. Where a limit is reached it says a paid option is planned, no date. Nothing to click.
- Flag on: a Billing card (balance, a top-up button at or above the minimum, recent charges, a plain statement that charges equal the model costs), and the limit messages offer the top-up. The model presets say what a task has cost, not what it will.

## The 1:1 rate and what it does not cover
- **Card fees.** Stripe's fee (about 3% plus 30 cents, to be checked at set-up) comes out of each payment, so a flat 1:1 loses money on every top-up, most on small ones. Decide: a minimum top-up (the plan assumes $5) and either absorb about 3% or show a visible processing line.
- **Metering gaps.** `LLM_PRICES` has no cache-write surcharge, so the metered cost is a little below the real one. Fix the price table before billing is on, or accept the gap.
- **Price changes.** Google's prices double on 2027-01-01 (Flash, then Argon on 2026-12-01). The price table must be current or 1:1 drifts. Add a test that every model a paid turn can use has a price (a paid turn on an unpriced model would be free).
- **Refunds, chargebacks, disputes.** A refund writes a `refund` row; a chargeback must freeze the balance. Rules to decide.

## Outside the code (Jack, with an accountant or lawyer, before the flag is turned on)
Whether to form the LLC before taking any money; sales tax or VAT on what is sold; how prepaid balances and refunds are treated where the users are; terms of service and the privacy page's wording about payments. These are not code questions and nothing here answers them.

## Status (2026-11-05, uncommitted): steps 1 to 5 built, switched off
**Built and tested (no Stripe needed):** `backend/utils/credits.py` (the ledger, balance, queued charges saved with the usage counts, the low ceilings, admin and own ceilings, the global paid ceiling, the person's own view); `backend/services/payments.py` (the provider interface, a fake provider with real HMAC signatures that the environment can never select, checkout and webhook handling, idempotent by payment id); the guard hook (`current_state(..., paid_bypass=True)`, passed only by the chat turn and only while billing is on); the `paid:` label that keeps paid calls out of the shared budget the way eval calls are; endpoints `GET /api/billing/me`, `POST /api/billing/checkout`, `POST /api/billing/webhook`, `PUT /api/billing/ceiling`, `PUT /api/admin/billing/ceiling` (all 404 with the flag off). 32 tests, two of them mutation-checked (a paid turn staying out of the shared budget; a paid person pausing at their ceiling). With `BILLING_ENABLED` off the chat turn does not even read a balance.
**Decided 2026-11-05 (Jack) and built:** the card fee is SHOWN as a processing line (the person picks an amount of credit; the card is charged the credit plus the fee, `credits.quote`; defaults 2.9% + $0.30 via `BILLING_FEE_PERCENT` / `BILLING_FEE_FIXED_USD`, to be checked against the processor's real price; the fee is not refundable). Refund policy: used credit is not refundable; unused credit is refunded on request by email (`POST /api/admin/billing/refund` records it against the balance, never more than the balance; the card itself is refunded in the processor's dashboard). A chargeback (`payment.disputed`) freezes the account, zeroes what is left of its credit and blocks top-ups and the paid bypass, even for a payment that completes later; an admin lifts it (`POST /api/admin/billing/unfreeze`). The Billing card (Account page, "Paid use") shows balance, today's use, a top-up with the fee line before paying, the person's own lower daily limit, recent rows and the fine print; it renders nothing unless the server has billing on. The minimum top-up stays $5 of credit.
**Not built:** the Stripe class and a real test-mode checkout (needs the account; the real dispute and webhook shapes must be matched then); the request-and-approve flow for a higher ceiling (an admin sets one directly, a person asks by email); an admin alert at the global paid ceiling (it pauses top-ups, nothing is sent); the allowlist for the dry run (step 7); a screen for an admin to refund or unfreeze (the two routes exist, nothing calls them).
**Known gaps in what is built:** (1) model calls made AFTER a turn finishes (some summarising and memory work) are not in the paid turn, so Jack pays for them; small, unmeasured. (2) A paid person's scheduled tasks stay on the free limits by design. (3) Charges are queued in memory and saved with the usage counts, so a crash loses up to a minute of them, in the person's favour. (4) A paid turn on a model with no price is not charged: it logs a warning, and nothing yet checks every model against `LLM_PRICES`. (5) The usage page now shows `paid:`-labelled rows as separate lines; not looked at on the page itself.

## Build order
1. **Warning copy** (DONE 2026-11-05: `budget_guard.PAID_OPTION_NOTE`, the blocked banner, the Help page; flag off, no code beyond text): the limit and blocked messages mention a planned paid option. Pure text, can ship with the release. Test: no message promises a date or says it exists.
2. **Ledger and balance** (`credits.py`) with tests: append-only, idempotent by `ref`, balance, refund, no text stored.
3. **Charge at the end of a turn**, only when paid mode is in force; flag-off test shows nothing is written.
4. **Guard hook, paid-spend subtraction and the paid ceilings (3b)**, with the two tests in 3 and 4 above and these: a paid person at their daily ceiling is paused with their balance intact; a new account's lower ceiling applies and then lifts; a person's own lower ceiling is honoured and a higher one is refused; nothing raises a ceiling but an admin's approval, and one approval raises that person's ceiling only; at the global paid ceiling top-ups pause and an admin is told, and a funded person already running is not cut off mid-turn.
5. **Payment interface, fake provider, endpoints** (404 when off, bad signature rejected, repeated webhook counted once).
6. **Stripe class and the Billing card.** Needs Jack's Stripe account; test mode first, then a real five-dollar top-up by hand.
7. **Dry run** with the flag on for Jack's own account only (an allowlist env var), before anyone else.

Steps 1 to 5 can be built and tested now with no account. Only step 6 needs one.

## Decisions for Jack (not settled; the plan assumes the first of each)
1. (Decided: show the processing line; minimum top-up $5 of credit.)
2. The paid daily ceiling and the new-account ceiling: low, as decided. The plan assumes $5 a day, and $2 a day for the first 7 days; guesses to be checked against what a heavy honest day costs (a large Sonnet task has cost over $1, so $5 is a handful of big Max-style tasks, and a person who needs more asks) and against what one chargeback would cost Jack.
3. The global paid ceiling: set against the provider limits that actually apply (Anthropic's tier and Google's cap), which have not been looked up for this plan.
4. (Decided: unused credit refundable on request, used credit and the fee not; a dispute freezes the account.) Still for a lawyer: whether this wording is enough where the users are.

## Risks and what would make us stop
- A chargeback after the credit was spent: the new-account ceiling, the daily ceiling and Stripe's own fraud tools are the defences; nothing removes the risk, so the first weeks run with small ceilings and Jack reading the ledger.
- Someone gets paid turns for free (an unpriced model, a webhook counted twice, a refund that is not a debit): the ledger tests and the unpriced-model test exist for these.
- Paid usage still trips the global block: the subtraction in 4 is the whole point and needs a test against the real `spent()`.
- A person is charged for a turn that failed: debit only turns that produced a priced call; decide whether a failed turn is charged.
- Legal or tax questions are unanswered: the flag stays off. That is the gate, not code.

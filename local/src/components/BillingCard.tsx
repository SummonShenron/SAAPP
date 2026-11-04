import { useCallback, useEffect, useRef, useState } from 'react';
import { api, type BillingQuote, type BillingView } from '../api';
import { FINE_PRINT, balanceLine, money, parseOwnLimit, parseTopUp, quoteLine, rowLine, topUpBlocked, usageLine } from './billingCardLogic';
import './__styles__/BillingCard.css';

const QUOTE_DELAY_MS = 400;

/**
 * Settings, "Paid use" (backend/utils/credits.py). Shown ONLY where the server has billing switched on: GET /api/billing/me answers 404 otherwise, and then this renders nothing at all. A person adds credit
 * through the payment provider's own page (they never type a card here), sees the processing fee as its own line before they pay, sets a daily limit of their own that is lower than the server's, and
 * sees their balance, today's use and recent rows. The words are in components/billingCardLogic.ts.
 */
export default function BillingCard() {
  const [view, setView] = useState<BillingView | null>(null);
  const [amountText, setAmountText] = useState('');
  const [quote, setQuote] = useState<BillingQuote | null>(null);
  const [limitText, setLimitText] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const latest = useRef(0);

  const load = useCallback(async () => {
    try {
      const next = await api.getBilling();
      setView(next);
      if (next) setAmountText(current => current || String(next.min_topup_usd));
      if (next) setLimitText(next.own_ceiling_usd !== null ? String(next.own_ceiling_usd) : '');
    } catch {
      setView(null);          // a failed read hides the card rather than showing a broken one
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const parsed = view ? parseTopUp(amountText, view.min_topup_usd) : null;
  const typed = parsed && 'amount' in parsed ? parsed.amount : null;

  // The processing line is asked for from the server (it knows the fee), a moment after the person stops typing, and an answer for an amount they have since changed is ignored.
  useEffect(() => {
    setQuote(null);
    if (typed === null) return;
    const ticket = ++latest.current;
    const timer = window.setTimeout(() => {
      api.getBillingQuote(typed).then(next => { if (latest.current === ticket) setQuote(next); }).catch(() => { if (latest.current === ticket) setQuote(null); });
    }, QUOTE_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [typed]);

  if (!view) return null;
  const blocked = topUpBlocked(view);

  const pay = async () => {
    if (typed === null || busy || blocked) return;
    setBusy(true);
    setError('');
    try {
      const started = await api.startBillingCheckout(typed);
      window.location.href = started.url;
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : 'Could not start the payment.');
      setBusy(false);
    }
  };

  const saveLimit = async () => {
    const result = parseOwnLimit(limitText, view.daily_ceiling_usd);
    if ('problem' in result) { setError(result.problem); setNotice(''); return; }
    setBusy(true);
    setError('');
    try {
      const saved = await api.setBillingCeiling(result.amount);
      setNotice(saved.message);
      await load();
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : 'Could not save that limit.');
      setNotice('');
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="account-card billing-card" aria-label="Paid use" data-help="billing.card">
      <h3>Paid use</h3>
      <p className="billing-balance"><strong>{balanceLine(view)}</strong> {usageLine(view)}</p>

      <div className="billing-block" data-help="billing.add">
        <div className="account-label">Add credit</div>
        <div className="billing-add">
          <input className="account-input" type="text" inputMode="decimal" aria-label="Amount of credit to add, in dollars" value={amountText} disabled={busy || blocked !== null}
            onChange={event => setAmountText(event.target.value)} onKeyDown={event => { if (event.key === 'Enter') void pay(); }} />
          <button type="button" className="account-btn" disabled={busy || blocked !== null || typed === null} onClick={() => void pay()}>{busy ? 'Opening...' : 'Continue to payment'}</button>
        </div>
        {blocked && <p className="account-note">{blocked}</p>}
        {!blocked && parsed && 'problem' in parsed && amountText.trim() !== '' && <p className="account-note">{parsed.problem}</p>}
        {!blocked && quote && <p className="billing-quote" aria-live="polite">{quoteLine(quote)}</p>}
      </div>

      <div className="billing-block" data-help="billing.limit">
        <div className="account-label">Your daily limit</div>
        <div className="account-hint">The most paid use in one day is {money(view.daily_ceiling_usd)}. You can set it lower.</div>
        <div className="billing-add">
          <input className="account-input" type="text" inputMode="decimal" placeholder="No limit of your own" aria-label="Your own daily limit, in dollars" value={limitText} disabled={busy}
            onChange={event => setLimitText(event.target.value)} onKeyDown={event => { if (event.key === 'Enter') void saveLimit(); }} />
          <button type="button" className="account-btn" disabled={busy} onClick={() => void saveLimit()}>Save</button>
        </div>
      </div>

      {error && <p className="account-note billing-error" role="alert">{error}</p>}
      {notice && !error && <p className="account-note" role="status">{notice}</p>}

      {view.recent.length > 0 && (
        <div className="billing-block">
          <div className="account-label">Recent</div>
          <ul className="billing-rows">{view.recent.map((row, index) => <li key={`${row.at}-${index}`}>{rowLine(row)}</li>)}</ul>
        </div>
      )}

      <ul className="billing-fine" data-help="billing.fine">{FINE_PRINT.map(line => <li key={line}>{line}</li>)}</ul>
    </section>
  );
}

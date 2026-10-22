import React, { useState } from 'react';
import type { BudgetView } from '../api';
import './__styles__/BudgetBanner.css';

/**
 * The usage budget guard's status line (backend/utils/budget_guard.py), shown above the chat box.
 *
 * - Low-power mode and a paused chat are shown to everyone, because they explain a change in how Sonic behaves.
 * - The early "notice" step is for admins only (the server only sends it amounts and percentages). The percentage itself is the ring beside the
 *   "..." button (components/UsageRing.tsx), not part of this line.
 * - Nothing at all is shown when no budget is configured or the budget is comfortable.
 * - When the shared budget has paused chat, an admin also gets a button that raises the block line by 25% (`onExtend`).
 */
function when(iso: string | null | undefined): string {
  if (!iso) return 'it resets';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return 'it resets';
  return date.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
}

const SCOPE_WORDS = { my_day: 'daily', my_five: '5-hour' } as const;

const BudgetBanner: React.FC<{
  budget: BudgetView | null; theme?: string; onExtend?: () => Promise<void>; onRequest?: () => Promise<void>;
}> = ({ budget, theme, onExtend, onRequest }) => {
  const [extending, setExtending] = useState(false);
  const [extendError, setExtendError] = useState('');
  const [requesting, setRequesting] = useState(false);
  if (!budget || budget.level === 'off') return null;
  const canAsk = Boolean(onRequest) && Boolean(budget.can_request_increase);
  const showMessage = budget.level !== 'ok' && Boolean(budget.message);
  if (!showMessage && !canAsk) return null;

  const own = Boolean(budget.own_limit);
  let text = budget.message;
  if (!showMessage) {
    const percent = Math.floor((budget.own_fraction ?? 0.8) * 100);
    text = `You've used ${percent}% of your ${SCOPE_WORDS[budget.own_scope ?? 'my_day']} usage.`;
  }
  if (budget.level === 'throttled') text = `Low-power mode until ${when(budget.resets_at)}: ${budget.message.replace(/^Low-power mode( for you)?:\s*/, '')}`;
  if (budget.level === 'blocked') text = `Paused until ${when(budget.resets_at)}: ${own ? 'you have reached your usage limit' : 'the usage budget has been reached'}.`;
  const paused = budget.paused.length ? `Paused: ${budget.paused.join(', ')}.` : undefined;
  const canExtend = Boolean(onExtend) && budget.level === 'blocked' && Boolean(budget.can_extend);
  const className = budget.level === 'ok' ? 'notice' : budget.level;

  const extend = async () => {
    if (!onExtend || extending) return;
    setExtending(true);
    setExtendError('');
    try {
      await onExtend();
    } catch (e) {
      setExtendError(e instanceof Error ? e.message : 'Could not extend the budget.');
    } finally {
      setExtending(false);
    }
  };

  const request = async () => {
    if (!onRequest || requesting) return;
    setRequesting(true);
    setExtendError('');
    try {
      await onRequest();
    } catch (e) {
      setExtendError(e instanceof Error ? e.message : 'Could not send the request.');
    } finally {
      setRequesting(false);
    }
  };

  return (
    <div className={`budget-banner budget-${className}${theme === 'shadow' ? ' budget-dark' : ''}`} role="status" aria-live="polite" title={paused}>
      <span className="budget-banner-dot" aria-hidden="true" />
      <span className="budget-banner-text">{text}</span>
      {canAsk && budget.increase_request === 'pending' && <span className="budget-request-status">✓ Request sent. An admin will review it.</span>}
      {canAsk && budget.increase_request === 'declined' && <span className="budget-request-status">Your request was declined for this period.</span>}
      {canAsk && !budget.increase_request && (
        <button type="button" className="budget-extend" onClick={request} disabled={requesting} title="Ask an admin to raise your limit by 25% until this window ends">
          {requesting ? 'Sending…' : 'Request 25% more'}
        </button>
      )}
      {canExtend && (
        <button type="button" className="budget-extend" onClick={extend} disabled={extending} title="Admins only: raise the block line by another 25% until this budget resets">
          {extending ? 'Extending…' : 'Extend by 25%'}
        </button>
      )}
      {extendError && <span className="budget-extend-error">{extendError}</span>}
    </div>
  );
};

export default BudgetBanner;

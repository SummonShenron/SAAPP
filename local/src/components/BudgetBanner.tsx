import React from 'react';
import type { BudgetView } from '../api';
import './__styles__/BudgetBanner.css';

/**
 * The usage budget guard's status line (backend/utils/budget_guard.py), shown above the chat box.
 *
 * - Low-power mode and a paused chat are shown to everyone, because they explain a change in how Sonic behaves.
 * - The early "notice" step and the meter chip are for admins only (the server only sends them amounts and percentages).
 * - Nothing at all is shown when no budget is configured or the budget is comfortable and the person isn't an admin.
 */
function when(iso: string | null | undefined): string {
  if (!iso) return 'it resets';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return 'it resets';
  return date.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
}

function chipText(budget: BudgetView): string {
  const percent = Math.round((budget.fraction ?? 0) * 100);
  return `Budget ${percent}%${budget.scope ? ` of ${budget.scope}` : ''}`;
}

const BudgetBanner: React.FC<{ budget: BudgetView | null; theme?: string }> = ({ budget, theme }) => {
  if (!budget || budget.level === 'off') return null;
  const hasChip = typeof budget.fraction === 'number';
  const showMessage = budget.level !== 'ok' && Boolean(budget.message);
  if (!showMessage && !hasChip) return null;

  let text = budget.message;
  if (budget.level === 'throttled') text = `Low-power mode until ${when(budget.resets_at)}: ${budget.message.replace(/^Low-power mode:\s*/, '')}`;
  if (budget.level === 'blocked') text = `Paused until ${when(budget.resets_at)}: the usage budget has been reached.`;
  const paused = budget.paused.length ? `Paused: ${budget.paused.join(', ')}.` : undefined;

  return (
    <div className={`budget-banner budget-${budget.level}${theme === 'shadow' ? ' budget-dark' : ''}`} role="status" aria-live="polite" title={paused}>
      {showMessage && <span className="budget-banner-text">{text}</span>}
      {hasChip && (
        <span className="budget-chip" title="Share of the usage budget used (admins only)">
          {chipText(budget)}
        </span>
      )}
    </div>
  );
};

export default BudgetBanner;

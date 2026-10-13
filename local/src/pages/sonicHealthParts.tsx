import React from 'react';
import type { BudgetView } from '../api';
import { budgetAmount, budgetTone } from './sonicHealthFormat';

/** One number with a label under it. `warn` turns it red: for a figure that should stay near zero. */
export const StatTile: React.FC<{ value: React.ReactNode; label: React.ReactNode; warn?: boolean }> = ({ value, label, warn }) => (
  <div className={`sh-stat${warn ? ' sh-stat-warn' : ''}`}>
    <div className="sh-stat-value">{value}</div>
    <div className="sh-stat-label">{label}</div>
  </div>
);

/** A labelled horizontal bar: `fraction` is 0 to 1 of the bar's length (values over 1 are clipped to a full bar). */
export const BarRow: React.FC<{ label: React.ReactNode; value: React.ReactNode; fraction: number; tone?: string }> = ({ label, value, fraction, tone = 'ok' }) => (
  <div className="sh-budget-row">
    <div className="sh-budget-label"><span>{label}</span><span>{value}</span></div>
    <div className="sh-bar"><div className={`sh-bar-fill sh-bar-${tone}`} style={{ width: `${Math.max(0, Math.min(fraction, 1)) * 100}%` }} /></div>
  </div>
);

/** The day and month spend bars of the usage budget. */
export const BudgetBars: React.FC<{ budget: BudgetView }> = ({ budget }) => (
  <>
    {(budget.detail ?? []).map(item => (
      <BarRow
        key={`${item.scope}-${item.unit}`}
        label={`${item.scope === 'day' ? 'Today' : 'This month'} (${item.unit === 'usd' ? 'dollars' : 'tokens'})`}
        value={`${budgetAmount(item.unit, item.spent)} of ${budgetAmount(item.unit, item.limit)} (${Math.round(item.fraction * 100)}%)`}
        fraction={item.fraction}
        tone={budgetTone(item.fraction)}
      />
    ))}
  </>
);

import React, { useEffect, useRef, useState } from 'react';
import type { BudgetView } from '../api';
import { budgetChipText, ownUsageText } from './budgetText';
import './__styles__/UsageRing.css';

const SIZE = 20; // smaller than the 28px round icon buttons it sits beside, so it reads as a gauge and not another button
const STROKE = 2.5;
const RADIUS = (SIZE - STROKE) / 2;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;
const TIP_MS = 2500; // how long a tapped tooltip stays (a touch screen has no hover)

/** The ring's colour follows the guard's level, so it reads the same way the old chip's colour did. */
const LEVEL_COLOR: Record<string, string> = { ok: 'var(--usage-ok, #2f7de1)', notice: '#f59e0b', throttled: '#f97316', blocked: 'var(--accent-danger)' };

/**
 * A small ring that fills as the usage budget is used (backend/utils/budget_guard.py). An admin sees the budget that is furthest along, the same one the old
 * "Budget 54% of month" chip showed. Everyone else sees how far along their OWN limit is (their day, or their 5-hour window), as a percentage and no more: the
 * server sends no amount and nothing about the shared budget to anyone but an admin. Hovering, focusing or tapping it says the same words. With no limit
 * of their own and no admin view there is nothing to show, and nothing is drawn.
 */
const UsageRing: React.FC<{ budget: BudgetView | null }> = ({ budget }) => {
  const [tapped, setTapped] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);

  if (!budget || budget.level === 'off') return null;
  const isAdminView = typeof budget.fraction === 'number';
  const shown = isAdminView ? budget.fraction : budget.own_fraction;
  if (typeof shown !== 'number') return null;
  const fraction = Math.max(0, Math.min(shown, 1)); // over 100% is a full ring; the colour and the words carry the rest
  const text = isAdminView ? budgetChipText(budget) : ownUsageText(budget);
  const color = LEVEL_COLOR[(isAdminView ? budget.level : budget.own_level) ?? 'ok'] ?? LEVEL_COLOR.ok;

  const tap = () => {
    setTapped(true);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setTapped(false), TIP_MS);
  };

  return (
    <div className={`usage-ring${tapped ? ' usage-ring-tapped' : ''}`} role="img" aria-label={text} tabIndex={0} onClick={tap} style={{ width: SIZE, height: SIZE }}>
      <svg width={SIZE} height={SIZE} viewBox={`0 0 ${SIZE} ${SIZE}`} aria-hidden="true">
        <circle className="usage-ring-track" cx={SIZE / 2} cy={SIZE / 2} r={RADIUS} fill="none" strokeWidth={STROKE} />
        <circle
          className="usage-ring-fill"
          cx={SIZE / 2}
          cy={SIZE / 2}
          r={RADIUS}
          fill="none"
          strokeWidth={STROKE}
          strokeLinecap="round"
          stroke={color}
          strokeDasharray={CIRCUMFERENCE}
          strokeDashoffset={CIRCUMFERENCE * (1 - fraction)}
          transform={`rotate(-90 ${SIZE / 2} ${SIZE / 2})`}
        />
      </svg>
      <span className="usage-ring-tip" role="tooltip">{text}</span>
    </div>
  );
};

export default UsageRing;

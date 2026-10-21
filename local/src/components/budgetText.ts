import type { BudgetView } from '../api';

/** How a budget scope is said to a person, where it is not already plain English ("day", "month"). */
const SCOPE_TEXT: Record<string, string> = { five: '5 hours', my_day: 'your day', my_five: 'your 5 hours' };

/** What the ring says to the person whose own limit it shows: "Your usage: 54% of your day". */
export function ownUsageText(budget: BudgetView): string {
  const percent = Math.round((budget.own_fraction ?? 0) * 100);
  const scope = budget.own_scope ? (SCOPE_TEXT[budget.own_scope] ?? budget.own_scope) : 'your day';
  return `Your usage: ${percent}% of ${scope}`;
}

export interface RingView {
  fraction: number;
  level: NonNullable<BudgetView['own_level']>;
  text: string;
}

/**
 * What the usage ring draws and says. Everyone else sees their own limit. An admin also has the shared budget, and the ring follows their own day while the shared
 * budget is quiet (below its notice line, so "ok"), because that is the number that moves as they use the app; once the shared budget is at notice or worse it
 * takes over, because that is the one an admin has to act on. The words always give both, so neither is hidden. When the figure furthest along IS the admin's own
 * limit (the server names its scope "my_day" or "my_five") there is only one figure and the ring and words are that. null when there is nothing to show.
 */
export function ringView(budget: BudgetView): RingView | null {
  const shared = typeof budget.fraction === 'number';
  const own = typeof budget.own_fraction === 'number';
  if (!shared && !own) return null;
  if (!shared) return { fraction: budget.own_fraction!, level: budget.own_level ?? 'ok', text: ownUsageText(budget) };
  const leadingIsOwn = budget.scope === 'my_day' || budget.scope === 'my_five';
  const sharedLevel = budget.level === 'off' ? 'ok' : budget.level;
  if (!own || leadingIsOwn) return { fraction: budget.fraction!, level: sharedLevel, text: budgetChipText(budget) };
  const text = `${ownUsageText(budget)} · ${budgetChipText(budget)}`;
  if (sharedLevel === 'ok') return { fraction: budget.own_fraction!, level: budget.own_level ?? 'ok', text };
  return { fraction: budget.fraction!, level: sharedLevel, text };
}

/** "Budget 54% of month": the share of the budget that is furthest along (admins only: the server sends no percentage to anyone else). */
export function budgetChipText(budget: BudgetView): string {
  const percent = Math.round((budget.fraction ?? 0) * 100);
  const scope = budget.scope ? (SCOPE_TEXT[budget.scope] ?? budget.scope) : '';
  const raised = budget.extended_by ? ` (+${Math.round(budget.extended_by * 100)}%)` : '';
  return `Budget ${percent}%${scope ? ` of ${scope}` : ''}${raised}`;
}

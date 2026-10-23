import type { BudgetView } from '../api';

/** How a budget scope is said to a person, where it is not already plain English ("day", "month"). */
const SCOPE_TEXT: Record<string, string> = { five: '5 hours', my_day: 'your day', my_five: 'your 5 hours' };

/** What the ring says to the person whose own limit it shows: "Your usage: 54% of your day". */
export function ownUsageText(budget: BudgetView): string {
  const percent = Math.round((budget.own_fraction ?? 0) * 100);
  const scope = budget.own_scope ? (SCOPE_TEXT[budget.own_scope] ?? budget.own_scope) : 'your day';
  return `Your usage: ${percent}% of ${scope}`;
}

/** When a limit starts over, in the viewer's own time and said briefly: "3:40 PM" later today, "Oct 23, 12:00 AM" on another day, "Nov 1" for a month. null when there
 *  is nothing true to say: no time, or a 5-hour window with no use in it (it only begins at the first use, so it has no end yet). */
export function resetText(iso: string | null | undefined, scope: string | undefined, fraction: number, now: Date = new Date()): string | null {
  if (!iso) return null;
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return null;
  if ((scope === 'five' || scope === 'my_five') && fraction <= 0) return null;
  if (scope === 'month') return at.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  if (at.toDateString() === now.toDateString()) return at.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
  return at.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
}

const withReset = (text: string, reset: string | null) => (reset ? `${text}, resets ${reset}` : text);

export interface RingView {
  fraction: number;
  level: NonNullable<BudgetView['own_level']>;
  text: string;
}

/**
 * What the usage ring draws and says. Everyone else sees their own limit. An admin also has the shared budget, and the ring always follows the figure that is furthest
 * along (the server's `fraction` and `scope`: the month at 61% beats the day at 1%), so it never hides the number that matters most. The words give both, the leading one
 * first, so neither is hidden. When the figure furthest along IS the admin's own limit (the server names its scope "my_day" or "my_five") there is only one figure and
 * the ring and words are that. null when there is nothing to show.
 */
export function ringView(budget: BudgetView): RingView | null {
  const shared = typeof budget.fraction === 'number';
  const own = typeof budget.own_fraction === 'number';
  if (!shared && !own) return null;
  const ownText = () => withReset(ownUsageText(budget), resetText(budget.own_resets_at, budget.own_scope, budget.own_fraction ?? 0));
  if (!shared) return { fraction: budget.own_fraction!, level: budget.own_level ?? 'ok', text: ownText() };
  const leadingIsOwn = budget.scope === 'my_day' || budget.scope === 'my_five';
  const sharedLevel = budget.level === 'off' ? 'ok' : budget.level;
  const sharedText = withReset(budgetChipText(budget), resetText(budget.resets_at, budget.scope, budget.fraction ?? 0));
  if (!own || leadingIsOwn) return { fraction: budget.fraction!, level: sharedLevel, text: sharedText };
  return { fraction: budget.fraction!, level: sharedLevel, text: `${sharedText} · ${ownText()}` };
}

/** "Budget 54% of month": the share of the budget that is furthest along (admins only: the server sends no percentage to anyone else). */
export function budgetChipText(budget: BudgetView): string {
  const percent = Math.round((budget.fraction ?? 0) * 100);
  const scope = budget.scope ? (SCOPE_TEXT[budget.scope] ?? budget.scope) : '';
  const raised = budget.extended_by ? ` (+${Math.round(budget.extended_by * 100)}%)` : '';
  return `Budget ${percent}%${scope ? ` of ${scope}` : ''}${raised}`;
}

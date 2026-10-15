import type { BudgetView } from '../api';

/** How a budget scope is said to a person, where it is not already plain English ("day", "month"). */
const SCOPE_TEXT: Record<string, string> = { five: '5 hours', my_day: 'your day', my_five: 'your 5 hours' };

/** "Budget 54% of month": the share of the budget that is furthest along (admins only: the server sends no percentage to anyone else). */
export function budgetChipText(budget: BudgetView): string {
  const percent = Math.round((budget.fraction ?? 0) * 100);
  const scope = budget.scope ? (SCOPE_TEXT[budget.scope] ?? budget.scope) : '';
  const raised = budget.extended_by ? ` (+${Math.round(budget.extended_by * 100)}%)` : '';
  return `Budget ${percent}%${scope ? ` of ${scope}` : ''}${raised}`;
}

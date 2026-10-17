// Number and date formatting shared by the Sonic Health tabs.

export function pct(value: number | null | undefined): string {
  if (value === null || value === undefined) return 'n/a';
  const n = value * 100;
  return `${n >= 10 ? n.toFixed(0) : n.toFixed(1)}%`;
}

export function tokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

export function usd(value: number | null | undefined): string {
  if (value === null || value === undefined) return '';
  return value < 0.01 ? '<$0.01' : `$${value.toFixed(2)}`;
}

export function shortDate(iso: string | null | undefined): string {
  if (!iso) return '';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
}

export function budgetAmount(unit: 'usd' | 'tokens', value: number): string {
  return unit === 'usd' ? `$${value.toFixed(2)}` : tokens(Math.round(value));
}

/** The colour step a budget fraction is in, matching the guard's own thresholds (80% notice, 100% low-power, 125% paused). */
export function budgetTone(fraction: number): 'ok' | 'notice' | 'throttled' | 'blocked' {
  return fraction >= 1.25 ? 'blocked' : fraction >= 1 ? 'throttled' : fraction >= 0.8 ? 'notice' : 'ok';
}

/** The knowledge bases by what they hold; an id that is not listed is shown as it is. */
export const KB_LABELS: Record<string, string> = {
  Affiliate_A: 'Sonic lore',
  Affiliate_B: 'Dragon Ball',
  Affiliate_C: "Jack's portfolio",
  Affiliate_D: 'BTY Fitness',
  personal: 'Personal knowledge bases (counts only)',
};

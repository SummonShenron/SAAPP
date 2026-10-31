// The data behind the Sonic Health coding-loop charts (components/CounterCharts.tsx). Pure: it turns the counters summary's daily series (backend/utils/self_counters.py `daily`) into the points each chart
// draws, so the arithmetic is pinned by tests and the component only draws. Counts only; there is nothing about a person here.

export interface DailyCounts {
  day: string;
  counts: Record<string, number>;
}

/** One slice of a stacked bar: which counters add up to it, and how it is named and coloured (a CSS variable from the theme, so the charts follow the light and dark themes). */
export interface StackGroup {
  id: string;
  label: string;
  keys: string[];
  color: string;
}

export interface StackPoint {
  day: string;
  total: number;
  values: number[];
  shares: number[];
}

export interface LinePoint {
  day: string;
  value: number | null;
  n: number;
}

const count = (counts: Record<string, number> | undefined, key: string): number => {
  const value = counts?.[key];
  return typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : 0;
};

/** Every calendar day from `days - 1` days before `today` to `today` (all UTC, like the server's days), as YYYY-MM-DD, oldest first. */
export function periodDays(days: number, today: Date): string[] {
  const out: string[] = [];
  const end = Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate());
  for (let back = Math.max(1, Math.round(days)) - 1; back >= 0; back -= 1) out.push(new Date(end - back * 86_400_000).toISOString().slice(0, 10));
  return out;
}

const byDay = (daily: DailyCounts[] | undefined): Map<string, Record<string, number>> => new Map((daily ?? []).map(row => [row.day, row.counts]));

export const OPENING_GROUPS: StackGroup[] = [
  { id: 'targeted', label: 'Went straight to the work (a window, or an edit)', keys: ['loop_open_read_window', 'loop_open_edit'], color: 'var(--cc-blue)' },
  { id: 'exploring', label: 'Explored (searched, listed, found a file)', keys: ['loop_open_search', 'loop_open_tree', 'loop_open_find'], color: 'var(--cc-gray)' },
  { id: 'top', label: 'Read a file from the top', keys: ['loop_open_read_top'], color: 'var(--cc-orange)' },
];

export const PEAK_GROUPS: StackGroup[] = [
  { id: 'small', label: 'Up to 25K tokens', keys: ['code_ctx_peak_small'], color: 'var(--cc-ramp-1)' },
  { id: 'medium', label: '25K to 50K', keys: ['code_ctx_peak_medium'], color: 'var(--cc-ramp-2)' },
  { id: 'large', label: '50K to 100K', keys: ['code_ctx_peak_large'], color: 'var(--cc-ramp-3)' },
  { id: 'huge', label: 'Over 100K (Haiku costs 5x more)', keys: ['code_ctx_peak_huge'], color: 'var(--cc-danger)' },
];

export const RANK_GROUPS: StackGroup[] = [
  { id: 'first', label: 'Ranked 1st', keys: ['orient_rank_first'], color: 'var(--cc-ramp-1)' },
  { id: 'second', label: '2nd', keys: ['orient_rank_second'], color: 'var(--cc-ramp-2)' },
  { id: 'third', label: '3rd', keys: ['orient_rank_third'], color: 'var(--cc-ramp-3)' },
  { id: 'fourth', label: '4th', keys: ['orient_rank_fourth'], color: 'var(--cc-ramp-4)' },
  { id: 'absent', label: 'Not listed', keys: ['orient_rank_absent'], color: 'var(--cc-gray)' },
];

/** One stacked bar per day (every day of the period, empty ones too): the groups' counts, their total, and each group's share of it. Pure. */
export function stackPoints(daily: DailyCounts[] | undefined, days: string[], groups: StackGroup[]): StackPoint[] {
  const rows = byDay(daily);
  return days.map(day => {
    const counts = rows.get(day);
    const values = groups.map(group => group.keys.reduce((sum, key) => sum + count(counts, key), 0));
    const total = values.reduce((sum, value) => sum + value, 0);
    return { day, total, values, shares: values.map(value => (total > 0 ? value / total : 0)) };
  });
}

/** A single stacked bar over a whole set of totals (the period, or the one before it): the same shape as one day's. Pure. */
export function stackTotals(totals: Record<string, number> | undefined, groups: StackGroup[]): StackPoint {
  const values = groups.map(group => group.keys.reduce((sum, key) => sum + count(totals, key), 0));
  const total = values.reduce((sum, value) => sum + value, 0);
  return { day: '', total, values, shares: values.map(value => (total > 0 ? value / total : 0)) };
}

/** A per-day average: `sumKey` divided by `countKey`, null on a day with none (so the line has a gap, not a false zero). `n` is how many turns it is the average of. Pure. */
export function averageLine(daily: DailyCounts[] | undefined, days: string[], sumKey: string, countKey: string): LinePoint[] {
  const rows = byDay(daily);
  return days.map(day => {
    const n = count(rows.get(day), countKey);
    return { day, value: n > 0 ? count(rows.get(day), sumKey) / n : null, n };
  });
}

/** A per-day share: `numeratorKey` over `denominatorKey` (as a fraction), null on a day with no denominator. Pure. */
export function ratioLine(daily: DailyCounts[] | undefined, days: string[], numeratorKey: string, denominatorKey: string): LinePoint[] {
  const rows = byDay(daily);
  return days.map(day => {
    const n = count(rows.get(day), denominatorKey);
    return { day, value: n > 0 ? Math.min(count(rows.get(day), numeratorKey) / n, 1) : null, n };
  });
}

/** True when a chart has anything to draw. */
export const hasStack = (points: StackPoint[]): boolean => points.some(point => point.total > 0);
export const hasLine = (points: LinePoint[]): boolean => points.some(point => point.value !== null);

/** The smallest round number (1, 2, 2.5, 5 times a power of ten) at or above `value`, so an axis ends on a clean figure. */
export function niceMax(value: number): number {
  if (!(value > 0)) return 1;
  const power = Math.pow(10, Math.floor(Math.log10(value)));
  const f = value / power;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10) * power;
}

export const percent = (share: number): string => `${Math.round(share * 100)}%`;

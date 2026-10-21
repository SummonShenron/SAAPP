// The arithmetic and wording of the "who spent what, one day" graph and table (SonicHealthWhoSpent.tsx). Kept free of React and of other project imports so
// `node --test src/pages/usersDayLayout.test.mjs` (from local/, Node 22.18+) can run it directly.

export interface DayPerson {
  user: string;
  you: boolean;
  cost: number;
  tokens: number;
  calls: number;
  turns: number;
  share: number | null;
}

export interface DayBreakdown {
  day: string;
  total: number;
  people: DayPerson[];
  more_people: number;
  evals: { cost: number; tokens: number; calls: number; share: number | null } | null;
  background: { cost: number; share: number | null } | null;
}

export type BarKind = 'you' | 'person' | 'others' | 'evals' | 'background';

export interface BarItem {
  key: string;
  label: string;
  /** The full text for the tooltip (the whole account id, the figures). */
  title: string;
  cost: number;
  share: number | null;
  kind: BarKind;
}

/** How many accounts the graph names; the rest are added up, the full list is the table on the Usage tab. */
export const BAR_LIMIT = 10;

/** Dollars, two places, or three when it is under a cent so a tiny amount is not shown as nothing. */
export function money(value: number): string {
  if (!(value > 0)) return '$0.00';
  return value < 0.01 ? `$${value.toFixed(3)}` : `$${value.toFixed(2)}`;
}

export function percent(fraction: number | null | undefined): string {
  return typeof fraction === 'number' ? `${Math.round(fraction * 100)}%` : '';
}

/** A long account id shortened in the middle; short names are left alone. */
export function shortName(name: string): string {
  return name.length > 24 ? `${name.slice(0, 12)}…${name.slice(-8)}` : name;
}

/** "Oct 20" from a billing day. */
export function shortDay(iso: string): string {
  const [, month, day] = iso.split('-').map(Number);
  const names = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  return `${names[(month || 1) - 1]} ${day}`;
}

function accounts(n: number): string {
  return `${n} account${n === 1 ? '' : 's'}`;
}

/** The bars for one day, biggest first: up to BAR_LIMIT named accounts (the viewer is always among them), the rest added up, then the evals and the background jobs. */
export function barItems(day: DayBreakdown, limit: number = BAR_LIMIT): BarItem[] {
  const out: BarItem[] = [];
  let named = day.people.slice(0, limit);
  const you = day.people.find(p => p.you);
  if (you && !named.includes(you)) named = day.people.slice(0, Math.max(limit - 1, 0)).concat(you);
  for (const person of named) {
    out.push({
      key: `person:${person.user}`,
      label: person.you ? 'You' : shortName(person.user),
      title: `${person.user}${person.you ? ' (you)' : ''} · ${money(person.cost)}${person.share !== null ? ` · ${percent(person.share)} of the day` : ''} · ${person.turns} turn${person.turns === 1 ? '' : 's'}`,
      cost: person.cost,
      share: person.share,
      kind: person.you ? 'you' : 'person',
    });
  }
  const rest = day.people.filter(p => !named.includes(p));
  if (rest.length > 0 || day.more_people > 0) {
    const cost = Math.round(rest.reduce((sum, p) => sum + p.cost, 0) * 10000) / 10000;
    const count = rest.length + day.more_people;
    out.push({
      key: 'others', label: `${accounts(count)} more`, title: `${accounts(count)} more${rest.length ? ` · ${money(cost)}` : ''}`, cost,
      share: day.total > 0 && rest.length ? Math.round((cost / day.total) * 10000) / 10000 : null, kind: 'others',
    });
  }
  if (day.evals) out.push({ key: 'evals', label: 'Testing and evals', title: `Testing and evals · ${money(day.evals.cost)} · ${day.evals.calls} calls`, cost: day.evals.cost, share: day.evals.share, kind: 'evals' });
  if (day.background) out.push({
    key: 'background', label: 'Background jobs', title: `Background jobs and anything not tied to an account · ${money(day.background.cost)}`,
    cost: day.background.cost, share: day.background.share, kind: 'background',
  });
  return out;
}

/** The width of a bar as a fraction of the whole day, so the bar reads as a share of the day. */
export function barFraction(item: BarItem, day: DayBreakdown): number {
  if (!(day.total > 0) || !(item.cost > 0)) return 0;
  return Math.min(item.cost / day.total, 1);
}

/** One sentence above the graph: the day's total, how many accounts, and the viewer's own share. */
export function dayHeadline(day: DayBreakdown, viewer: string | null, label: string): string {
  if (!(day.total > 0)) return `${label}: nothing was spent.`;
  const lead = `${label}: ${money(day.total)} in all across ${accounts(day.people.length + day.more_people)}`;
  const you = day.people.find(p => p.you);
  if (you) return `${lead}. You used ${money(you.cost)}${you.share !== null ? ` (${percent(you.share)} of the day)` : ''}.`;
  return viewer ? `${lead}. You did not use Sonic that day.` : `${lead}.`;
}

export interface TableRow {
  key: string;
  account: string;
  /** False for the evals and the background jobs: their names are words, not account ids, and are shown whole. */
  isAccount: boolean;
  you: boolean;
  cost: number;
  share: number | null;
  turns: number | null;
  calls: number | null;
  tokens: number | null;
}

/** The full table for one day: every account listed (up to the 60 the server sends), then the evals and the background jobs. The total is separate. */
export function tableRows(day: DayBreakdown): TableRow[] {
  const rows: TableRow[] = day.people.map(p => ({
    key: `person:${p.user}`, account: p.user, isAccount: true, you: p.you, cost: p.cost, share: p.share, turns: p.turns, calls: p.calls, tokens: p.tokens,
  }));
  if (day.evals) rows.push({ key: 'evals', account: 'Testing and evals', isAccount: false, you: false, cost: day.evals.cost, share: day.evals.share, turns: null, calls: day.evals.calls, tokens: day.evals.tokens });
  if (day.background) rows.push({ key: 'background', account: 'Background jobs and anything not tied to an account', isAccount: false, you: false, cost: day.background.cost, share: day.background.share, turns: null, calls: null, tokens: null });
  return rows;
}

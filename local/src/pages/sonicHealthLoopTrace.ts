// The Sonic Health rows that say where a coding turn goes wrong (backend/utils/reading_waste.py, loop_rhythm.py, code_outcomes.py): how it opens, whether the "where to look" block (backend/utils/orientation.py)
// listed what got edited, when the first edit is proposed, and how big the prompts get. Pure: it turns two sets of counter totals (this period and the one before) into label / current / previous cells, so the page
// only renders them and a test can pin the arithmetic. Counts only, so there is nothing about a person here.

export type Totals = Record<string, number | undefined>;

export interface LoopTraceRow {
  id: string;
  label: string;
  current: string;
  previous: string;
  note: string;
}

const n = (totals: Totals, key: string): number => {
  const value = totals[key];
  return typeof value === 'number' && Number.isFinite(value) ? value : 0;
};

const pct = (part: number, whole: number): string => (whole > 0 ? `${Math.round((part / whole) * 100)}%` : '-');
const avg = (sum: number, count: number, digits = 1): string => (count > 0 ? (sum / count).toFixed(digits) : '-');

const OPENING_PARTS: Array<[string, string[]]> = [
  ['from the top', ['read_top']],
  ['at a window', ['read_window']],
  ['searched', ['search']],
  ['listed or found', ['tree', 'find']],
  ['edited', ['edit']],
];

const opening = (totals: Totals, prefix: string): string => {
  const kinds = ['read_top', 'read_window', 'search', 'tree', 'find', 'edit', 'other'];
  const whole = kinds.reduce((sum, kind) => sum + n(totals, `${prefix}_${kind}`), 0);
  if (whole <= 0) return '-';
  return OPENING_PARTS.map(([label, group]) => `${label} ${pct(group.reduce((sum, kind) => sum + n(totals, `${prefix}_${kind}`), 0), whole)}`).join(' / ');
};

const RANKS: Array<[string, string]> = [['first', '1st'], ['second', '2nd'], ['third', '3rd'], ['fourth', '4th'], ['absent', 'not listed']];

const rank = (totals: Totals): string => {
  const whole = RANKS.reduce((sum, [key]) => sum + n(totals, `orient_rank_${key}`), 0);
  return whole > 0 ? RANKS.map(([key, label]) => `${label} ${pct(n(totals, `orient_rank_${key}`), whole)}`).join(' / ') : '-';
};

const firstEdit = (totals: Totals): string => {
  const turns = n(totals, 'code_first_edit_turns');
  if (turns <= 0) return '-';
  const early = n(totals, 'code_first_edit_at_very_early') + n(totals, 'code_first_edit_at_early');
  return `step ${avg(n(totals, 'code_first_edit_steps'), turns)} on average, by step 4 in ${pct(early, turns)}`;
};

const firstEditWithBlock = (totals: Totals): string => {
  const turns = n(totals, 'orient_first_edit_turns');
  return turns > 0 ? `step ${avg(n(totals, 'orient_first_edit_steps'), turns)} on average` : '-';
};

const topShare = (totals: Totals): string => pct(n(totals, 'reading_top_reads'), n(totals, 'reading_top_reads') + n(totals, 'reading_window_reads'));

const peak = (totals: Totals): string => {
  const turns = n(totals, 'code_ctx_turns');
  if (turns <= 0) return '-';
  return `over 100K in ${pct(n(totals, 'code_ctx_peak_huge'), turns)} of turns, ${avg(n(totals, 'code_ctx_calls_huge'), turns, 2)} such calls per turn`;
};

const cache = (totals: Totals): string => pct(n(totals, 'code_ctx_cached'), n(totals, 'code_ctx_input'));

const blockUse = (totals: Totals): string => {
  const shown = n(totals, 'orient_turns');
  if (shown <= 0) return '-';
  return `${shown} turns; first read was a listed file in ${pct(n(totals, 'orient_first_read_in_block'), n(totals, 'orient_read_turns'))}`;
};

/** The rows, in the order they are shown. `current` and `previous` are the totals for this period and the one before. */
export function loopTraceRows(current: Totals, previous: Totals): LoopTraceRow[] {
  const both = (label: string, id: string, note: string, cell: (totals: Totals) => string): LoopTraceRow => ({ id, label, current: cell(current), previous: cell(previous), note });
  return [
    both('How a coding turn opened: its first move', 'open', 'share of turns', totals => opening(totals, 'loop_open')),
    both('... the same, for turns that were given the "where to look" block', 'open-block', 'share of turns', totals => opening(totals, 'loop_open_block')),
    both('Turns given the "where to look" block, and how often the first file read was one it listed', 'block-use', 'turns / share', blockUse),
    both('In block turns that applied an edit: where the best edited file ranked in the block', 'block-rank', 'share of edit turns', rank),
    both('Step of the first edit proposal, all coding turns that proposed one', 'first-edit', 'steps', firstEdit),
    both('... the same, for turns given the block', 'first-edit-block', 'steps', firstEditWithBlock),
    both('Reads that started at the top of a file, against a window where the work is', 'top-reads', 'share of reads', topShare),
    both('Biggest prompt of a coding turn: turns over 100K tokens (Haiku costs 5x more there)', 'peak', 'share / calls', peak),
    both('Input served from the prompt cache in coding turns', 'cache', 'share of input', cache),
  ];
}

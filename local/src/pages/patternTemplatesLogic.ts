// The words of the "Pattern stories" card on Sonic Health, kept apart from React so they can be tested (backend/utils/pattern_notes.py is the other half).
import type { PatternTemplate } from '../api';

/** What a template says about itself: whether it writes, and what it has written. */
export function templateState(template: Pick<PatternTemplate, 'approved' | 'notes' | 'latest'>): string {
  if (!template.approved) return 'Not approved: nothing is written for it.';
  const made = template.notes === 0 ? 'No note yet.' : `${template.notes} note${template.notes === 1 ? '' : 's'} written, the latest for ${template.latest ?? 'an earlier day'}.`;
  return `Approved: it writes a note when a finished day fits. ${made}`;
}

/** The "write the earlier days" button is only offered while at least one template is approved (nothing is written for one that is not). */
export function canBackfill(templates: Array<Pick<PatternTemplate, 'approved'>>): boolean {
  return templates.some(template => template.approved);
}

/** What the button reported: how many notes it wrote and how many days it left for the daily pass because of its own limit. */
export function backfillMessage(result: { written: number; capped: number }): string {
  if (result.written === 0 && result.capped === 0) return 'No earlier day fits an approved template, or they were all written already.';
  const wrote = `${result.written} note${result.written === 1 ? '' : 's'} written for earlier days`;
  return result.capped ? `${wrote}; ${result.capped} more day${result.capped === 1 ? '' : 's'} fit but were left out (the most it writes at once). Press it again for the next ones.` : `${wrote}.`;
}

/** The limits, in one sentence. */
export function limitsLine(limits: { per_week: number; recent_days: number; backfill: number }): string {
  return `At most ${limits.per_week} a week and one a day, looking at the last ${limits.recent_days} days; "write the earlier days" writes up to ${limits.backfill} at once and ignores the weekly limit.`;
}

// The words and the sorting of the "Its voice" tab on Sonic Health, kept apart from React so they can be tested (backend/utils/voice_review.py is the other half).
import type { VoiceReviewAction, VoiceReviewItem } from '../api';

export type VoiceFilter = 'all' | 'approved' | 'drafts' | 'unused';
export const FILTERS: ReadonlyArray<{ id: VoiceFilter; label: string }> = [
  { id: 'all', label: 'All' },
  { id: 'approved', label: 'In use' },
  { id: 'drafts', label: 'Not in use' },
  { id: 'unused', label: 'In use, never matched' },
];
/** How many rows a list shows before "Show all". */
export const FIRST_ROWS = 12;

/** The rows of one kind, narrowed by a filter. "In use, never matched" is the question the counts exist to answer: an approved item that no message has reached in the period. */
export function filterItems(items: VoiceReviewItem[], kind: VoiceReviewItem['kind'], filter: VoiceFilter): VoiceReviewItem[] {
  const ofKind = items.filter(item => item.kind === kind);
  if (filter === 'approved') return ofKind.filter(item => item.approved);
  if (filter === 'drafts') return ofKind.filter(item => !item.approved);
  if (filter === 'unused') return ofKind.filter(item => item.approved && item.used === 0);
  return ofKind;
}

/** One line over a list: how many may be used, and how many of those were matched at all in the period. */
export function summaryLine(items: VoiceReviewItem[], kind: VoiceReviewItem['kind'], days: number): string {
  const ofKind = items.filter(item => item.kind === kind);
  const inUse = ofKind.filter(item => item.approved);
  const matched = inUse.filter(item => item.used > 0).length;
  if (ofKind.length === 0) return 'None yet.';
  const what = kind === 'example' ? 'examples' : 'facts';
  return `${inUse.length} of ${ofKind.length} ${what} may be used. ${matched} of those ${matched === 1 ? 'was' : 'were'} matched to a message in the last ${days} days.`;
}

/** Who decided: the registry file, or a person here. */
export function stateLine(item: Pick<VoiceReviewItem, 'approved' | 'default' | 'decision'>): string {
  if (item.decision === 'removed') return `Removed here${item.default ? ' (the registry has it approved)' : ''}: it is not used.`;
  if (item.decision === 'approved') return `Approved here${item.default ? '' : ' (the registry has it as a draft)'}: it is used.`;
  return item.approved ? 'Approved in the registry: it is used.' : 'A draft: it is never used until you approve it.';
}

/** How often it was matched to a message. A draft or removed item is not used (stateLine already says so), so it only says if it was matched before, and nothing more. */
export function usageLine(item: Pick<VoiceReviewItem, 'approved' | 'used'>, days: number): string {
  if (!item.approved) return item.used > 0 ? `It was matched ${item.used} time${item.used === 1 ? '' : 's'} in the last ${days} days, before it stopped being used.` : '';
  if (item.used === 0) return `In use, but no message matched it in the last ${days} days.`;
  return `Matched ${item.used} time${item.used === 1 ? '' : 's'} in the last ${days} days.`;
}

/** The buttons a row offers: approve or remove by whether it is in use, and a way back to the registry's setting once a person has decided. */
export function actionsFor(item: Pick<VoiceReviewItem, 'approved' | 'decision'>): VoiceReviewAction[] {
  const actions: VoiceReviewAction[] = [item.approved ? 'remove' : 'approve'];
  if (item.decision !== null) actions.push('reset');
  return actions;
}

export const ACTION_LABELS: Record<VoiceReviewAction, string> = { approve: 'Approve', remove: 'Remove', reset: 'Back to the registry' };

/** The same floors the registry's own check holds the whole set to (backend/utils/voice_examples.py check_set: at least 6 light and 5 hurting examples). Said as a warning when removals have taken the set under them. */
export const MIN_LIGHT = 6;
export const MIN_HURTING = 5;
export function thinWarning(items: VoiceReviewItem[]): string {
  const inUse = items.filter(item => item.kind === 'example' && item.approved);
  const light = inUse.filter(item => item.tone === 'light').length;
  const hurting = inUse.filter(item => item.tone === 'hurting').length;
  const short: string[] = [];
  if (light < MIN_LIGHT) short.push(`${light} light (the set is meant to have at least ${MIN_LIGHT})`);
  if (hurting < MIN_HURTING) short.push(`${hurting} hurting (at least ${MIN_HURTING})`);
  return short.length ? `Only ${short.join(' and ')} examples are in use, so the voice has less to match against.` : '';
}

// The pure parts of the memory compaction meter (components/CompactionMeter.tsx), kept apart from React so they can be tested. The meter shows how many of Sonic's memory notes are waiting to be folded together
// against the number at which they are (backend/utils/compaction_meter.py): the ring fills clockwise as notes are added, and when a compaction folds them in it traces back to the start.

import { gaugeSummary } from './sessionColumnLogic.ts';      // with the extension: the test runner loads this file directly, and needs it (the build allows it)

export interface CompactionView { uncompacted: number; threshold: number; }

/** How long the ring takes to fill (components/__styles__/Ring.css: 0.7 s) and to trace back after a compaction (1.8 s: slower, so the person can see it happen). */
export const FORWARD_MS = 700;
export const RETURN_MS = 1800;

const whole = (value: unknown): number | null => (typeof value === 'number' && Number.isFinite(value) && value >= 0 ? Math.floor(value) : null);

/** What the server sent, or null when it is not two real counts (a threshold of zero would make a meter that is always full, so it is refused). */
export function cleanMeter(raw: unknown): CompactionView | null {
  if (!raw || typeof raw !== 'object') return null;
  const item = raw as Record<string, unknown>;
  const uncompacted = whole(item.uncompacted);
  const threshold = whole(item.threshold);
  if (uncompacted === null || threshold === null || threshold === 0) return null;
  return { uncompacted, threshold };
}

/** How far along the line is, from 0 (just compacted) to 1 (the next note triggers a compaction). */
export function markerFraction(view: CompactionView): number {
  return Math.min(1, Math.max(0, view.uncompacted / view.threshold));
}

/** "37 of 100 notes"; at the threshold or past it, the next note will fold them. */
export function meterText(view: CompactionView): string {
  const noun = view.threshold === 1 ? 'note' : 'notes';
  if (view.uncompacted > view.threshold) return `${view.uncompacted} notes: they are folded together on the next note`;
  return `${view.uncompacted} of ${view.threshold} ${noun}`;
}

/** The words beside the dial (as the usage ring's are beside it): the count, or what is happening when the notes are over the limit. */
export function meterCaption(view: CompactionView): string {
  return view.uncompacted > view.threshold ? 'Overloaded: folding notes together' : meterText(view);
}

/** The few words for a folded card's header: "Memory 37/100". */
export function meterSummary(view: CompactionView | null): string {
  return view ? `Memory ${view.uncompacted}/${view.threshold}` : '';
}

/** The folded Gauges card's header and the phone strip's tooltip: "Memory 37/100 · Usage 12%". Only what there is. */
export function gaugesSummary(meter: CompactionView | null, usage: { fraction: number } | null): string {
  return [meterSummary(meter), gaugeSummary(null, usage)].filter(Boolean).join(' · ');
}

/** Past the threshold the ring is full and the notes are being folded together: "overloaded" (red, pulsing) until the count falls. */
export function isOverloaded(view: CompactionView): boolean {
  return view.uncompacted > view.threshold;
}

/** The colour level of the ring: quiet until it is nearly full, then the warm "notice" colour (the same levels as the usage ring's). */
export function meterLevel(view: CompactionView): 'ok' | 'notice' {
  return markerFraction(view) >= 0.9 ? 'notice' : 'ok';
}

/** Whether a new reading means a compaction just happened: the count fell. The first reading (nothing before it) and an unchanged or rising count are not. */
export function didCompact(previous: CompactionView | null, next: CompactionView | null): boolean {
  return !!previous && !!next && next.uncompacted < previous.uncompacted;
}

/** The meter's tooltip: its words, then (when the server reported it) how full the model's window got on the last turn, which this meter replaced as the ring. */
export function meterTitle(view: CompactionView, contextText?: string | null): string {
  const base = `Memory notes waiting to be folded together: ${meterText(view)}`;
  return contextText ? `${base}. ${contextText}` : base;
}

// The pure parts of the memory compaction meter (components/CompactionMeter.tsx), kept apart from React so they can be tested. The meter shows how many of Sonic's memory notes are waiting to be folded together
// against the number at which they are (backend/utils/compaction_meter.py): the ring fills clockwise as notes are added, and when a compaction folds them in it traces back to the start.

import { gaugeSummary } from './sessionColumnLogic.ts';      // with the extension: the test runner loads this file directly, and needs it (the build allows it)

/** Which reading the one ring shows: Sonic's memory notes waiting to be folded (across conversations), or this conversation's messages waiting to be folded into its summary. */
export type MeterKind = 'memory' | 'conversation';

/** `uncompacted` and `threshold` are notes (memory) or messages (conversation). No `kind` means memory, the first reading the ring ever had. */
export interface CompactionView { uncompacted: number; threshold: number; kind?: MeterKind; /** Conversation reading only: a fold would have something to do (enough messages beyond the newest ones kept word for word), so the ring offers "Fold now". */ foldable?: boolean; }

const kindOf = (view: CompactionView): MeterKind => view.kind ?? 'memory';

/** A fold needs at least this many messages besides the newest ones it leaves alone (the server's MIN_FOLD_MESSAGES). */
const MIN_FOLD_MESSAGES = 2;

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

/** The conversation's reading from the summary endpoint (backend/utils/conversation_summary.py page_view): messages after the last one folded, and the number at which Sonic folds them. Null when
 *  it is not two real counts (a threshold of zero would make a ring that is always full). */
export function conversationMeter(raw: unknown): CompactionView | null {
  if (!raw || typeof raw !== 'object') return null;
  const item = raw as Record<string, unknown>;
  const unfolded = whole(item.unfolded);
  const foldAt = whole(item.fold_at);
  if (unfolded === null || foldAt === null || foldAt === 0) return null;
  const keep = whole(item.keep);
  return { uncompacted: unfolded, threshold: foldAt, kind: 'conversation', foldable: keep !== null && unfolded - keep >= MIN_FOLD_MESSAGES };
}

/** How far along the line is, from 0 (just compacted) to 1 (the next note triggers a compaction). */
export function markerFraction(view: CompactionView): number {
  return Math.min(1, Math.max(0, view.uncompacted / view.threshold));
}

/** "37 of 100 notes" (or messages); at the threshold or past it, the next note (or reply) will fold them. */
export function meterText(view: CompactionView): string {
  if (kindOf(view) === 'conversation') {
    if (view.uncompacted > view.threshold) return `${view.uncompacted} messages: they are folded into the summary after the next reply`;
    return `${view.uncompacted} of ${view.threshold} messages`;
  }
  const noun = view.threshold === 1 ? 'note' : 'notes';
  if (view.uncompacted > view.threshold) return `${view.uncompacted} notes: they are folded together on the next note`;
  return `${view.uncompacted} of ${view.threshold} ${noun}`;
}

/** The words beside the dial (as the usage ring's are beside it): the count, or what is happening when the notes are over the limit. */
export function meterCaption(view: CompactionView): string {
  if (view.uncompacted <= view.threshold) return meterText(view);
  return kindOf(view) === 'conversation' ? 'Overloaded: folding into the summary' : 'Overloaded: folding notes together';
}

/** The ring's name beside it: "Memory" or "Chat". */
export function meterName(view: CompactionView): string {
  return kindOf(view) === 'conversation' ? 'Chat' : 'Memory';
}

/** The few words for a folded card's header: "Memory 37/100" or "Chat 9/14". */
export function meterSummary(view: CompactionView | null): string {
  return view ? `${meterName(view)} ${view.uncompacted}/${view.threshold}` : '';
}

/** Which reading the person left the ring on. A per-viewer convenience, remembered in the browser; memory is the default. */
export const METER_MODE_KEY = 'sonic-meter-mode';

export function readMeterMode(storage?: Pick<Storage, 'getItem'> | null): MeterKind {
  try {
    const store = storage === undefined ? globalThis.localStorage : storage;
    return store?.getItem(METER_MODE_KEY) === 'conversation' ? 'conversation' : 'memory';
  } catch {
    return 'memory';
  }
}

export function writeMeterMode(mode: MeterKind, storage?: Pick<Storage, 'setItem'> | null): void {
  try {
    const store = storage === undefined ? globalThis.localStorage : storage;
    store?.setItem(METER_MODE_KEY, mode);
  } catch {
    /* private window or blocked storage: the ring simply starts on memory next time */
  }
}

export const otherMeterMode = (mode: MeterKind): MeterKind => (mode === 'memory' ? 'conversation' : 'memory');

/** The reading to draw: the chosen one, or whichever exists when the other has not arrived (or does not exist here). */
export function pickMeter(mode: MeterKind, memory: CompactionView | null, conversation: CompactionView | null): CompactionView | null {
  const first = mode === 'conversation' ? conversation : memory;
  const other = mode === 'conversation' ? memory : conversation;
  return first ?? other ?? null;
}

/** The ring can be switched only when both readings exist. */
export const canSwitchMeter = (memory: CompactionView | null, conversation: CompactionView | null): boolean => !!memory && !!conversation;

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

/** Whether a new reading means a compaction just happened: the count fell. The first reading (nothing before it), an unchanged or rising count, and a switch between the two readings are not. */
export function didCompact(previous: CompactionView | null, next: CompactionView | null): boolean {
  return !!previous && !!next && kindOf(previous) === kindOf(next) && next.uncompacted < previous.uncompacted;
}

/** The meter's tooltip: its words, then (when the server reported it) how full the model's window got on the last turn, which this meter replaced as the ring. */
export function meterTitle(view: CompactionView, contextText?: string | null): string {
  const base = kindOf(view) === 'conversation'
    ? `Messages waiting to be folded into this conversation's summary: ${meterText(view)}`
    : `Memory notes waiting to be folded together: ${meterText(view)}`;
  return contextText ? `${base}. ${contextText}` : base;
}

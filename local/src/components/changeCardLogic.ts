/**
 * The logic of the change card (components/ChangeCard.tsx): one change to the person's Google Calendar or a Google Doc, from Sonic's propose_calendar_change / propose_doc_change tools (backend/services/change_cards.py).
 * Pure, so it can be tested without a page. The server prepares the card against the real event or document and writes its words; this only refuses what could not be drawn and words what happened.
 */
/** What the card is about, as the server names it: create, update, delete for the calendar; create, append, replace, insert for documents. */
export type ChangeAction = string;

export interface ChangeCardRow {
  label: string;
  value: string;
}

export interface ChangeCardData {
  proposal_id: string;
  action: ChangeAction;
  /** The small heading above the title and the words on the button that applies the change, as the server wrote them. */
  heading: string;
  applyLabel: string;
  /** The event's or document's title: the new one for an add, the current one for a change or a delete. */
  title: string;
  rows: ChangeCardRow[];
  /** Who Google will email, or that nobody will be. Always shown. */
  notice: string;
  /** A delete: the button is drawn as the careful one. */
  destructive: boolean;
}

export type ChangeDecision = 'apply' | 'decline';

/** What the server answers to a press (backend/services/change_cards.py decide). */
export type ChangeStatus = 'applied' | 'declined' | 'failed' | 'expired' | 'invalid';

const ACTION_SHAPE = /^[a-z_]{1,30}$/;

/** The card from a `calendar_card` event, or null when it cannot be drawn (no id, no title, not a known action). Rows without a label or a value are dropped. */
export function parseChangeCard(payload: unknown): ChangeCardData | null {
  if (!payload || typeof payload !== 'object') return null;
  const raw = payload as Record<string, unknown>;
  const id = typeof raw.proposal_id === 'string' ? raw.proposal_id : '';
  const title = typeof raw.title === 'string' ? raw.title.trim() : '';
  const action = typeof raw.action === 'string' ? raw.action : '';
  if (!id || !title || !ACTION_SHAPE.test(action)) return null;
  const rows: ChangeCardRow[] = [];
  if (Array.isArray(raw.rows)) {
    for (const item of raw.rows) {
      if (!item || typeof item !== 'object') continue;
      const row = item as Record<string, unknown>;
      const label = typeof row.label === 'string' ? row.label.trim() : '';
      const value = typeof row.value === 'string' ? row.value.trim() : '';
      if (label && value) rows.push({ label, value });
    }
  }
  return {
    proposal_id: id,
    action,
    heading: typeof raw.heading === 'string' && raw.heading.trim() ? raw.heading.trim() : 'Change',
    applyLabel: typeof raw.apply_label === 'string' && raw.apply_label.trim() ? raw.apply_label.trim() : 'Apply',
    title,
    rows,
    notice: typeof raw.notice === 'string' ? raw.notice.trim() : '',
    destructive: raw.destructive === true || action === 'delete',
  };
}

/** The line shown on the card once the press is answered. `message` is the server's own sentence (what happened, or why nothing did). */
export function outcomeLine(status: ChangeStatus, message: string): string {
  if (status === 'applied') return message ? `Done. ${message}` : 'Done.';
  if (status === 'declined') return 'Declined. Nothing was changed.';
  if (status === 'failed') return message ? `Nothing was changed. ${message}` : 'Nothing was changed.';
  if (status === 'expired') return 'Sonic is no longer waiting for this, so nothing was changed. Ask again if you still want it.';
  return 'That could not be sent.';
}

// What the page says about a guest's free demo turns (docs/guest-mode-plan.md, steps 2 and 4). Pure, so it is tested by itself (src/guestAllowance.test.mjs). The numbers come from the server
// (backend/utils/guest_limits.py: GUEST_VISITOR_DAILY_TURNS, GUEST_NUDGE_AT_TURN and the rest are settings there); nothing here knows them.

export interface GuestAllowance {
  applies: boolean;
  used?: number;
  limit?: number;
  remaining?: number | null;
  nudge_at?: number;
  /** Why this visitor cannot send another message: "visitor", "ip" or "budget"; "" when they can. */
  blocked?: string;
}

export type GuestAllowanceTone = 'info' | 'nudge' | 'blocked';

export interface GuestAllowanceNote {
  tone: GuestAllowanceTone;
  text: string;
}

/** The line to show above the message box, or null for nothing: not for anyone but the shared guest, not before a first turn (the welcome screen stays clean), and not when no limit is set. */
export function guestAllowanceNote(allowance: GuestAllowance | null | undefined): GuestAllowanceNote | null {
  if (!allowance || !allowance.applies) return null;
  if (allowance.blocked) {
    return {
      tone: 'blocked',
      text: allowance.blocked === 'budget'
        ? 'The free demo has reached its limit for today. A free account keeps going, or come back tomorrow.'
        : "You've used today's free demo turns. A free account keeps going.",
    };
  }
  const { used = 0, limit = 0, remaining = null, nudge_at: nudgeAt = 0 } = allowance;
  if (!limit || remaining === null || remaining === undefined || used < 1) return null;
  const left = `${remaining} of ${limit} free demo turns left today.`;
  if (nudgeAt > 0 && used >= nudgeAt) return { tone: 'nudge', text: `${left} An account lifts the limit, and Sonic works on your own projects.` };
  return { tone: 'info', text: left };
}

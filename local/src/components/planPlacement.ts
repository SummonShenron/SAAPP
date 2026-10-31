// Where the plan card goes in the conversation (pages/Chat.tsx, ChatMessageList). While an approved plan is being carried out, the page keeps the newest reply pinned in view as its steps, edits and
// answer arrive, so the plan card goes ABOVE that reply: the work happens underneath the plan. At the end of the list (where it used to be) the card was the bottom of the page, every step scrolled to it,
// and the steps and edit cards drawn above it were hidden (Jack, 2026-10-31). A plan that is only proposed, or finished, stays after the reply: it is the thing the reply points at.

/** The id of the message the plan card is drawn above, or null to draw it at the end. Pure. */
export function planAnchorId(messages: ReadonlyArray<{ id: string; sender: string }>, planState: string | null | undefined): string | null {
  if (planState !== 'approved') return null;
  const last = messages[messages.length - 1];
  return last && last.sender === 'ai' ? last.id : null;
}

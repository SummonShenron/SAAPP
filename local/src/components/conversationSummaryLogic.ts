// The pure parts of the "Conversation so far" control (components/ConversationSummaryControl.tsx), kept apart from React so they can be tested. The summary is Sonic's short, quoted record of the older
// part of one conversation (backend/utils/conversation_summary.py); the control lets the person read it, edit it, add to it, and fold the older messages into it on request.

export interface SummaryProgress {
  empty: boolean;
  /** Messages after the last one folded into the summary (the server counts them). */
  unfolded: number;
  /** How many unfolded messages make Sonic fold on its own. */
  fold_at: number;
  /** How many of the newest messages a fold always leaves word for word. */
  keep: number;
}

/** A fold needs at least this many messages besides the ones it leaves alone (the server's MIN_FOLD_MESSAGES). */
export const MIN_FOLD_MESSAGES = 2;

export const itemCount = (view: { sections: { items: unknown[] }[] }): number => view.sections.reduce((sum, section) => sum + section.items.length, 0);

/** Whether a fold would have something to do: enough messages beyond the ones kept word for word. */
export const canCompact = (view: SummaryProgress): boolean => view.unfolded - view.keep >= MIN_FOLD_MESSAGES;

/** The control is shown once there is a summary, or once there is something to fold; before that it would only be noise. */
export const shouldShow = (view: SummaryProgress | null | undefined): boolean => !!view && (!view.empty || canCompact(view));

/** One plain line about where the conversation is: how far from the next automatic fold. */
export const progressText = (view: SummaryProgress): string => {
  if (view.unfolded >= view.fold_at) return 'The next reply folds the older messages into this summary.';
  return `${view.unfolded} of ${view.fold_at} messages kept word for word before the next fold.`;
};

/** What the compact button said happened. */
export const compactMessage = (compacted: boolean): string => (compacted
  ? 'Folded the older messages into the summary.'
  : 'Nothing to fold yet: the newest messages are always kept word for word.');

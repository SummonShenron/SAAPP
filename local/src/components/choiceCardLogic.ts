/**
 * The logic of the choice card (components/ChoiceCard.tsx; logic in choiceCardLogic.ts): one question with a few options, from Sonic's ask_choice tool (backend/services/choice_cards.py).
 * Pure, so it can be tested without a page. The server validates the card first; this only refuses what could not be drawn.
 */
export interface ChoiceOption {
  label: string;
  description: string;
  /** Sonic's recommendation. The server puts it first. */
  recommended: boolean;
}

export interface ChoiceCardData {
  choice_id: string;
  question: string;
  options: ChoiceOption[];
}

/** An option by its position (0 is the first button), or the person's own words. */
export type ChoiceAnswer = { index: number } | { other: string };

/** What the server accepts for the person's own words (backend/services/choice_cards.py MAX_OTHER_CHARS). */
export const MAX_OTHER_CHARS = 500;

/** The card from a `choice_card` event, or null when it is not one that can be drawn (no id, no question, not two to four options). */
export function parseChoiceCard(payload: unknown): ChoiceCardData | null {
  if (!payload || typeof payload !== 'object') return null;
  const raw = payload as Record<string, unknown>;
  const id = typeof raw.choice_id === 'string' ? raw.choice_id : '';
  const question = typeof raw.question === 'string' ? raw.question.trim() : '';
  if (!id || !question || !Array.isArray(raw.options) || raw.options.length < 2 || raw.options.length > 4) return null;
  const options: ChoiceOption[] = [];
  for (const item of raw.options) {
    if (!item || typeof item !== 'object') return null;
    const o = item as Record<string, unknown>;
    const label = typeof o.label === 'string' ? o.label.trim() : '';
    if (!label) return null;
    options.push({ label, description: typeof o.description === 'string' ? o.description.trim() : '', recommended: o.recommended === true });
  }
  return { choice_id: id, question, options };
}

/** The person's own words as they are sent: trimmed, spaces collapsed, cut to what the server accepts. Empty means there is nothing to send. */
export function cleanOther(text: string): string {
  return text.replace(/\s+/g, ' ').trim().slice(0, MAX_OTHER_CHARS);
}

/** The line shown on the card once it is answered: "You chose: Show it at 80%" or "You answered: ...". */
export function answerText(card: ChoiceCardData, answer: ChoiceAnswer): string {
  if ('index' in answer) {
    const option = card.options[answer.index];
    return option ? `You chose: ${option.label}` : 'You answered.';
  }
  return `You answered: ${answer.other}`;
}

// What the welcome screen offers. A card is a question plus an optional small label saying what kind of question it is.
// This module has no imports so it can be tested on its own (src/welcomeCards.test.mjs).

export interface WelcomeCard {
  text: string;
  label?: string;
  /** "attach": the card needs a file first, so it opens the picker (leaving `prompt` in the box) instead of sending its text.
   *  "open": the card is a way into another page (`path`) instead of a question. */
  action?: 'attach' | 'open';
  prompt?: string;
  path?: string;
}

// The portfolio visitor (the shared "guest"; mostly recruiters and hiring managers) is judging the person and the system in half a minute, so the plain
// questions lead and the technical one comes last. The order matters: on a phone only the first card shows. Every question here has to be one the
// portfolio knowledge base can answer.
const PORTFOLIO_CARDS: readonly WelcomeCard[] = [
  { label: 'About Jack', text: 'Who is Jack Harper, and what has he built?' },
  { label: 'About this project', text: 'What is Sonic Assistant, and why did Jack build it?' },
  { label: 'Watch it work', text: 'What was changed in the last pull request?' },
  { label: 'Under the hood', text: "How does Sonic Assistant keep one person's data separate from another's?" },
];

/** The shared portfolio guest. The embedded guest (a different shared identity) keeps its own questions. */
export function isPortfolioGuest(principal: string | null | undefined, embedded: boolean): boolean {
  return principal === 'guest' && !embedded;
}

export function portfolioCards(): WelcomeCard[] {
  return PORTFOLIO_CARDS.map(card => ({ ...card }));
}

const TIDY_CARD: WelcomeCard = { label: 'Try this', text: 'Tidy up a messy folder, like Downloads', action: 'open', path: '/tidy' };

/**
 * Adds the "tidy up a folder" card to a set of cards, for a browser that can open folders. It takes the place of one of the ordinary "Try this" cards
 * (never the photo card), or is added at the end when there is room, so the screen keeps its four cards.
 */
export function withTidyCard(cards: WelcomeCard[], limit = 4): WelcomeCard[] {
  if (cards.some(card => card.action === 'open')) return cards;
  const index = cards.map(card => card.label === 'Try this' && !card.action).lastIndexOf(true);
  if (index >= 0) return cards.map((card, i) => (i === index ? { ...TIDY_CARD } : card));
  return cards.length < limit ? [...cards, { ...TIDY_CARD }] : cards;
}

export function cardsFromQuestions(questions: readonly string[]): WelcomeCard[] {
  return questions.map(text => ({ text }));
}

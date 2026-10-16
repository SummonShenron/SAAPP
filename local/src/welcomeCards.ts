// What the welcome screen offers. A card is a question plus an optional small label saying what kind of question it is.
// This module has no imports so it can be tested on its own (src/welcomeCards.test.mjs).

export interface WelcomeCard {
  text: string;
  label?: string;
  /** "attach": the card needs a file first, so it opens the picker (leaving `prompt` in the box) instead of sending its text. */
  action?: 'attach';
  prompt?: string;
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

export function cardsFromQuestions(questions: readonly string[]): WelcomeCard[] {
  return questions.map(text => ({ text }));
}

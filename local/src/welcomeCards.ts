// What the welcome screen offers. A card is a question plus an optional small label saying what kind of question it is.
// This module has no imports so it can be tested on its own (src/welcomeCards.test.mjs).

export interface WelcomeCard {
  text: string;
  label?: string;
  /** "attach": the card needs a file first, so it opens the picker (leaving `prompt` in the box) instead of sending its text.
   *  "open": the card is a way into another page (`path`) instead of a question. */
  action?: 'attach' | 'open' | 'signup' | 'prefill';
  prompt?: string;
  path?: string;
  /** "signup" cards only: what Sonic does for someone with an account, shown with a sign-up button when the card is chosen (no model call, so it costs nothing). */
  detail?: string;
  /** "signup" cards only: which account feature this is, the same word the server sends in an `account_nudge` event (backend/utils/guest_nudge.py). */
  key?: 'checks' | 'fix' | 'tidy' | 'memory';
  /** A new-account card (the server's `kind`): pressing it is counted by that name (backend/utils/activation.py KINDS). */
  kind?: string;
  /** The card is about the server's demo project: the conversation works on it (the page sends `demo_project` with each turn until another kind of card is chosen). */
  demo?: boolean;
}

// The shared guest ("guest"; strangers from a post or a search, and recruiters) gets three lanes of cards (docs/guest-mode-plan.md). The FRONT lane shows Sonic working: every card is a
// request that makes it call tools (read the code, search the web, run code), so the visitor sees what an account adds before reading a word about the builder. The second lane is the
// answers about the person and the project, for the recruiters. Every card in the second lane has to be answerable by the portfolio knowledge base; every card in the first by the tools
// a guest has (backend/services/agent_workflow.py: a GitHub repo, web search, read a URL, a Python sandbox). The third lane is what a guest cannot do (it needs a folder, the local
// runner or memory, none of which a guest has): its cards send nothing, they say what an account adds and offer sign-up.
export type GuestLane = 'demo' | 'builder' | 'account';

export const GUEST_LANES: readonly { id: GuestLane; label: string }[] = [
  { id: 'demo', label: 'Watch Sonic work' },
  { id: 'builder', label: 'About the builder' },
  { id: 'account', label: 'With an account' },
];

// Three of these work on the demo project, a small made-up invoicing project the server holds for the guest (backend/utils/demo_project.py): `demo: true` tells the server the conversation is
// about it, so the folder tools read and test it and the diff card offers sign-up where Apply would be. Their wording is pinned to the three things planted in it (see test_demo_project.py).
const DEMO_CARDS: readonly WelcomeCard[] = [
  { label: 'Fix a bug', text: 'Invoices go overdue a day late in the demo project. Find the cause, fix it, and show me the tests pass.', demo: true },
  { label: 'Add a feature', text: "Add a late-fee line to the demo project's report and show me the tests pass.", demo: true },
  { label: 'Trace a setting', text: 'Find every place the demo project sets its rate limit and say which one wins.', demo: true },
  { label: 'Watch it work', text: 'What was changed in the last pull request?' },
];

const BUILDER_CARDS: readonly WelcomeCard[] = [
  { label: 'About Jack', text: 'Who is Jack Harper, and what has he built?' },
  { label: 'About this project', text: 'What is Sonic Assistant, and why did Jack build it?' },
  { label: 'The story', text: 'What is the story of the Sonic Assistant?' },
  { label: 'Under the hood', text: "How does Sonic Assistant keep one person's data separate from another's?" },
];

// Each one is a thing only an account can do (a guest has no folder, no runner, no memory: see `apply_sync`, `is_guest_username` in agent_workflow.py). Keep each `detail` true to what ships.
const ACCOUNT_CARDS: readonly WelcomeCard[] = [
  {
    key: 'fix', label: 'Fix a bug', action: 'signup', text: 'Fix the failing test in my project and check the fix.',
    detail: 'Connect a project folder and Sonic reads your code, proposes the fix as a diff you approve, then runs your tests to check it.',
  },
  {
    key: 'checks', label: 'Run your checks', action: 'signup', text: "Run my project's tests and tell me what broke.",
    detail: "Sonic runs your own tests and linters on your computer through a small local program, and asks you before each command that changes anything.",
  },
  {
    key: 'memory', label: 'Be remembered', action: 'signup', text: "Remember that I'm learning Rust and like short answers.",
    detail: 'Sonic keeps what you tell it between conversations and uses it quietly. Every note is on the Memory page, where you can edit or remove it.',
  },
  {
    key: 'tidy', label: 'Tidy a folder', action: 'signup', text: 'Tidy up my messy Downloads folder.',
    detail: 'Pick a folder and Sonic proposes how to sort it into tidy sub-folders. Every move can be undone.',
  },
];

/** The account card for a feature word the server sent (an `account_nudge` event), or null for a word this page does not know. */
export function accountCardFor(key: unknown): WelcomeCard | null {
  const found = ACCOUNT_CARDS.find(card => card.key === key);
  return found ? { ...found } : null;
}

/** The shared portfolio guest. The embedded guest (a different shared identity) keeps its own questions. */
export function isPortfolioGuest(principal: string | null | undefined, embedded: boolean): boolean {
  return principal === 'guest' && !embedded;
}

/** The cards of one lane of the shared guest's welcome screen (the front lane unless asked for the other). A copy, so a caller cannot change what the next visitor sees. */
export function portfolioCards(lane: GuestLane = 'demo'): WelcomeCard[] {
  const cards = lane === 'builder' ? BUILDER_CARDS : lane === 'account' ? ACCOUNT_CARDS : DEMO_CARDS;
  return cards.map(card => ({ ...card }));
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

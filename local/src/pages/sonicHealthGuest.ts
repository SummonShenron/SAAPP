// What the Sonic Health "Turns" tab shows about the guest funnel and the reply voices, from the raw counter totals. Counts only (backend/utils/self_counters.py): which sign-up button was pressed, how many
// free turns the visitor had used, which voice wrote a reply and why. Kept apart from React so the sums and the wording can be tested (sonicHealthGuest.test.mjs).

export type Totals = Record<string, number>;
export interface Row { key: string; label: string; count: number; }

const FEATURE_LABELS: Record<string, string> = { checks: 'Run your checks', fix: 'Fix a bug', tidy: 'Tidy a folder', memory: 'Be remembered' };
const FEATURES = ['checks', 'fix', 'tidy', 'memory'] as const;

/** Every sign-up button a guest page has (backend/utils/guest_signup.py SOURCES), in the order the page shows them, with what each one is. */
export const SIGNUP_SOURCES: ReadonlyArray<readonly [string, string]> = [
  ['demo_diff', "The demo project's diff card (Sign up to apply)"],
  ['limit', 'The refusal when the free turns run out'],
  ['banner', 'The line above the message box'],
  ...FEATURES.map(f => [`nudge_${f}`, `A card under a reply: ${FEATURE_LABELS[f]}`] as const),
  ...FEATURES.map(f => [`card_${f}`, `A card on the "With an account" tab: ${FEATURE_LABELS[f]}`] as const),
];

/** How many free turns the visitor had used that day when they pressed it (backend/utils/guest_signup.py BUCKETS). */
export const SIGNUP_AFTER: ReadonlyArray<readonly [string, string]> = [['none', 'None yet'], ['one_or_two', '1 or 2'], ['three_to_five', '3 to 5'], ['six_plus', '6 or more']];

const count = (totals: Totals, key: string): number => Math.max(0, Math.round(Number(totals[key]) || 0));

export function signupRows(totals: Totals): Row[] {
  return SIGNUP_SOURCES.map(([source, label]) => ({ key: source, label, count: count(totals, `guest_signup_${source}`) }));
}

export function signupAfterRows(totals: Totals): Row[] {
  return SIGNUP_AFTER.map(([bucket, label]) => ({ key: bucket, label, count: count(totals, `guest_signup_after_${bucket}`) }));
}

export const signupTotal = (totals: Totals): number => signupRows(totals).reduce((sum, row) => sum + row.count, 0);

/** Replies where a guest was shown an account card because of what they asked (backend/utils/guest_nudge.py). */
export const nudgesShown = (totals: Totals): number => FEATURES.reduce((sum, feature) => sum + count(totals, `guest_nudge_${feature}`), 0);

/** Which voice wrote each reply and why (backend/utils/reply_tier.py REASONS). The first three are replies a cheaper model wrote; the rest stayed on the standard voice. */
export const VOICE_REASONS: ReadonlyArray<readonly [string, string, boolean]> = [
  ['lite_setting', 'Gemini Lite, their choice', true],
  ['haiku_setting', 'Claude Haiku, their choice', true],
  ['auto_plain', 'Auto: a plain turn, written by the cheaper model', true],
  ['standard_setting', 'The standard voice, their setting', false],
  ['safety', 'Kept on the standard voice: a safety context', false],
  ['emotional', 'Kept on the standard voice: a feeling carried over', false],
  ['checkin', 'Kept on the standard voice: a check-in was due', false],
  ['companion', 'Kept on the standard voice: a companion block was in the prompt', false],
  ['identity', 'Kept on the standard voice: asked about Sonic itself', false],
  ['frustration', 'Kept on the standard voice: blame or frustration', false],
  ['not_offered', 'Chosen, but the server does not offer that model', false],
];

export function voiceRows(totals: Totals): Row[] {
  return VOICE_REASONS.map(([reason, label]) => ({ key: reason, label, count: count(totals, `reply_tier_${reason}`) }));
}

export const voiceTotal = (totals: Totals): number => voiceRows(totals).reduce((sum, row) => sum + row.count, 0);

/** Replies a cheaper model wrote, whether chosen outright or by Auto. */
export const cheaperWrote = (totals: Totals): number => VOICE_REASONS.filter(([, , cheaper]) => cheaper).reduce((sum, [reason]) => sum + count(totals, `reply_tier_${reason}`), 0);

export const share = (part: number, whole: number): string => (whole > 0 ? `${Math.round((part / whole) * 100)}%` : '-');


// ---- new accounts (backend/utils/activation.py) ----------------------------------------------------------------------------------------------------------------------------------------
/** The first steps to value, each counted once per signed-in person (EVENTS), in the order a new account is led through them. */
export const ACTIVATION_STEPS: ReadonlyArray<readonly [string, string]> = [
  ['folder', 'Connected a project folder'],
  ['applied', 'Had a proposed change applied'],
  ['memory', 'Saved a first memory'],
  ['runner', 'Connected a runner'],
  ['document', 'Added a document to their knowledge base'],
];

/** Which new-account welcome card was pressed (KINDS), worded as the card is on the screen. */
export const WELCOME_KINDS: ReadonlyArray<readonly [string, string]> = [
  ['demo', 'Watch Sonic fix a bug in the demo project'],
  ['remember', 'Tell Sonic something to remember'],
  ['runner', "Run my project's checks on my own computer"],
  ['document', 'Add a document to your own knowledge base'],
];

export const activationRows = (totals: Totals): Row[] => ACTIVATION_STEPS.map(([key, label]) => ({ key, label, count: count(totals, `activation_${key}`) }));
export const welcomeRows = (totals: Totals): Row[] => WELCOME_KINDS.map(([key, label]) => ({ key, label, count: count(totals, `welcome_card_${key}`) }));
export const activationTotal = (totals: Totals): number => activationRows(totals).reduce((sum, row) => sum + row.count, 0);
export const welcomeTotal = (totals: Totals): number => welcomeRows(totals).reduce((sum, row) => sum + row.count, 0);

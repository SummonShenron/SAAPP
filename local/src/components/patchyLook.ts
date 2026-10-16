// What Patchy is doing and feeling, as plain functions (no React), so the rules can be tested.
//
// Two separate questions:
//  - the LOOK: what he is doing while a turn runs (thinking, searching, puzzled, writing), taken from the step the backend reports;
//  - the MOOD: the one expression he shows right now, which is the look while a turn runs and otherwise whatever the person's own
//    actions and the usage budget call for (a poke, a failure, typing, a long quiet, a paused chat).

/** How much energy he shows; follows the user's emotional state (the backend's energy tier, backend/utils/emotion_utils.py). */
export type PatchyEnergy = 'open' | 'easing' | 'subdued';

/** What he is doing while a turn runs. 'idle' means no turn is running. */
export type PatchyLook = 'idle' | 'thinking' | 'searching' | 'puzzled' | 'writing';

/** The usage budget's level as far as he is concerned ('off' and unknown count as fine). */
export type PatchyBudget = 'ok' | 'notice' | 'throttled' | 'blocked';

export type PatchyMood = 'laugh' | 'soft' | 'error' | 'active' | 'done' | 'asleep' | 'watching' | 'drowsy' | 'idle';

/** The poses of the right arm (PatchyParts.tsx draws them). */
export type ArmPose = 'hang' | 'type' | 'ready' | 'think' | 'search' | 'puzzle' | 'cheer' | 'hug';

/** The class on a Patchy's root: it picks the accent color and every animation in MiniPatchy.css. */
export function patchyRootClass(p: { mood: PatchyMood; look: PatchyLook; energy: PatchyEnergy; budget: PatchyBudget; steered: boolean; reading?: boolean }): string {
  const key = p.mood === 'active' ? (p.look === 'writing' ? 'working' : p.look) : p.mood;
  return `mp-root mp-${key} mp-e-${p.energy} mp-b-${p.budget}${p.steered ? ' mp-steered' : ''}${p.reading ? ' mp-reading' : ''}`;
}

/** The hover text: what he is doing, or the invitation to poke him (a low person is not invited to). */
export function patchyTitle(p: { mood: PatchyMood; look: PatchyLook; energy: PatchyEnergy }): string {
  if (p.mood === 'laugh') return 'Hehe!';
  if (p.mood === 'error') return 'Something went wrong';
  if (p.mood === 'asleep') return 'Resting while chat is paused';
  if (p.mood === 'active') {
    if (p.look === 'thinking') return 'Thinking…';
    if (p.look === 'searching') return 'Searching…';
    if (p.look === 'puzzled') return 'Hmm, trying another way…';
    return 'Writing…';
  }
  return p.energy === 'subdued' ? 'SAAPP' : 'Poke SAAPP!';
}

/** SMIL ignores prefers-reduced-motion, so the SVG's own animations are only drawn when motion is welcome. */
export function motionAllowed(): boolean {
  return !(typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches);
}

/** The status Chat shows from the moment a message is sent until the backend reports its first step. */
export const TURN_SENT_STATUS = 'Running at the speed of sound';

// Backend graph nodes (the same ids Chat's getNodeLabel knows), grouped by what a person would say Patchy is doing.
const THINKING_NODES = new Set(['coordinator_node', 'reasoner_node', 'memory_node']);
const SEARCHING_NODES = new Set(['retrieve_node', 'grade_documents_node', 'web_search_node', 'github_search', 'pr_summary']);
const PUZZLED_NODES = new Set(['rewrite_query_node', 'reward_evaluator']);

/**
 * The look for the step a running turn is on. Planning steps read as thinking, the searching steps get the magnifying glass, the retry
 * steps (a query rewritten, an answer reconsidered) look puzzled, and everything else (the steps that produce the answer, run code or draft
 * a pull request) is Patchy typing it up.
 */
export function lookForNode(node: string | null | undefined): Exclude<PatchyLook, 'idle'> {
  if (!node || node === TURN_SENT_STATUS || THINKING_NODES.has(node)) return 'thinking';
  if (SEARCHING_NODES.has(node)) return 'searching';
  if (PUZZLED_NODES.has(node)) return 'puzzled';
  return 'writing';
}

/** The budget level as Patchy sees it. */
export function budgetFromLevel(level: string | null | undefined): PatchyBudget {
  return level === 'notice' || level === 'throttled' || level === 'blocked' ? level : 'ok';
}

export interface MoodInputs {
  laughing: boolean;
  nudged: boolean;
  faulted: boolean;
  celebrating: boolean;
  /** The person is engaged with the input (clicked into it or typed, and has not sent or left): he watches it. Not "is typing", which flickers with every pause, and not plain focus, which the page gives the box itself and never takes back. */
  watching: boolean;
  drowsy: boolean;
  look: PatchyLook;
  budget: PatchyBudget;
}

/**
 * The one expression to show. In order: a poke always gets an answer; a failure outranks everything but that; a running turn shows its look;
 * a finished one celebrates; a paused chat (the budget's block) sleeps; engaging with the input makes him watch it; a long quiet makes him drowsy.
 */
export function resolveMood(s: MoodInputs): PatchyMood {
  if (s.laughing) return 'laugh';
  if (s.nudged) return 'soft';
  if (s.faulted) return 'error';
  if (s.look !== 'idle') return 'active';
  if (s.celebrating) return 'done';
  if (s.budget === 'blocked') return 'asleep';
  if (s.watching) return 'watching';
  if (s.drowsy) return 'drowsy';
  return 'idle';
}

// How long each short-lived state lasts, in milliseconds.
export const CELEBRATE_MS = 1600;
export const LAUGH_MS = 1400;
export const NUDGE_MS = 1200;
/** How long the failure face stays before he settles again. */
export const FAULT_MS = 6000;
/** How long the "got it" nod lasts after a message is steered in. */
export const STEER_MS = 1100;
/** A quiet this long and his eyes get heavy. */
export const DROWSY_AFTER_MS = 180_000;

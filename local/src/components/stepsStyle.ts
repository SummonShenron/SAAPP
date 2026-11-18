// Whether the conversation's steps are drawn as a graph (the graph under each step stays, the text sits to the right of it: graphLoader.ts "a graph that stays") or as the plain line with nothing kept.
// It is the way the steps look, not a setting with a control. Below GRAPH_MIN_WIDTH_PX (a phone) the text keeps all the width and the plain line is used. A person who wants the line everywhere can set
// localStorage "stepsStyle" to "line" (nothing in the app writes it): kept so the graph can be switched off without a new build if it ever misbehaves.

export const STEPS_STYLE_KEY = 'stepsStyle';
export const GRAPH_MIN_WIDTH_PX = 700;

/** Graph mode: the window is wide enough and the stored choice is not "line". Pure. */
export function stepsGraphOn(stored: string | null, wide: boolean): boolean {
  return wide && stored !== 'line';
}

/** The stored choice, or null (storage can be blocked or missing: then there is no choice). */
export function readStepsStyle(): string | null {
  try {
    return window.localStorage.getItem(STEPS_STYLE_KEY);
  } catch {
    return null;
  }
}

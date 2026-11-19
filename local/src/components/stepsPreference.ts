// Whether a new turn's steps start open or closed (2026-11-18). What the person last chose with the little arrow on the line of the CURRENT turn's steps is remembered, in the browser and across reloads, and
// applies to every turn after it: collapse them once and the next messages stay collapsed too, until the person opens them again. Pressing the line of an older reply only changes that reply. With nothing chosen
// yet, the newest reply's steps start open (traceFacts.ts stepsAreOpen).

export const STEPS_OPEN_KEY = 'stepsOpen';

/** The stored choice as a yes/no: closed is false, open is true, anything else (nothing stored, or something this page did not write) is no choice. Pure. */
export function parseStepsPreference(stored: string | null): boolean | null {
  return stored === 'closed' ? false : stored === 'open' ? true : null;
}

/** Whether a turn's steps start open: the newest reply's (`current`) do, unless the person has collapsed them. Any other reply starts closed whatever was chosen. Pure. */
export function defaultStepsOpen(current: boolean, preference: boolean | null): boolean {
  return current && preference !== false;
}

/** The stored choice, or null (storage can be blocked or missing: then there is no choice). */
export function readStepsPreference(): boolean | null {
  try {
    return parseStepsPreference(window.localStorage.getItem(STEPS_OPEN_KEY));
  } catch {
    return null;
  }
}

/** Remember the choice. Storage can be blocked: then the choice only lasts for the turn it was made on, which is all it did before. */
export function writeStepsPreference(open: boolean): void {
  try {
    window.localStorage.setItem(STEPS_OPEN_KEY, open ? 'open' : 'closed');
  } catch {
    /* remembering the choice is a convenience */
  }
}

// Small per-browser chat preferences that used to live in the chat box's "..." menu and now live on the Account page. Kept in this browser (storage can be blocked, so a failed read gives the
// default and a failed write just means the choice does not stick).

const SHOW_PATCHY_KEY = 'showPatchy';
/** Fired on the window when the choice changes, so a chat page that is already open picks it up without a reload. */
export const SHOW_PATCHY_EVENT = 'sonic:show-patchy-changed';

/** Whether the little robot (SAAPP) is shown. On unless the person turned him off. */
export function loadShowPatchy(): boolean {
  try {
    return localStorage.getItem(SHOW_PATCHY_KEY) !== 'false';
  } catch {
    return true;
  }
}

export function saveShowPatchy(show: boolean): void {
  try {
    localStorage.setItem(SHOW_PATCHY_KEY, String(show));
  } catch {
    /* not persisted */
  }
  try {
    window.dispatchEvent(new Event(SHOW_PATCHY_EVENT));
  } catch {
    /* no window: nothing is listening */
  }
}

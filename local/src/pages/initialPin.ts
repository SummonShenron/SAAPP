// Landing on the newest message when a thread first fills (a page load or a refresh). pages/Chat.tsx starts this when the message list goes from empty to not empty.
//
// WHY A LOOP, NOT ONE SCROLL: when the thread fills, the chat window may not be mounted yet (the old one-shot found no window and never tried again, which left the thread at the top), and its height keeps
// growing for a moment as the markdown, images and cards lay out (a scroll made before that lands short of the bottom). So for about a second and a half it pins to the bottom on every frame, retrying while
// there is no window, and lets go the moment the person scrolls, touches, clicks or presses a key in it, so it never fights them.

/** What it needs of the chat window (an HTMLElement has all of it). */
export interface PinWindow {
  scrollHeight: number;
  scrollTo(options: { top: number; behavior: ScrollBehavior }): void;
  addEventListener(type: string, listener: () => void, options?: { passive: boolean }): void;
  removeEventListener(type: string, listener: () => void): void;
}

export const PIN_FRAMES = 90;
/** Any of these in the chat window means the person is steering: the pin lets go. (The wheel, a finger, a click or a drag on the scrollbar, the keyboard.) */
export const PIN_STOP_EVENTS: readonly string[] = ['wheel', 'touchstart', 'pointerdown', 'keydown'];

/** Starts pinning; returns the function that stops it (safe to call more than once). `raf`/`caf` are injectable for tests. */
export function startInitialPin(
  getWindow: () => PinWindow | null,
  raf: (callback: () => void) => number = callback => window.requestAnimationFrame(callback),
  caf: (handle: number) => void = handle => window.cancelAnimationFrame(handle),
): () => void {
  let stopped = false;
  let frames = 0;
  let handle = 0;
  let attached: PinWindow | null = null;
  const stop = () => {
    if (stopped) return;
    stopped = true;
    caf(handle);
    if (attached) PIN_STOP_EVENTS.forEach(type => attached!.removeEventListener(type, stop));
  };
  const tick = () => {
    if (stopped) return;
    const win = getWindow();
    if (win) {
      if (!attached) {
        attached = win;
        PIN_STOP_EVENTS.forEach(type => win.addEventListener(type, stop, { passive: true }));
      }
      win.scrollTo({ top: win.scrollHeight, behavior: 'instant' as ScrollBehavior });
    }
    frames += 1;
    if (frames >= PIN_FRAMES) stop();
    else handle = raf(tick);
  };
  handle = raf(tick);
  return stop;
}

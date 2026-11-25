/**
 * Catching up on a reply after the page lost its stream (2026-11-24).
 *
 * The server now finishes a turn whatever happens to the connection (backend/utils/detached_stream.py): closing Safari, the phone suspending the
 * page, a tab switch on a flaky network. So when the stream drops part-way ('interrupted'), or a new message is refused because the last reply is
 * still being written (409), or the page opens on a conversation whose turn is still running, the page does not show an error: it says Sonic is
 * still working, asks /api/chat/turn-status every few seconds, and reloads the conversation once the turn is done. Pure apart from the callbacks.
 */

export const CATCH_UP_INTERVAL_MS = 3000;
export const CATCH_UP_MAX_MS = 10 * 60 * 1000;

export const DROPPED_NOTICE = '_[The connection dropped, but Sonic is still working on this reply. It will appear here when it is done.]_';
export const BUSY_NOTICE = '_[Sonic is still finishing the last reply in this conversation. It will appear here when it is done; then you can send this again.]_';
export const STILL_WORKING_NOTICE = '_[Sonic is still working on this reply. It will appear here when it is done.]_';

/** Whether a failed send means the turn may still be running on the server: the stream dropped part-way, or the server said a turn is still running. */
export function shouldCatchUp(err: { kind?: string; status?: number } | null | undefined): boolean {
  return !!err && (err.kind === 'interrupted' || err.status === 409);
}

export interface WaitOptions {
  intervalMs?: number;
  maxMs?: number;
  sleep?: (ms: number) => Promise<void>;
  now?: () => number;
}

/**
 * Asks `isRunning` until it says false ('finished') or `maxMs` pass ('timeout'). A failed ask (an exception, or null) is not an answer: it is
 * asked again at the next interval.
 */
export async function waitForTurn(isRunning: () => Promise<boolean | null>, options: WaitOptions = {}): Promise<'finished' | 'timeout'> {
  const interval = options.intervalMs ?? CATCH_UP_INTERVAL_MS;
  const max = options.maxMs ?? CATCH_UP_MAX_MS;
  const sleep = options.sleep ?? (ms => new Promise<void>(resolve => setTimeout(resolve, ms)));
  const now = options.now ?? (() => Date.now());
  const started = now();
  for (;;) {
    let running: boolean | null = null;
    try {
      running = await isRunning();
    } catch {
      running = null;
    }
    if (running === false) return 'finished';
    if (now() - started >= max) return 'timeout';
    await sleep(interval);
  }
}

/** The reply's text once the notice is added after whatever had already arrived. */
export function withNotice(existing: string | undefined, notice: string): string {
  return existing && existing.trim() ? `${existing}\n\n${notice}` : notice;
}

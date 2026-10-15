// Waiting until the app is really signed in: Clerk has a session, and the server has accepted its token once.
//
// Until then a message sent from the page goes out with no (or a stale) login token and the server refuses it, which leaves the page in a
// broken half-state. On a phone Clerk's script loads more slowly and the server may still be waking up, so "not yet" is normal and is retried;
// only a server that keeps refusing a token it was given counts as "denied". Plain functions, no React, so they can be tested on their own
// (authReady.test.mjs, run from local/ with `node --test src/utils/authReady.test.mjs`).

/** What one try at checking the session found. */
export type SessionCheck = 'ready' | 'retry' | 'denied';
/** How the whole wait ended. */
export type AuthOutcome = 'ready' | 'denied' | 'timeout' | 'cancelled';

export const DENIED_AFTER = 3; // refusals in a row before it is believed: a refusal can be a token that expired in the browser's queue
export const GIVE_UP_AFTER_MS = 90_000; // a sleeping server takes the better part of a minute to wake
const RETRY_DELAYS_MS = [400, 800, 1500, 2500, 4000];

/** How long to wait before try number `attempt` (0 for the first retry): quick at first, then every four seconds. */
export function retryDelay(attempt: number): number {
  return RETRY_DELAYS_MS[Math.min(Math.max(attempt, 0), RETRY_DELAYS_MS.length - 1)];
}

export interface WaitOptions {
  check: () => Promise<SessionCheck>;
  sleep?: (ms: number) => Promise<void>;
  now?: () => number;
  giveUpAfterMs?: number;
  isCancelled?: () => boolean;
  /** Called after every try that did not finish the wait, so the screen can say how it is going. */
  onTry?: (attempt: number, last: SessionCheck) => void;
}

const realSleep = (ms: number) => new Promise<void>(resolve => setTimeout(resolve, ms));

/** Keeps checking until the session is good, the server keeps refusing it, or it has taken too long. A check that throws counts as "retry". */
export async function waitUntilAuthenticated(options: WaitOptions): Promise<AuthOutcome> {
  const { check, sleep = realSleep, now = Date.now, giveUpAfterMs = GIVE_UP_AFTER_MS, isCancelled = () => false, onTry } = options;
  const started = now();
  let refusals = 0;
  for (let attempt = 0; ; attempt++) {
    if (isCancelled()) return 'cancelled';
    let result: SessionCheck;
    try {
      result = await check();
    } catch {
      result = 'retry';
    }
    if (isCancelled()) return 'cancelled';
    if (result === 'ready') return 'ready';
    refusals = result === 'denied' ? refusals + 1 : 0;
    if (refusals >= DENIED_AFTER) return 'denied';
    if (now() - started >= giveUpAfterMs) return 'timeout';
    onTry?.(attempt, result);
    await sleep(retryDelay(attempt));
  }
}

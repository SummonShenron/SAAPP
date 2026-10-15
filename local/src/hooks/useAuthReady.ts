import { useCallback, useEffect, useState } from 'react';
import { verifySession } from '../api';
import { waitUntilAuthenticated } from '../utils/authReady';

/** 'checking' until the session is confirmed; then 'ready', or one of the two ways it can fail (see utils/authReady.ts). */
export type AuthGateState = 'checking' | 'ready' | 'denied' | 'timeout';

const SLOW_AFTER_MS = 8000; // after this the screen says it may take a while, so a sleeping server does not look like a hang

/**
 * Whether the page may be used yet: Clerk has a session and the server has accepted it. The pages behind it (components/Layout.tsx) do not
 * render, so nothing can be sent or loaded with a login that is not ready: on a phone, where Clerk starts later and the server may be waking,
 * those requests used to go out with no token and fail, and the page was left half working. `skip` is for the embedded widget, which has its own
 * guest identity and never waits.
 */
export function useAuthReady(skip: boolean): { state: AuthGateState; slow: boolean; retry: () => void } {
  const [state, setState] = useState<AuthGateState>(skip ? 'ready' : 'checking');
  const [slow, setSlow] = useState(false);
  const [round, setRound] = useState(0);

  useEffect(() => {
    if (skip) {
      setState('ready');
      return;
    }
    let cancelled = false;
    setState('checking');
    setSlow(false);
    const slowTimer = setTimeout(() => { if (!cancelled) setSlow(true); }, SLOW_AFTER_MS);
    waitUntilAuthenticated({ check: verifySession, isCancelled: () => cancelled }).then(outcome => {
      clearTimeout(slowTimer);
      if (cancelled || outcome === 'cancelled') return;
      setState(outcome);
    });
    return () => {
      cancelled = true;
      clearTimeout(slowTimer);
    };
  }, [skip, round]);

  const retry = useCallback(() => setRound(r => r + 1), []);
  return { state, slow, retry };
}

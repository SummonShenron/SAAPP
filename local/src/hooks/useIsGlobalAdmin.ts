import { useEffect, useState } from 'react';
import { api, getEffectivePrincipal, isGuestPrincipal } from '../api';

// When the admin check fails (an expired token that waited too long in the browser's queue, a server restart in development), ask again a
// few times instead of giving up for the life of the page: each attempt gets a fresh login token. Only the failure case waits.
const RETRY_DELAYS_MS = [1500, 4000, 10000];

/** Whether the signed-in user is in the Global_Admins group. Only decides what to SHOW (a nav link); the server enforces
 *  admin access on every admin endpoint, so a wrong answer here can never expose anything. */
export function useIsGlobalAdmin(enabled: boolean = true): boolean {
  const [isAdmin, setIsAdmin] = useState(false);

  useEffect(() => {
    if (!enabled) return; // not until the person is fully signed in (hooks/useAuthReady.ts)
    const principal = getEffectivePrincipal();
    if (!principal || isGuestPrincipal(principal)) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const attempt = (retriesLeft: number) => {
      api.getUserGroups(principal)
        .then((groups: string[]) => { if (!cancelled) setIsAdmin(Array.isArray(groups) && groups.includes('Global_Admins')); })
        .catch(() => {
          if (cancelled) return;
          setIsAdmin(false);
          if (retriesLeft > 0) {
            timer = setTimeout(() => attempt(retriesLeft - 1), RETRY_DELAYS_MS[RETRY_DELAYS_MS.length - retriesLeft]);
          }
        });
    };
    attempt(RETRY_DELAYS_MS.length);

    return () => { cancelled = true; if (timer) clearTimeout(timer); };
  }, [enabled]);

  return isAdmin;
}

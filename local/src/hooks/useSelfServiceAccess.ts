import { useEffect, useState } from 'react';
import { api, getEffectivePrincipal, isGuestPrincipal } from '../api';
import { canUseSelfService } from '../components/selfServiceAccess';

// Like the admin check (hooks/useIsGlobalAdmin.ts): when the lookup fails, ask again a few times, each with a fresh login token, instead of giving up for the life of the page.
const RETRY_DELAYS_MS = [1500, 4000, 10000];

/** Whether to show the Self Service link in the navigation: the person can add documents to a knowledge base (components/selfServiceAccess.ts). False for a guest without asking, and until the answer is
 *  known, so the link appears a moment after sign-in for someone who has it and never for someone who does not. Only decides what to SHOW; the server enforces who may add or delete documents. */
export function useSelfServiceAccess(enabled: boolean = true): boolean {
  const [allowed, setAllowed] = useState(false);

  useEffect(() => {
    if (!enabled) return undefined;
    const principal = getEffectivePrincipal();
    if (!principal || isGuestPrincipal(principal)) { setAllowed(false); return undefined; }
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const attempt = (retriesLeft: number) => {
      Promise.all([api.getUserGroups(principal), api.getAffiliates(principal)])
        .then(([groups, knowledgeBases]) => {
          if (!cancelled) setAllowed(canUseSelfService(groups, (knowledgeBases ?? []).map(base => base.id)));
        })
        .catch(() => {
          if (cancelled) return;
          setAllowed(false);
          if (retriesLeft > 0) timer = setTimeout(() => attempt(retriesLeft - 1), RETRY_DELAYS_MS[RETRY_DELAYS_MS.length - retriesLeft]);
        });
    };
    attempt(RETRY_DELAYS_MS.length);

    return () => { cancelled = true; if (timer) clearTimeout(timer); };
  }, [enabled]);

  return allowed;
}

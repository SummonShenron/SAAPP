import { useEffect, useState } from 'react';
import { api, getEffectivePrincipal, isGuestPrincipal } from '../api';

/** Whether the signed-in user is in the Global_Admins group. Only decides what to SHOW (a nav link); the server enforces
 *  admin access on every admin endpoint, so a wrong answer here can never expose anything. */
export function useIsGlobalAdmin(): boolean {
  const [isAdmin, setIsAdmin] = useState(false);

  useEffect(() => {
    const principal = getEffectivePrincipal();
    if (!principal || isGuestPrincipal(principal)) return;
    let cancelled = false;
    api.getUserGroups(principal)
      .then((groups: string[]) => { if (!cancelled) setIsAdmin(Array.isArray(groups) && groups.includes('Global_Admins')); })
      .catch(() => { if (!cancelled) setIsAdmin(false); });
    return () => { cancelled = true; };
  }, []);

  return isAdmin;
}

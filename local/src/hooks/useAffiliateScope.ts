import { useEffect, useState } from 'react';
import { useAuth } from '@clerk/clerk-react';
import { api, type KnowledgeBase } from '../api';

interface UseAffiliateScopeArgs {
  enabled: boolean;
  selectedAffiliate: string;
  setSelectedAffiliate: (affiliate: string) => void;
  setAllowedAffiliates: (affs: KnowledgeBase[]) => void;
}

/**
 * Loads the security scopes (affiliates) the current user may use and keeps the selected one
 * valid: guests are pinned to their sandbox scope, signed-in users get whatever the API allows.
 * Previously lived inside Filters.tsx's render component; extracted so the controls can move into
 * the overflow menu without losing this data loading. Disabled in embed mode, where the filter
 * row was never mounted.
 */
export function useAffiliateScope({
  enabled,
  selectedAffiliate,
  setSelectedAffiliate,
  setAllowedAffiliates,
}: UseAffiliateScopeArgs): { fetchingScope: boolean } {
  const { isLoaded, isSignedIn } = useAuth();
  const [fetchingScope, setFetchingScope] = useState<boolean>(enabled);
  const principal = localStorage.getItem('principal') ?? '';

  useEffect(() => {
    if (!enabled) {
      setFetchingScope(false);
      return;
    }

    const isGuest = principal === 'guest' || principal === 'guest_bty';
    const guestScope = principal === 'guest_bty' ? 'Affiliate_D' : 'Affiliate_C';

    // Handle guest flows first; do not wait on Clerk
    if (isGuest) {
      setAllowedAffiliates([{ id: guestScope, display_name: guestScope.replace('_', ' ') }]);
      if (selectedAffiliate === 'All' || selectedAffiliate !== guestScope) {
        setSelectedAffiliate(guestScope);
      }
      setFetchingScope(false);
      return;
    }

    // Non-guest users can wait for Clerk
    if (!isLoaded) {
      setFetchingScope(true);
      return;
    }

    if (!isSignedIn || !principal) {
      setFetchingScope(false);
      return;
    }

    const fetchUserPermissions = async () => {
      setFetchingScope(true);
      try {
        const affiliates = await api.getAffiliates(principal);
        setAllowedAffiliates(affiliates);

        if (affiliates.length > 0 && selectedAffiliate !== 'All' && !affiliates.some(a => a.id === selectedAffiliate)) {
          setSelectedAffiliate(affiliates[0].id);
        }
      } catch (err) {
        console.error('Filter fetch error:', err);
      } finally {
        setFetchingScope(false);
      }
    };

    fetchUserPermissions();
    // selectedAffiliate is deliberately NOT a dependency — including it causes infinite fetch loops.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, principal, isLoaded, isSignedIn, setAllowedAffiliates, setSelectedAffiliate]);

  return { fetchingScope };
}

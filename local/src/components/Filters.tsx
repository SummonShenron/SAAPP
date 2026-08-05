import React, { useEffect, useState } from 'react';
import { api } from '../api';
import { useAuth } from '@clerk/clerk-react';

interface FiltersProps {
  selectedAffiliate: string;
  setSelectedAffiliate: (affiliate: string) => void;
  loadingChat: boolean;
  allowedAffiliates: string[];
  setAllowedAffiliates: (affs: string[]) => void;
}

export const Filters: React.FC<FiltersProps> = ({ 
  selectedAffiliate, 
  setSelectedAffiliate, 
  loadingChat, 
  allowedAffiliates, 
  setAllowedAffiliates 
}) => {
  const { isLoaded, isSignedIn } = useAuth();
  const [fetchingFilters, setFetchingFilters] = useState<boolean>(true);
  const principal = localStorage.getItem('principal') ?? "";

  useEffect(() => {
  const isGuest = principal === 'guest' || principal === 'guest_bty';
  const guestScope = principal === 'guest_bty' ? 'Affiliate_D' : 'Affiliate_C';

  // Handle guest flows first; do not wait on Clerk
  if (isGuest) {
    setAllowedAffiliates([guestScope]);
    if (selectedAffiliate === 'All' || selectedAffiliate !== guestScope) {
      setSelectedAffiliate(guestScope);
    }
    setFetchingFilters(false);
    return;
  }

  // Non-guest users can wait for Clerk
  if (!isLoaded) {
    setFetchingFilters(true);
    return;
  }

  if (!isSignedIn || !principal) {
    setFetchingFilters(false);
    return;
  }

  const fetchUserPermissions = async () => {
    setFetchingFilters(true);
    try {
      const affiliates = await api.getAffiliates(principal);
      setAllowedAffiliates(affiliates);

      if (affiliates.length > 0 && selectedAffiliate !== 'All' && !affiliates.includes(selectedAffiliate)) {
        setSelectedAffiliate(affiliates[0]);
      }
    } catch (err) {
      console.error("Filter fetch error:", err);
    } finally {
      setFetchingFilters(false);
    }
  };

  fetchUserPermissions();
// eslint-disable-next-line react-hooks/exhaustive-deps
}, [principal, isLoaded, isSignedIn, setAllowedAffiliates, setSelectedAffiliate]);
    
  // 3. REMOVE `selectedAffiliate` from this array to prevent infinite fetch loops
  // eslint-disable-next-line react-hooks/exhaustive-deps

  if (fetchingFilters) {
    return <div className="filter-row"><span>Loading security scope...</span></div>;
  }

  return (
    <div className="filter-row">
      <label htmlFor="affiliate-select">Active Security Scope:</label>
      <select 
        id="affiliate-select"
        value={selectedAffiliate} 
        onChange={(e) => setSelectedAffiliate(e.target.value)}
        disabled={loadingChat}
      >
        <option value="All">Select a knowledge base...</option>
        {allowedAffiliates.map((aff) => (
          <option key={aff} value={aff}>{aff}</option>
        ))}
      </select>
    </div>
  );
};
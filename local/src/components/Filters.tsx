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
    // 1. Block until Clerk is fully loaded
    if (!isLoaded) return;
    
    const isGuest = principal === 'guest';
    const hasAuth = isSignedIn || isGuest;
    
    if (!hasAuth || !principal) {
        setFetchingFilters(false);
        return;
    }

    const fetchUserPermissions = async () => {
      setFetchingFilters(true);
      try {
        // 2. Prevent Guests from hitting the secure database endpoint
        if (isGuest) {
          setAllowedAffiliates(['Guest Sandbox']); // Set a default scope for guests
          if (selectedAffiliate === 'All') {
            setSelectedAffiliate('Guest Sandbox');
          }
          return; // Stop execution here!
        }

        console.log("Fetching affiliates for:", principal);
        const affiliates = await api.getAffiliates(principal);
        console.log("Affiliates received:", affiliates);
        
        setAllowedAffiliates(affiliates);

        // Safely update parent state
        if (affiliates.length > 0) {
          if (selectedAffiliate !== 'All' && !affiliates.includes(selectedAffiliate)) {
            setSelectedAffiliate(affiliates[0]);
          }
        }
      } catch (err) {
        console.error("Filter fetch error:", err);
      } finally {
        setFetchingFilters(false);
      }
    };

    fetchUserPermissions();
    
  // 3. REMOVE `selectedAffiliate` from this array to prevent infinite fetch loops
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [principal, isLoaded, isSignedIn, setAllowedAffiliates, setSelectedAffiliate]);

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
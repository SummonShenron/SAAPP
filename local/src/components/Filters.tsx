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
    // Only fetch if Clerk is fully loaded and user is signed in
    if (!isLoaded || !isSignedIn || !principal) {
        if (isLoaded) setFetchingFilters(false);
        return;
    }

    const fetchUserPermissions = async () => {
      setFetchingFilters(true);
      try {
        console.log("Fetching affiliates for:", principal);
        const affiliates = await api.getAffiliates(principal);
        console.log("Affiliates received:", affiliates);
        
        setAllowedAffiliates(affiliates);

        if (affiliates.length > 0) {
          // Only set to the first affiliate if 'All' is currently selected 
          // (which is your default) and you WANT to force a selection.
          // To keep "Select a knowledge base..." as the default, do nothing here.
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
  }, [principal, isLoaded, isSignedIn]); 

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
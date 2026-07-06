import React, { useEffect, useState } from 'react';
import { api } from '../api';

interface FiltersProps {
  selectedAffiliate: string;
  setSelectedAffiliate: (affiliate: string) => void;
  loadingChat: boolean;
  allowedAffiliates: string[];               
  setAllowedAffiliates: (affs: string[]) => void;
}

export const Filters: React.FC<FiltersProps> = ({ selectedAffiliate, setSelectedAffiliate, loadingChat, allowedAffiliates, setAllowedAffiliates }) => {
  const [fetchingFilters, setFetchingFilters] = useState<boolean>(true);
  const principal = localStorage.getItem('principal') ?? "";


  useEffect(() => {
    if (!principal) return;

    const fetchUserPermissions = async () => {
      setFetchingFilters(true);
      try {
        const affiliates = await api.getAffiliates(principal);
        setAllowedAffiliates(affiliates);

        // If they are on 'All' or an invalid affiliate, force-select their first allowed scope
        if (affiliates.length > 0 && selectedAffiliate !== 'All' && !affiliates.includes(selectedAffiliate)) {
          setSelectedAffiliate(affiliates[0]);
        }
      } catch (err) {
        console.error("Metadata filter compilation error:", err);
      } finally {
        setFetchingFilters(false);
      }
    };

    fetchUserPermissions();
  }, [principal, selectedAffiliate, setSelectedAffiliate]);

  if (fetchingFilters) {
    return (
      <div className="filter-row">
        <span style={{ color: '#64748b', fontSize: '0.85rem' }}>Evaluating authorization directory vectors...</span>
      </div>
    );
  }

  return (
    <div className="filter-row">
      <label htmlFor="affiliate-select">Active Security Scope (Entra ID Claims):</label>
      <select 
        id="affiliate-select"
        value={selectedAffiliate} 
        onChange={(e) => setSelectedAffiliate(e.target.value)}
        disabled={loadingChat}
        style={{ color: selectedAffiliate === 'All' ? '#94a3b8' : 'inherit' }} // 💡 Turns select text gray if placeholder is active
      >
        
        <option value="All">
          Select a knowledge base...
        </option>
        
        {allowedAffiliates.map((aff) => (
          <option key={aff} value={aff} style={{ color: 'initial' }}>
            {aff === 'Affiliate_A' ? 'Affiliate Workspace A' : 'Affiliate Workspace B'}
          </option>
        ))}
      </select>
    </div>
  );
};
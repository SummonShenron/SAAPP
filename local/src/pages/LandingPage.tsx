import React, { useState } from 'react';
import sonicImg from '../assets/sonicandshadow.jpg';

interface LandingPageProps {
  onEnter: (username: string) => void;
}

export const LandingPage: React.FC<LandingPageProps> = ({ onEnter }) => {
  // Default to jack_admin but let the reviewer select other personas
  const [selectedPersona, setSelectedPersona] = useState<string>('jack_admin');

  const personas = [
    { id: 'jack_admin', name: 'Jack Harper (Global Admin)', desc: 'Access to both Sonic & Dragon Ball repositories.' },
    { id: 'sonic_user', name: 'Sonic Security Officer', desc: 'Strictly isolated to Workspace A (Sonic Lore).' },
    { id: 'dragon_ball_user', name: 'Z-Fighter Analyst', desc: 'Strictly isolated to Workspace B (Dragon Ball Data).' },
  ];

  const handlePortalEntry = () => {
    // Saves the simulated identity to local storage so Filters.tsx and Chat.tsx can see it
    localStorage.setItem('x-user-id', selectedPersona);
    onEnter(selectedPersona);
  };

  return (
    <div className="landing-page">
      <div className="landing-left">
        <div className="landing-hero-text">
          <h2>SONIC ASSISTANT</h2>
          <h3>RAG Pipeline & Multi-Tenant Data Isolation System.</h3>
          <h4>Built by Jack Harper.</h4>
        </div>

        {/* 🎯 THE PORTFOLIO FIX: Interactive Identity Emulation */}
        <div className="persona-selector-container" style={{ margin: '2rem 0', width: '100%', maxWidth: '400px' }}>
          <label 
            htmlFor="persona-select" 
            style={{ display: 'block', marginBottom: '0.5rem', color: '#94a3b8', fontSize: '0.9rem', fontWeight: 500 }}
          >
            Select Simulated Identity Context (IAM Emulation):
          </label>
          
          <select
            id="persona-select"
            value={selectedPersona}
            onChange={(e) => setSelectedPersona(e.target.value)}
            style={{
              width: '100%',
              padding: '0.75rem',
              borderRadius: '6px',
              backgroundColor: '#1e293b',
              color: '#f8fafc',
              border: '1px solid #334155',
              fontSize: '1rem',
              cursor: 'pointer',
              marginBottom: '1rem'
            }}
          >
            {personas.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>

          <p style={{ color: '#64748b', fontSize: '0.85rem', fontStyle: 'italic', minHeight: '40px' }}>
            {personas.find(p => p.id === selectedPersona)?.desc}
          </p>
        </div>

        <button className="enter-btn" onClick={handlePortalEntry}>
          ENTER
        </button>
      </div>
      <div className="landing-right" style={{ backgroundImage: `url(${sonicImg})` }}>
        {/* Background image panel */}
      </div>
    </div>
  );
};
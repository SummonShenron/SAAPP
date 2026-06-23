import React, { useState, useEffect } from 'react';
import sonicImg from '../assets/sonicandshadow.jpg';

interface LandingPageProps {
  onEnter: (username: string) => void;
}

export const LandingPage: React.FC<LandingPageProps> = ({ onEnter }) => {
  // Default to jack_admin but let the reviewer select other personas
  const [selectedPersona, setSelectedPersona] = useState<string>('jack_admin');
  
  // Dynamic Viewport Detection Hook
  const [isMobile, setIsMobile] = useState<boolean>(window.innerWidth < 768);

  useEffect(() => {
    const handleResize = () => {
      setIsMobile(window.innerWidth < 768);
    };
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, []);

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

  // =========================================================================
  //                          VIEWPORT VIEW RENDERS
  // =========================================================================

  const renderMobileView = () => (
    <div style={{
      display: 'flex',
      flexDirection: 'column',
      justifyContent: 'center',
      alignItems: 'center',
      minHeight: '100vh',
      width: '100vw',
      padding: '20px',
      backgroundColor: '#0f172a', // Clean slate-900 baseline dark
      backgroundImage: `linear-gradient(rgba(15, 23, 42, 0.85), rgba(15, 23, 42, 0.95)), url(${sonicImg})`,
      backgroundSize: 'cover',
      backgroundPosition: 'center',
      textAlign: 'center'
    }}>
      <div style={{
        backgroundColor: 'rgba(30, 41, 59, 0.9)', // slate-800 translucent card
        backdropFilter: 'blur(8px)',
        border: '1px solid #334155',
        borderRadius: '16px',
        padding: '24px 16px',
        width: '100%',
        maxWidth: '360px',
        boxShadow: '0 10px 25px -5px rgba(0, 0, 0, 0.3)'
      }}>
        <div style={{ marginBottom: '24px' }}>
          <h2 style={{ fontSize: '1.75rem', fontWeight: 800, color: '#3b82f6', margin: '0 0 4px 0', letterSpacing: '0.5px' }}>SONIC ASSISTANT</h2>
          <p style={{ fontSize: '0.85rem', color: '#94a3b8', margin: 0 }}>RAG Pipeline & Data Isolation System</p>
          <span style={{ fontSize: '0.75rem', color: '#64748b', display: 'block', marginTop: '2px' }}>By Jack Harper</span>
        </div>

        {/* Interactive Identity Emulation */}
        <div style={{ marginBottom: '24px', textAlign: 'left' }}>
          <label 
            htmlFor="persona-select-mobile" 
            style={{ display: 'block', marginBottom: '8px', color: '#94a3b8', fontSize: '0.85rem', fontWeight: 600 }}
          >
            Simulated Identity Context (IAM)
          </label>
          
          <select
            id="persona-select-mobile"
            value={selectedPersona}
            onChange={(e) => setSelectedPersona(e.target.value)}
            style={{
              width: '100%',
              height: '44px', // 44px tap boundaries
              padding: '0 10px',
              borderRadius: '8px',
              backgroundColor: '#0f172a',
              color: '#f8fafc',
              border: '1px solid #334155',
              fontSize: '16px', // Prevents iOS input auto-zoom
              cursor: 'pointer',
              outline: 'none'
            }}
          >
            {personas.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>

          <div style={{ 
            marginTop: '12px', 
            padding: '10px', 
            borderRadius: '6px', 
            backgroundColor: 'rgba(15, 23, 42, 0.5)',
            border: '1px dashed #334155',
            minHeight: '60px' 
          }}>
            <p style={{ color: '#64748b', fontSize: '0.8rem', fontStyle: 'italic', margin: 0, lineHeight: '1.3' }}>
              {personas.find(p => p.id === selectedPersona)?.desc}
            </p>
          </div>
        </div>

        <button 
          className="enter-btn" 
          onClick={handlePortalEntry}
          style={{
            width: '100%',
            height: '44px',
            borderRadius: '8px',
            backgroundColor: '#3b82f6',
            color: '#fff',
            border: 'none',
            fontSize: '1rem',
            fontWeight: 700,
            cursor: 'pointer',
            transition: 'background-color 0.2s'
          }}
        >
          ENTER PORTAL
        </button>
      </div>
    </div>
  );

  const renderDesktopView = () => (
    <div className="landing-page" style={{ display: 'flex', width: '100vw', height: '100vh' }}>
      <div className="landing-left" style={{ flex: 1, display: 'flex', flexDirection: 'column', justifyContent: 'center', padding: '10% 5%', backgroundColor: 'var(--bg-surface)' }}>
        <div className="landing-hero-text">
          <h2>SONIC ASSISTANT</h2>
          <h3>RAG Pipeline & Multi-Tenant Data Isolation System.</h3>
          <h4>Built by Jack Harper.</h4>
        </div>

        {/* 🎯 THE PORTFOLIO FIX: Interactive Identity Emulation */}
        <div className="persona-selector-container" style={{ margin: '2rem 0', width: '100%', maxWidth: '400px' }}>
          <label 
            htmlFor="persona-select-desktop" 
            style={{ display: 'block', marginBottom: '0.5rem', color: '#94a3b8', fontSize: '0.9rem', fontWeight: 500 }}
          >
            Select Simulated Identity Context (IAM Emulation):
          </label>
          
          <select
            id="persona-select-desktop"
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
              marginBottom: '1rem',
              outline: 'none'
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
      <div className="landing-right" style={{ flex: 1.2, backgroundImage: `url(${sonicImg})`, backgroundSize: 'cover', backgroundPosition: 'center', position: 'relative' }}>
        {/* Background image panel */}
      </div>
    </div>
  );

  return (
    <div style={{ width: '100vw', height: '100vh', overflow: 'hidden' }}>
      {isMobile ? renderMobileView() : renderDesktopView()}
    </div>
  );
};
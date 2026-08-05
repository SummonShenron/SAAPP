// src/components/Layout.tsx
import { Outlet, useNavigate } from "react-router-dom";
import { useState, useEffect } from "react";
import { api } from "../api";
import HelpPanel from "../components/HelpPanel";
import { useAuth } from '@clerk/clerk-react';

interface LayoutProps {
  theme: string;
  toggleTheme: () => void;
  onExit: () => void;
}

export function Layout({ theme, toggleTheme }: LayoutProps) {
  const { isLoaded, isSignedIn } = useAuth();
  const navigate = useNavigate();
  const [isAdmin, setIsAdmin] = useState(false);
  const [showHelp, setShowHelp] = useState(false);
  const [closing, setClosing] = useState(false);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);

  // --- EMBED MODE DETECTION ---
  const hashSearch = window.location.hash.includes('?') ? window.location.hash.split('?')[1] : '';
  const searchParams = new URLSearchParams(window.location.search || hashSearch);
  const isEmbedded = searchParams.get('mode') === 'embed' || window.location.href.includes("mode=embed");

  // 1. AUTO-INJECT GUEST CREDENTIALS FOR EMBED MODE
  useEffect(() => {
    if (isEmbedded) {
      if (localStorage.getItem('guest_token') !== 'guest-sandbox-token') {
        localStorage.setItem('guest_token', 'guest-sandbox-token');
        localStorage.setItem('principal', 'guest');
        localStorage.setItem('x-user-id', 'guest');
      }
    }
  }, [isEmbedded]);

  const toggleHelp = () => {
    setShowHelp(!showHelp);
    setMobileMenuOpen(false);
  };

  const handleLogout = async () => {
    try {
      await api.logout();
    } catch (error) {
      console.error("Logout failed:", error);
    }
  };

  // 2. RUN BACKEND CHECKS AFTER GUEST SESSION IS INJECTED
  useEffect(() => {
    if (!isLoaded) return;
    
    const hasAuth = isSignedIn || !!localStorage.getItem('guest_token') || isEmbedded;
    if (!hasAuth) return;

    const username = localStorage.getItem("principal") || (isEmbedded ? "guest" : "");
    if (username) {
      api.isPaappAdmin(username)
        .then(setIsAdmin)
        .catch((err) => console.warn("Admin check skipped for guest:", err));
    }
  }, [isLoaded, isSignedIn, isEmbedded]);

  const handleNavClick = (path: string) => {
    navigate(path);
    setMobileMenuOpen(false);
  };

  // If in Embed Mode, return ONLY the page content without top menus/sidebars
  if (isEmbedded) {
    return (
      <div className="portal-container bty-embed-container" style={{ minHeight: '100vh', background: '#121316', padding: 0 }}>
        <Outlet />
      </div>
    );
  }

  // Regular Standalone Mode Layout
  return (
    <div className={`portal-container ${theme === "shadow" ? "theme-shadow" : ""}`}>
      <nav className="menu-navigator">
        <div className="nav-logo" onClick={() => navigate("/")}>
          {theme === "sonic" ? "⚡Sonic Assistant" : "⚡Shadow Engine"}
        </div>

        {/* Desktop Links */}
        <div className="nav-links desktop-only">
          <span onClick={toggleTheme} className="theme-toggle-btn">
            {theme === "sonic" ? "Hero" : "Dark"}
          </span>
          <span onClick={() => navigate("/chat")}>Chat</span>
          <span onClick={() => navigate("/saved")}>Saved</span>
          <span onClick={() => navigate("/self-service")}>Self Service</span>
          {isAdmin && <span onClick={() => navigate("/time-tracking")}>Time Tracking</span>}
          <span onClick={() => navigate("/taskboard")}>Taskboard</span>
          <span onClick={() => navigate("/insights")}>Insights</span>
          <span onClick={toggleHelp}>Help</span>
          <span onClick={handleLogout} className="nav-exit">Disconnect</span>
        </div>

        {/* Mobile Quick Actions Header */}
        <div className="mobile-actions-group">
          <span onClick={handleLogout} className="nav-exit">
            Disconnect
          </span>
          <button 
            onClick={() => setMobileMenuOpen(!mobileMenuOpen)}
            className="hamburger-btn"
            aria-label="Toggle menu"
          >
            {mobileMenuOpen ? "✕" : "☰"}
          </button>
        </div>
      </nav>

      {/* Mobile Slide-Down Drawer for Pages */}
      {mobileMenuOpen && (
        <div className="mobile-drawer">
          <div className="mobile-drawer-header">
            <span>Workspace Navigation</span>
            <span onClick={toggleTheme} className="theme-toggle-btn-mobile">
              Mode: {theme === "sonic" ? "⚡ Sonic" : "🌑 Shadow"}
            </span>
          </div>
          <div className="mobile-drawer-links">
            <span onClick={() => handleNavClick("/chat")}>Chat Workspace</span>
            <span onClick={() => handleNavClick("/saved")}>Saved Conversations</span>
            <span onClick={() => handleNavClick("/self-service")}>Self Service</span>
            {isAdmin && <span onClick={() => handleNavClick("/time-tracking")}>⏱ Time Tracking</span>}
            <span onClick={() => handleNavClick("/taskboard")}>Taskboard</span>
            <span onClick={() => handleNavClick("/insights")}>Insights</span>
            <span onClick={() => { toggleHelp(); setMobileMenuOpen(false); }}>Help Panel</span>
          </div>
        </div>
      )}

      {/* Main page content */}
      <Outlet />

      {/* Sidebar panel */}
      {showHelp && (
        <div className={`help-panel-container ${closing ? "closing" : ""}`}>
          <HelpPanel />
        </div>
      )}
    </div>
  );
}
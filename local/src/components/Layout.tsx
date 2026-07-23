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

  useEffect(() => {
    const username = localStorage.getItem("principal");
    if (username) {
      api.isPaappAdmin(username).then(setIsAdmin);
    }
  }, []);

  useEffect(() => {
    // Block execution until Clerk script has fully loaded
    if (!isLoaded) return;
    
    const hasAuth = isSignedIn || !!localStorage.getItem('guest_token');
    if (!hasAuth) return;

    const username = localStorage.getItem("principal");
    if (username) {
      api.isPaappAdmin(username).then(setIsAdmin);
    }
  }, [isLoaded, isSignedIn]);
  const handleNavClick = (path: string) => {
    navigate(path);
    setMobileMenuOpen(false);
  };

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

      {/* 1. Main page content */}
      <Outlet />

      {/* 2. Sidebar panel */}
      {showHelp && (
        <div className={`help-panel-container ${closing ? "closing" : ""}`}>
          <HelpPanel />
        </div>
      )}
    </div>
  );
}
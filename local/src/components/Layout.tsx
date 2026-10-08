// src/components/Layout.tsx
import { Outlet, useNavigate } from "react-router-dom";
import { useState, useEffect } from "react";
import { api, getEffectivePrincipal, isGuestPrincipal } from "../api";
import { useIsGlobalAdmin } from "../hooks/useIsGlobalAdmin";
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
  const [showHelp, setShowHelp] = useState(false);
  const [closing, setClosing] = useState(false);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const isAdmin = useIsGlobalAdmin();

  const HELP_CLOSE_ANIMATION_MS = 200;

  const closeHelp = () => {
    setClosing(true);
    setTimeout(() => {
      setShowHelp(false);
      setClosing(false);
    }, HELP_CLOSE_ANIMATION_MS);
    // Fire-and-forget: dismissing it (by any means) means it should never auto-show again,
    // regardless of whether this was the automatic first-time popup or a manual reopen.
    api.updateHasSeenHelp(true).catch((err) => console.error("Failed to record help panel as seen:", err));
  };

  useEffect(() => {
    if (!showHelp) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") closeHelp();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showHelp]);

  // --- EMBED MODE DETECTION ---
const hashSearch = window.location.hash.includes('?') ? window.location.hash.split('?')[1] : '';
const searchParams = new URLSearchParams(window.location.search || hashSearch);
const isEmbedded = searchParams.get('mode') === 'embed' || window.location.href.includes("mode=embed");

// Bootstrap guest identity synchronously so children see it on first render
if (typeof window !== 'undefined') {
  getEffectivePrincipal();
}
  const toggleHelp = () => {
    if (showHelp) {
      closeHelp();
    } else {
      setShowHelp(true);
    }
    setMobileMenuOpen(false);
  };

  const handleLogout = async () => {
    try {
      await api.logout();
    } catch (error) {
      console.error("Logout failed:", error);
    }
  };

  // Auto-show the help/onboarding overlay once, the first time a user ever signs in — the
  // embed iframe never renders it at all (see the isEmbedded early return below), so skip the
  // check entirely there rather than fetching a setting nothing will use.
  useEffect(() => {
    if (!isLoaded || isEmbedded) return;
    const hasAuth = isSignedIn || !!localStorage.getItem('guest_token');
    if (!hasAuth) return;

    api.getHasSeenHelp()
      .then((data) => {
        if (!data.has_seen_help) setShowHelp(true);
      })
      .catch((err) => console.warn("Could not fetch help-seen setting:", err));
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
      <nav 
        className="menu-navigator"
        onMouseEnter={() => window.dispatchEvent(new CustomEvent("nav-hover", { detail: true }))}
        onMouseLeave={() => window.dispatchEvent(new CustomEvent("nav-hover", { detail: false }))}
      >
        <div className="nav-logo" onClick={() => navigate("/")}>
          {theme === "sonic" ? "Sonic Assistant" : "Sonic Assistant"}
        </div>

        {/* Desktop Links */}
        <div className="nav-links desktop-only">
          <span onClick={toggleTheme} className="theme-toggle-btn">
            {theme === "sonic" ? "Hero" : "Dark"}
          </span>
          <span onClick={() => navigate("/chat")}>Chat</span>
          <span onClick={() => navigate("/self-service")}>Self Service</span>
          {!isGuestPrincipal(getEffectivePrincipal()) && <span onClick={() => navigate("/memory")}>Memory</span>}
          {!isGuestPrincipal(getEffectivePrincipal()) && <span onClick={() => navigate("/integrations")}>Integrations</span>}
          {isAdmin && <span onClick={() => navigate("/sonic-health")}>Sonic Health</span>}
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
            <span onClick={() => handleNavClick("/self-service")}>Self Service</span>
            {!isGuestPrincipal(getEffectivePrincipal()) && <span onClick={() => handleNavClick("/memory")}>Memory</span>}
            {!isGuestPrincipal(getEffectivePrincipal()) && <span onClick={() => handleNavClick("/integrations")}>Integrations</span>}
            {isAdmin && <span onClick={() => handleNavClick("/sonic-health")}>Sonic Health</span>}
            <span onClick={() => { toggleHelp(); setMobileMenuOpen(false); }}>Help Panel</span>
          </div>
        </div>
      )}

      {/* Main page content */}
      <Outlet />

      {/* Help / onboarding overlay */}
      {showHelp && (
        <div
          className={`help-panel-backdrop ${closing ? "closing" : ""}`}
          onClick={closeHelp}
        >
          <div
            className={`help-panel-container ${closing ? "closing" : ""}`}
            onClick={(e) => e.stopPropagation()}
          >
            <HelpPanel onClose={closeHelp} />
          </div>
        </div>
      )}
    </div>
  );
}
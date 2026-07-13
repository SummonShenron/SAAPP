import { Outlet, useNavigate } from "react-router-dom";
import { useState, useEffect } from "react";
import { api } from "../api";
import HelpPanel from "../components/HelpPanel"

interface LayoutProps {
  theme: string;
  toggleTheme: () => void;
  onExit: () => void;
}

export function Layout({ theme, toggleTheme, onExit }: LayoutProps) {
  const navigate = useNavigate();
  const [isAdmin, setIsAdmin] = useState(false);
  const [showHelp, setShowHelp] = useState(false);
  const [closing, setClosing] = useState(false);
  const toggleHelp = () => {
    if (showHelp) {
      setClosing(true);

      setTimeout(() => {
        setShowHelp(false);
        setClosing(false);
      }, 250); // match your CSS animation duration
    } else {
      setShowHelp(true);
    }
  };

  useEffect(() => {
    const username = localStorage.getItem("principal");
    if (username) {
      api.isPaappAdmin(username).then(setIsAdmin);
    }
  }, []);

  return (
    <div className={`portal-container ${theme === "shadow" ? "theme-shadow" : ""}`}>
      <nav className="menu-navigator">
        <div className="nav-logo" onClick={() => navigate("/")}>
          {theme === "sonic" ? "⚡Sonic Assistant" : "⚡Shadow Engine"}
        </div>
        <div className="nav-links">
          <span onClick={toggleTheme} className="theme-toggle-btn">
            {theme === "sonic" ? "Hero" : "Dark"}
          </span>
          <span onClick={() => navigate("/chat")}>Chat</span>
          <span onClick={() => navigate("/saved")}>Saved Conversations</span>
          <span onClick={() => navigate("/self-service")}>Self Service</span>
          {isAdmin && (
            <span onClick={() => navigate("/time-tracking")}>
              Time Tracking
            </span>
          )}
          <span onClick={() => navigate("/taskboard")}>Taskboard</span>
          <span onClick={toggleHelp}>Help</span>
          <span onClick={onExit} className="nav-exit">Disconnect Session</span>
        </div>
      </nav>

      {/* 1. Main page content stays as the second element block */}
      <Outlet />

      {/* 2. Sidebar panel moved down here so it doesn't disrupt child indexing */}
      {showHelp && (
        <div className={`help-panel-container ${closing ? "closing" : ""}`}>
          <HelpPanel />
        </div>
      )}
    </div>
  );
}

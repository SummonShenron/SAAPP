import { Outlet, useNavigate } from "react-router-dom";
import { useState, useEffect } from "react";
import { api } from "../api";

interface LayoutProps {
  theme: string;
  toggleTheme: () => void;
  onExit: () => void;
}

export function Layout({ theme, toggleTheme, onExit }: LayoutProps) {
  const navigate = useNavigate();
  const [isAdmin, setIsAdmin] = useState(false);

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

        
          {isAdmin && (
            <span onClick={() => navigate("/time-tracking")}>
              Time Tracking
            </span>
          )}

          <span onClick={() => navigate("/saved")}>Saved Conversations</span>
          <span onClick={() => navigate("/self-service")}>Self Service</span>

          <span onClick={onExit} className="nav-exit">Disconnect Session</span>
        </div>
      </nav>

      <Outlet />
    </div>
  );
}

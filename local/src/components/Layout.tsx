import { Outlet, useNavigate } from "react-router-dom";

interface LayoutProps {
  theme: string;
  toggleTheme: () => void;
  onExit: () => void;
}

export function Layout({ theme, toggleTheme, onExit }: LayoutProps) {
  const navigate = useNavigate();
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
          <span onClick={() => navigate("/self-service")}>Self Service</span>
          <span onClick={() => navigate("/saved")}>Saved Conversations</span>
          <span onClick={onExit} className="nav-exit">Disconnect Session</span>
        </div>
      </nav>
      {/* This renders the child route */}
      <Outlet />
    </div>
  );
}

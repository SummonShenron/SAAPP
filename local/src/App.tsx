import { Routes, Route, Navigate, useNavigate } from "react-router-dom";
import { useState } from "react";
import { LandingPage } from "./pages/LandingPage";
import { ChatPage } from "./pages/Chat";
import { SelfServicePage } from "./pages/SelfService";
import { MemoryPage } from "./pages/Memory";
import { IntegrationsPage } from "./pages/Integrations";
import { PrivacyPage } from "./pages/Privacy";
import { TermsPage } from "./pages/Terms";
import { Layout } from "../src/components/Layout";
import { api } from "./api";
import { TimeWorkspace } from "./pages/Time"
import { Taskboard } from "./pages/Taskboard"
import InsightsPage from "./pages/InsightsPage";

function App() {
  const navigate = useNavigate();
  // LIFT THEME STATE HERE
  const [theme, setTheme] = useState<"sonic" | "shadow">(() => {
    const saved = localStorage.getItem('theme');
    return saved === "shadow" ? "shadow" : "sonic";
  });
  const toggleTheme = () => {
    const newTheme = theme === "sonic" ? "shadow" : "sonic";
    localStorage.setItem('theme', newTheme);
    setTheme(newTheme);
  };

  return (
    <Routes>
      {/* Landing page (no nav bar) */}
      <Route
        path="/"
        element={
          <LandingPage
            onEnter={async (username: string) => {
              if (!username) {
                alert("Authentication failed: Please select a valid profile.");
                return;
              }
              try {
                const isAuthenticated = await api.verifyIdentity(username);
                if (!isAuthenticated) {
                  alert("Authorization failed: Unknown or unauthorized profile.");
                  return;
                }
                localStorage.setItem("principal", username);
                localStorage.setItem("x-user-id", username);
                navigate("/chat");
              } catch {
                alert("Network error: Could not connect to authorization vault.");
              }
            }}
          />
        }
      />
      {/* Public legal pages — no auth, no nav chrome. Linked from Google's OAuth consent screen. */}
      <Route path="/privacy" element={<PrivacyPage />} />
      <Route path="/terms" element={<TermsPage />} />
      {/* Layout wrapper for all authenticated pages */}
      <Route
        element={
          <Layout
            theme={theme}
            toggleTheme={toggleTheme}
            onExit={() => {
              localStorage.removeItem("principal");
              navigate("/");
            }}
          />
        }
      >
       <Route
        path="/chat"
        element={<ChatPage theme={theme} toggleTheme={toggleTheme} />}
      />
        {/* <Route path="/time-tracking" element={<TimeWorkspace />} /> */}
        <Route path="/self-service" element={<SelfServicePage />} />
        <Route path="/memory" element={<MemoryPage />} />
        <Route path="/integrations" element={<IntegrationsPage />} />
        {/* <Route path="/taskboard" element={<Taskboard />} />
        <Route path="/insights" element={<InsightsPage/>} /> */}

      </Route>
      {/* Catch-all */}
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

export default App;

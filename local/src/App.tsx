import { Routes, Route, Navigate, useNavigate } from "react-router-dom";
import { useState } from "react";
import { LandingPage } from "./pages/LandingPage";
import { ChatPage } from "./pages/Chat";
import { SelfServicePage } from "./pages/SelfService";
import { SavedConversationsPage } from "./pages/SavedConversations";
import { Layout } from "../src/components/Layout";
import { api } from "./api";

function App() {
  const navigate = useNavigate();
  // LIFT THEME STATE HERE
  const [theme, setTheme] = useState<"sonic" | "shadow">("sonic");
  const toggleTheme = () => setTheme(theme === "sonic" ? "shadow" : "sonic");
  const username = localStorage.getItem("x-user-id") || "";

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
                localStorage.setItem("x-user-id", username);
                navigate("/chat");
              } catch {
                alert("Network error: Could not connect to authorization vault.");
              }
            }}
          />
        }
      />
      {/* Layout wrapper for all authenticated pages */}
      <Route
        element={
          <Layout
            theme={theme}
            toggleTheme={toggleTheme}
            onExit={() => {
              localStorage.removeItem("x-user-id");
              navigate("/");
            }}
          />
        }
      >
       <Route
        path="/chat"
        element={<ChatPage theme={theme} toggleTheme={toggleTheme} />}
      />
        <Route path="/self-service" element={<SelfServicePage />} />
        <Route path="/saved" element={<SavedConversationsPage username={username} />} />
      </Route>
      {/* Catch-all */}
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

export default App;

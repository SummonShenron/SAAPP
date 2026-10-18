import { Routes, Route, Navigate, useNavigate } from "react-router-dom";
import { lazy, Suspense, useState } from "react";
import { LandingPage } from "./pages/LandingPage";
import { ChatPage } from "./pages/Chat";
import { Layout } from "../src/components/Layout";
import { api } from "./api";

// The chat page and the landing page are what almost everyone opens first, so they travel in the main file. Every other page is fetched the first time
// it is opened, which keeps the admin dashboards and settings pages out of the download a visitor waits for before anything appears.
const SelfServicePage = lazy(() => import("./pages/SelfService").then(m => ({ default: m.SelfServicePage })));
const TidyPage = lazy(() => import("./pages/Tidy").then(m => ({ default: m.TidyPage })));
const MemoryPage = lazy(() => import("./pages/Memory").then(m => ({ default: m.MemoryPage })));
const TasksPage = lazy(() => import("./pages/Tasks").then(m => ({ default: m.TasksPage })));
const IntegrationsPage = lazy(() => import("./pages/Integrations").then(m => ({ default: m.IntegrationsPage })));
const AccountPage = lazy(() => import("./pages/Account").then(m => ({ default: m.AccountPage })));
const SonicHealthPage = lazy(() => import("./pages/SonicHealth").then(m => ({ default: m.SonicHealthPage })));
const PrivacyPage = lazy(() => import("./pages/Privacy").then(m => ({ default: m.PrivacyPage })));
const TermsPage = lazy(() => import("./pages/Terms").then(m => ({ default: m.TermsPage })));

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
    <Suspense fallback={null}>
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
        <Route path="/self-service" element={<SelfServicePage />} />
        <Route path="/tidy" element={<TidyPage />} />
        <Route path="/memory" element={<MemoryPage />} />
        <Route path="/tasks" element={<TasksPage />} />
        <Route path="/integrations" element={<IntegrationsPage />} />
        <Route path="/account" element={<AccountPage theme={theme} toggleTheme={toggleTheme} />} />
        {/* Admin only: the server enforces Global_Admins on every endpoint this page calls. */}
        <Route path="/sonic-health/:tab?" element={<SonicHealthPage />} />
      </Route>
      {/* Catch-all */}
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
    </Suspense>
  );
}

export default App;

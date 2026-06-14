import { LandingPage } from './pages/LandingPage';
import { ChatPage } from './pages/Chat';
import { Routes, Route, Navigate, useNavigate } from 'react-router-dom';
import { api } from './api'; // 🔗 Import your centralized API layer

function App() {
  const navigate = useNavigate();

  return (
    <Routes>
      {/* 1. Landing Page: Session validation completely delegated to the secure backend */}
      <Route path="/" element={
        <LandingPage onEnter={async (username: string) => {
          // Rule 1: Fast client check for empty payloads to save network overhead
          if (!username) {
            alert("Authentication failed: Please select a valid profile.");
            return;
          }

          try {
            // Rule 2: Hand identity evaluation completely off to the backend server
            const isAuthenticated = await api.verifyIdentity(username);

            if (!isAuthenticated) {
              console.error(`Security Guard: Access Denied. Backend rejected identity context: [${username}]`);
              alert("Authorization failed: Unknown or unauthorized profile.");
              return; // Halt execution. Do not navigate to workspace.
            }

            // Identity successfully validated by backend authority. Set secure session tracking state.
            localStorage.setItem('x-user-id', username);
            navigate('/chat');
            
          } catch (err) {
            console.error("Auth server connection fault:", err);
            alert("Network error: Could not establish connection to the authorization vault.");
          }
        }} />
      } />

      {/* 2. The Main Chat Portal Workspace */}
      <Route path="/chat" element={
        <ChatPage onExit={() => {
          localStorage.removeItem('x-user-id'); // Completely purge session tracking token
          navigate('/');
        }} />
      } />

      {/* 3. Catch-all safety net redirection */}
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

export default App;
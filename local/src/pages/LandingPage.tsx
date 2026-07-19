import React, { useEffect } from 'react';
import { useSignIn, useAuth, useClerk } from '@clerk/clerk-react';
import { useNavigate } from 'react-router-dom'; // or your custom router hook
import sonicImg from '../assets/sonicandshadow.jpg';
import { getMe } from '../api'

interface LandingPageProps {
  onEnter: (username: string) => void;
}

export const LandingPage: React.FC<LandingPageProps> = ({ onEnter }) => {
  const { signIn, isLoaded: isSignInLoaded } = useSignIn();
  const { openSignIn } = useClerk();
  const { userId, isSignedIn, isLoaded: isAuthLoaded } = useAuth();
  const navigate = useNavigate();

  // 1. Clerk Auto-redirect Hook
  // If Clerk detects they successfully logged in via Google/Email, 
  // automatically push them into the app with their real username/email!
  useEffect(() => {
  // Define an async helper function inside the hook
  const initializeUser = async () => {
    if (isAuthLoaded && isSignedIn && userId) {
      try {
        console.log("Fetching user profile for:", userId);
        // This uses the Clerk JWT automatically via getAuthHeaders()
        const userProfile = await getMe(userId); 
        
        // If this succeeds, update storage and trigger navigation
        localStorage.setItem('principal', userProfile.username);
        localStorage.setItem('x-user-id', userProfile.username);
        onEnter(userProfile.username);
      } catch (err) {
        console.error("Backend does not recognize this Clerk user:", err);
        // Handle error (e.g., user is valid in Clerk but missing from your DB)
      }
    }
  };

  // Execute the async function
  initializeUser();

}, [isAuthLoaded, isSignedIn, userId, onEnter]);
  // 2. The Guest Sandbox Handler
  const handleGuestEntry = () => {
    localStorage.setItem('guest_token', 'guest-sandbox-token');
    
    // Change both of these to 'guest' to match your MongoDB 'username' field!
    localStorage.setItem('principal', 'guest');
    localStorage.setItem('x-user-id', 'guest');
    
    onEnter('guest');
  };

  // 3. Trigger Google OAuth via Clerk
  const handleGoogleLogin = async () => {
    if (!isSignInLoaded) return;
    try {
      await signIn.authenticateWithRedirect({
        strategy: 'oauth_google',
        redirectUrl: '/sso-callback', // Set this up in Clerk Dashboard redirects
        redirectUrlComplete: '/'
      });
    } catch (err) {
      console.error("Error signing in with Google:", err);
    }
  };

  const handleLogin = () => {
    openSignIn({
      // Keeps users on the same page after they finish logging in
      fallbackRedirectUrl: window.location.origin
    });
  };

  useEffect(() => {
  if (isAuthLoaded && isSignedIn) {
    // Navigate to chat immediately if user is already signed in
    navigate("/chat"); // Replace with your actual chat route
  }
}, [isAuthLoaded, isSignedIn, navigate]);

  return (
    <div className="landing-page">
      <div className="landing-left">
        <div className="landing-hero-text">
          <h2>SONIC ASSISTANT</h2>
          <h3>RAG Pipeline & Multi-Tenant Data Isolation System.</h3>
          <h4>Built by Jack Harper.</h4>
        </div>

        {/* THE HYBRID PORTAL GATEWAY */}
        <div className="login-actions-container" style={{ margin: '3rem 0', width: '100%', maxWidth: '350px', display: 'flex', flexDirection: 'column', gap: '1rem' }}>
          
          {/* Real Auth Option */}
          <button 
            className="enter-btn login-btn" 
            onClick={handleLogin}
            style={{
              backgroundColor: '#4285F4',
              color: 'white',
              border: 'none',
              padding: '0.85rem',
              borderRadius: '6px',
              fontWeight: 600,
              cursor: 'pointer'
            }}
          >
            Sign In / Sign Up
          </button>

          <div style={{ display: 'flex', alignItems: 'center', color: '#64748b', fontSize: '0.85rem', margin: '0.5rem 0' }}>
            <div style={{ flex: 1, height: '1px', backgroundColor: '#334155' }}></div>
            <span style={{ padding: '0 10px' }}>OR</span>
            <div style={{ flex: 1, height: '1px', backgroundColor: '#334155' }}></div>
          </div>

          {/* Frictionless Portfolio Option */}
          <button 
            className="enter-btn guest-btn" 
            onClick={handleGuestEntry}
            style={{
              backgroundColor: '#1e293b',
              color: '#f8fafc',
              border: '1px solid #334155',
              padding: '0.85rem',
              borderRadius: '6px',
              fontWeight: 600,
              cursor: 'pointer',
              transition: 'all 0.2s'
            }}
          >
            Sign in as Guest (One-Click Sandbox)
          </button>
          
          <p style={{ color: '#64748b', fontSize: '0.8rem', textAlign: 'center', lineHeight: '1.4' }}>
            Guest mode provides interactive, safe access to sandboxed features and Jack's portfolio knowledge base.
          </p>
        </div>
      </div>
      <div className="landing-right" style={{ backgroundImage: `url(${sonicImg})` }}>
        {/* Background image panel[cite: 1] */}
      </div>
    </div>
  );
};
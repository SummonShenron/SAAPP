import React, { useEffect, useState } from 'react';
import { useSignIn, useAuth, useClerk } from '@clerk/clerk-react';
import { useNavigate } from 'react-router-dom'; // or your custom router hook
import sonicImg from '../assets/sonicandshadow.jpg';
import { getMe } from '../api'
import sonicSpinImg from '../assets/sonic-rolling.gif';
import shadowSpinImg from '../assets/shadow.gif';
interface LandingPageProps {
  onEnter: (username: string) => void;
}

export const LandingPage: React.FC<LandingPageProps> = ({ onEnter }) => {
  const { signIn, isLoaded: isSignInLoaded } = useSignIn();
  const { openSignIn } = useClerk();
  const { userId, isSignedIn, isLoaded: isAuthLoaded } = useAuth();
  const navigate = useNavigate();
  const [showNotice, setShowNotice] = useState(true);
  const [serverStarting, setServerStarting] = useState(false);
  const handleDismiss = () => {
    setShowNotice(false);
    localStorage.setItem('hideNotice', '1');
  };
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
    localStorage.setItem('principal', 'guest');
    localStorage.setItem('x-user-id', 'guest');

    setServerStarting(true);   // show the "Starting server…" indicator
    onEnter('guest');          // existing navigation / entry logic
  };

  const handleGoogleLogin = async () => {
    if (!isSignInLoaded) return;
    try {
      setServerStarting(true);
      await signIn.authenticateWithRedirect({
        strategy: 'oauth_google',
        redirectUrl: '/sso-callback',
        redirectUrlComplete: '/'
      });
    } catch (err) {
      console.error("Error signing in with Google:", err);
      setServerStarting(false);
    }
  };

  const handleLogin = () => {
    openSignIn({ fallbackRedirectUrl: window.location.origin });
  };

  useEffect(() => {
    if (isAuthLoaded && isSignedIn) {
      navigate("/chat");
    }
  }, [isAuthLoaded, isSignedIn, navigate]);

  useEffect(() => {
    // clear serverStarting if chat already marked server started
    if (localStorage.getItem('serverStarted')) {
      setServerStarting(false);
      localStorage.removeItem('serverStarted');
    }

    // auto-clear spinner after 90s to avoid indefinite state
    let t: number | undefined;
    if (serverStarting) {
      t = window.setTimeout(() => {
        setServerStarting(false);
      }, 90000);
    }
    return () => { if (t) clearTimeout(t); };
  }, [serverStarting]);

  useEffect(() => {
    if (localStorage.getItem('hideNotice')) setShowNotice(false);
  }, []);

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
          {/* Affiliate access note (place after Sign In button) */}
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 12,
              marginTop: 8,
              fontSize: '0.85rem',
              color: '#64748b',
              width: '100%',
              justifyContent: 'space-between',
              flexWrap: 'wrap'
            }}
          >
            <div style={{ flex: '1 1 auto', minWidth: 180 }}>
              <strong style={{ color: '#111827' }}>Affiliate access</strong>
              <div style={{ marginTop: 4 }}>
                <span style={{ display: 'inline-block', marginRight: 10 }}>
                  <strong>A</strong> — Sonic docs
                </span>
                <span style={{ display: 'inline-block', marginRight: 10 }}>
                  <strong>B</strong> — Dragon Ball docs
                </span>
                <span style={{ display: 'inline-block' }}>
                  <strong>C</strong> — Jack's portfolio
                </span>
              </div>
            </div>
          </div>
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
          
          {/* Guest mode description */}
          <p style={{ color: '#64748b', fontSize: '0.8rem', textAlign: 'center', lineHeight: '1.4' }}>
            Guest mode provides interactive, safe access to sandboxed features and Jack's portfolio knowledge base.
          </p>

          {/* Disclaimer Banner */}
          {showNotice && (
            <div style={{
              marginTop: '1rem',
              padding: '0.9rem',
              borderRadius: 8,
              background: 'linear-gradient(90deg, rgba(255,249,230,1) 0%, rgba(255,243,230,1) 100%)',
              border: '1px solid #f59e0b',
              color: '#92400e',
              maxWidth: 420,
              marginLeft: 'auto',
              marginRight: 'auto',
              textAlign: 'left'
            }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'flex-start' }}>
                <div style={{ flex: 1 }}>
                  <strong>Notice</strong>
                  <div style={{ fontSize: '0.85rem', marginTop: 6 }}>
                    Services may take a moment to start. First request after inactivity can be slow while the server spins up.
                  </div>

                  <details style={{ marginTop: 8, fontSize: '0.82rem', color: '#6b7280' }}>
                    <summary style={{ cursor: 'pointer' }}>Learn more</summary>
                    <div style={{ marginTop: 8 }}>
                      We run on cost‑sensitive infrastructure and use third‑party LLMs on free tiers. This can cause longer response times or temporary unavailability during peak demand. If the app appears to hang after signing in, wait 30–60 seconds and try again. You may be redirected to the chat page while the backend starts.
                    </div>
                  </details>
                </div>

                <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 8 }}>
                  <button
                    onClick={() => setShowNotice(false)}
                    aria-label="Dismiss notice"
                    style={{
                      background: 'transparent',
                      border: 'none',
                      color: '#92400e',
                      cursor: 'pointer',
                      fontSize: '0.9rem'
                    }}
                  >
                    Dismiss
                  </button>

                  {/* Inline server starting indicator */}
                  {serverStarting ? (
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      {/* reuse your existing spinner image if you prefer */}
                      <img src={sonicSpinImg} alt="starting" style={{ width: 18, height: 18 }} />
                      <span style={{ fontSize: '0.82rem', color: '#6b7280' }}>Starting server…</span>
                    </div>
                  ) : null}
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
      <div className="landing-right" style={{ backgroundImage: `url(${sonicImg})` }}>
        {/* Background image panel[cite: 1] */}
      </div>
    </div>
  );
};
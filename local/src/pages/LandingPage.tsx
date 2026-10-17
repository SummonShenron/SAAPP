import React, { useEffect, useState } from 'react';
import { useSignIn, useAuth, useClerk } from '@clerk/clerk-react';
import { useNavigate } from 'react-router-dom';
import LogoMark from '../components/LogoMark';
import PatchyAtLaptop from '../components/PatchyAtLaptop';
import { getMe, logLogin } from '../api';
import Spinner from '../components/Spinner';

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

  // 1. Unified Clerk Auth Initialization & Single Logging Call
  useEffect(() => {
    const initializeUser = async () => {
      if (isAuthLoaded && isSignedIn && userId) {
        try {
          console.log("Fetching user profile for:", userId);
          
          await new Promise((resolve) => setTimeout(resolve, 300));

          const clerkEmail = (window as any)?.Clerk?.user?.primaryEmailAddress?.emailAddress || (window as any)?.Clerk?.user?.emailAddresses?.[0]?.emailAddress || userId;
          const userProfile = await getMe(clerkEmail);

          const canonicalPrincipal = (userProfile.email || userProfile.username || clerkEmail || userId).trim();
          localStorage.setItem('principal', canonicalPrincipal);
          localStorage.setItem('x-user-id', canonicalPrincipal);

          if (!sessionStorage.getItem('login_logged')) {
            await logLogin();
            sessionStorage.setItem('login_logged', 'true');
          }

          onEnter(userProfile.username);
          navigate("/chat");
        } catch (err) {
          console.error("Backend error initializing Clerk user:", err);
        }
      }
    };

    initializeUser();
  }, [isAuthLoaded, isSignedIn, userId, onEnter, navigate]);

  // 2. Guest Sandbox Entry
  const handleGuestEntry = async () => {
    setServerStarting(true);
    localStorage.setItem('guest_token', 'guest-sandbox-token');
    localStorage.setItem('principal', 'guest');
    localStorage.setItem('x-user-id', 'guest');

    await logLogin();

    onEnter('guest');
    navigate("/chat");
  };

  const handleLogin = () => {
    openSignIn({ fallbackRedirectUrl: window.location.origin });
  };

  useEffect(() => {
    if (localStorage.getItem('hideNotice')) setShowNotice(false);
  }, []);

  useEffect(() => {
    if (localStorage.getItem('serverStarted')) {
      setServerStarting(false);
      localStorage.removeItem('serverStarted');
    }

    let t: number | undefined;
    if (serverStarting) {
      t = window.setTimeout(() => setServerStarting(false), 90000);
    }
    return () => { if (t) clearTimeout(t); };
  }, [serverStarting]);

  return (
    <div className="landing-page">
      {/* Patchy at his laptop, sitting on the bottom edge of this panel */}
      <div className="landing-top">
        <PatchyAtLaptop />
      </div>

      {/* Below him, two panels: the title and sign in (with the notice under it) on the left, the logo and the guest sandbox on the right.
          (On a phone the panels dissolve into one column; the order of that column is in index.css.) */}
      <div className="landing-row">
        <div className="landing-left">
          <div className="landing-id landing-hero-text">
            <h2>SONIC ASSISTANT</h2>
            <h3>RAG Pipeline & Multi-Tenant Data Isolation System.</h3>
            <h4>Built by Jack Harper.</h4>
          </div>

          <div className="landing-signin">
            <button
              className="enter-btn login-btn"
              onClick={handleLogin}
              style={{
                backgroundColor: 'var(--ld-btn-bg)',
                color: 'var(--ld-btn-ink)',
                border: 'none',
                padding: '0.85rem',
                borderRadius: '6px',
                fontWeight: 600,
                cursor: 'pointer'
              }}
            >
              Sign In / Sign Up
            </button>

            <div className="landing-affiliate">
              <strong style={{ color: 'var(--landing-ink)' }}>Affiliate access</strong>
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

        {showNotice && (
          <div className="landing-notice">
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'flex-start' }}>
              <div style={{ flex: 1 }}>
                <strong>Notice</strong>
                <div style={{ fontSize: '0.85rem', marginTop: 6 }}>
                  Services may take a moment to start. First request after inactivity can be slow while the server spins up.
                </div>

                <details style={{ marginTop: 8, fontSize: '0.82rem', color: '#a8a29e' }}>
                  <summary style={{ cursor: 'pointer' }}>Learn more</summary>
                  <div style={{ marginTop: 8 }}>
                    We run on cost‑sensitive infrastructure and use third‑party LLMs on free tiers. This can cause longer response times or temporary unavailability during peak demand. If the app appears to hang after signing in, wait 30–60 seconds and try again. You may be redirected to the chat page while the backend starts.
                  </div>
                </details>
              </div>

              <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 8 }}>
                <button
                  onClick={() => {
                    setShowNotice(false);
                    localStorage.setItem('hideNotice', '1');
                  }}
                  aria-label="Dismiss notice"
                  style={{
                    background: 'transparent',
                    border: 'none',
                    color: '#f0e2d4',
                    cursor: 'pointer',
                    fontSize: '0.9rem'
                  }}
                >
                  Dismiss
                </button>

                {serverStarting && (
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <Spinner size={18} />
                    <span style={{ fontSize: '0.82rem', color: '#a8a29e' }}>Starting server…</span>
                  </div>
                )}
              </div>
            </div>
          </div>
        )}

          <div className="landing-links">
            <a href="#/privacy">Privacy Policy</a>
            <a href="#/terms">Terms of Service</a>
          </div>
        </div>

        <div className="landing-right">
          <LogoMark className="landing-logo" variant="mono" size={64} label="Sonic Graph Technologies" />
          <div className="landing-name">Sonic Graph Technologies</div>

          <div className="landing-guest">
            <button
              className="enter-btn guest-btn"
              onClick={handleGuestEntry}
              style={{
                backgroundColor: 'transparent',
                color: 'var(--landing-ink)',
                border: '1px solid var(--landing-ink)',
                padding: '0.85rem',
                borderRadius: '6px',
                fontWeight: 600,
                cursor: 'pointer',
                transition: 'all 0.2s'
              }}
            >
              Sign in as Guest (One-Click Sandbox)
            </button>

            <p className="landing-guest-note">
              Guest mode provides interactive, safe access to sandboxed features and Jack's portfolio knowledge base.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
};

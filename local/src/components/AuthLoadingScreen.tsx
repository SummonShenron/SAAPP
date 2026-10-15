import React from 'react';
import { useNavigate } from 'react-router-dom';
import Spinner from './Spinner';
import type { AuthGateState } from '../hooks/useAuthReady';
import './__styles__/AuthLoadingScreen.css';

/**
 * Shown in place of the page until the person is fully signed in (hooks/useAuthReady.ts), so nothing can be sent or loaded with a login that
 * is not ready. It says what is happening: first "Signing you in", then, if it is taking a while (a phone, or a server that was asleep), that
 * it may take up to a minute, and if it never works, what to do about it.
 */
const AuthLoadingScreen: React.FC<{ state: Exclude<AuthGateState, 'ready'>; slow: boolean; onRetry: () => void }> = ({ state, slow, onRetry }) => {
  const navigate = useNavigate();
  const failed = state !== 'checking';
  const title = state === 'checking' ? 'Signing you in…' : state === 'denied' ? 'Your sign-in was not accepted' : "Couldn't reach the server";
  const detail =
    state === 'checking'
      ? slow
        ? 'Still connecting. If the server was asleep this can take up to a minute.'
        : 'Just a moment while your session is confirmed.'
      : state === 'denied'
        ? 'Your session may have expired. Try again, or go back and sign in again.'
        : 'It took too long to get an answer. Check your connection and try again.';

  return (
    <main className="auth-loading" role="status" aria-live="polite" aria-busy={!failed}>
      {!failed && <Spinner size={56} />}
      <h2 className="auth-loading-title">{title}</h2>
      <p className="auth-loading-detail">{detail}</p>
      {failed && (
        <div className="auth-loading-actions">
          <button type="button" className="auth-loading-btn" onClick={onRetry}>Try again</button>
          {state === 'denied' && (
            <button type="button" className="auth-loading-btn auth-loading-btn-quiet" onClick={() => navigate('/')}>Back to sign in</button>
          )}
        </div>
      )}
    </main>
  );
};

export default AuthLoadingScreen;

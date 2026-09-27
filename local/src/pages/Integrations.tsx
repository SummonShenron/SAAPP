import React, { useState, useEffect } from 'react';
import { api, getEffectivePrincipal, isGuestPrincipal, type CalendarConnectionStatus } from '../api';
import '../pages/__styles__/SelfService.css';

// Reads query params off the hash fragment (HashRouter — see local/src/main.tsx — puts the route
// after '#', so a post-redirect '?connected=google' lands in location.hash, not location.search).
// Same idiom already used by getEmbeddedMode()/Layout.tsx/Chat.tsx for embed-mode detection.
function getHashQueryParams(): URLSearchParams {
  const hashSearch = window.location.hash.includes('?') ? window.location.hash.split('?')[1] : '';
  return new URLSearchParams(window.location.search || hashSearch);
}

export const IntegrationsPage: React.FC = () => {
  const principal = getEffectivePrincipal();
  const isGuest = isGuestPrincipal(principal);

  const [status, setStatus] = useState<CalendarConnectionStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [connecting, setConnecting] = useState(false);
  const [disconnecting, setDisconnecting] = useState(false);
  const [redirectBanner, setRedirectBanner] = useState<{ success: boolean; message: string } | null>(null);

  const refreshStatus = async () => {
    try {
      const result = await api.getCalendarStatus();
      setStatus(result);
    } catch (err) {
      console.error('Failed to fetch Google Calendar connection status:', err);
    }
  };

  useEffect(() => {
    const params = getHashQueryParams();
    if (params.get('connected') === 'google') {
      setRedirectBanner({ success: true, message: 'Google Calendar connected successfully.' });
    } else if (params.get('calendar_error')) {
      const reason = params.get('calendar_error');
      const message =
        reason === 'access_denied'
          ? 'Google authorization was cancelled.'
          : reason === 'expired'
          ? 'That connection attempt expired — please try again.'
          : 'Could not complete the Google Calendar connection — please try again.';
      setRedirectBanner({ success: false, message });
    }

    refreshStatus().finally(() => setLoading(false));
  }, []);

  const handleConnect = async () => {
    setConnecting(true);
    setRedirectBanner(null);
    try {
      const { authorization_url } = await api.startCalendarConnection(window.location.href);
      window.location.href = authorization_url;
    } catch (err: any) {
      setRedirectBanner({ success: false, message: err.message || 'Failed to start Google Calendar connection.' });
      setConnecting(false);
    }
  };

  const handleDisconnect = async () => {
    if (!window.confirm('Disconnect Google Calendar? The agent will no longer be able to read or schedule events for you.')) return;
    setDisconnecting(true);
    try {
      await api.disconnectCalendar();
      await refreshStatus();
    } catch (err) {
      console.error('Failed to disconnect Google Calendar:', err);
      alert('Failed to disconnect Google Calendar.');
    } finally {
      setDisconnecting(false);
    }
  };

  if (loading) {
    return <div className="self-service-loading">Checking your connections...</div>;
  }

  return (
    <div className="self-service-container">
      <header className="self-service-header">
        <h1>Integrations</h1>
      </header>

      <div className="vertical-card-stack">
        <section className="service-card">
          <div className="card-badge">GOOGLE CALENDAR</div>
          <h2>Google Calendar</h2>
          <p className="card-description">
            Connect your own Google Calendar so the assistant can check your schedule and book
            events on your behalf — each connection is private to your account.
          </p>

          {isGuest ? (
            <div className="security-warning-lockout">
              Google Calendar isn't available for guest sessions — sign in with a real account to connect one.
            </div>
          ) : status?.connected ? (
            <>
              <p className="card-description">
                Connected as <strong>{status.account_email || 'your Google account'}</strong>.
              </p>
              <button className="action-button" onClick={handleDisconnect} disabled={disconnecting}>
                {disconnecting ? 'Disconnecting...' : 'Disconnect'}
              </button>
            </>
          ) : (
            <button className="action-button" onClick={handleConnect} disabled={connecting}>
              {connecting ? 'Redirecting to Google...' : 'Connect Google Calendar'}
            </button>
          )}

          {redirectBanner && (
            <div className={`status-banner ${redirectBanner.success ? 'success' : 'error'}`}>
              {redirectBanner.message}
            </div>
          )}
        </section>
      </div>
    </div>
  );
};

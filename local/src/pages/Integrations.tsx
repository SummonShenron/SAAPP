import React, { useState, useEffect } from 'react';
import { api, getEffectivePrincipal, isGuestPrincipal, type CalendarConnectionStatus, type GitHubTokenStatus } from '../api';
import '../pages/__styles__/SelfService.css';

// Reads query params off the hash fragment (HashRouter — see local/src/main.tsx — puts the route
// after '#', so a post-redirect '?connected=google' lands in location.hash, not location.search).
// Same idiom already used by getEmbeddedMode()/Layout.tsx/Chat.tsx for embed-mode detection.
function getHashQueryParams(): URLSearchParams {
  const hashSearch = window.location.hash.includes('?') ? window.location.hash.split('?')[1] : '';
  return new URLSearchParams(window.location.search || hashSearch);
}

// The full set of scopes this connection now requests — used only to detect whether an
// already-connected account predates the Drive/Docs/Gmail expansion and needs to reconnect.
// Calendar's own scope isn't included here since a Calendar-only connection is still fully
// functional for Calendar itself; this is specifically about the newer capabilities.
const EXPANDED_SCOPES = [
  'https://www.googleapis.com/auth/drive',
  'https://www.googleapis.com/auth/documents',
  'https://www.googleapis.com/auth/gmail.readonly',
  'https://www.googleapis.com/auth/gmail.send',
];

export const IntegrationsPage: React.FC = () => {
  const principal = getEffectivePrincipal();
  const isGuest = isGuestPrincipal(principal);

  const [status, setStatus] = useState<CalendarConnectionStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [connecting, setConnecting] = useState(false);
  const [disconnecting, setDisconnecting] = useState(false);
  const [redirectBanner, setRedirectBanner] = useState<{ success: boolean; message: string } | null>(null);
  const [githubTokenInput, setGithubTokenInput] = useState('');
  const [githubStatus, setGithubStatus] = useState<GitHubTokenStatus>({ configured: false, last4: null, github_login: null });
  const [savingToken, setSavingToken] = useState(false);
  const [tokenSaveMessage, setTokenSaveMessage] = useState<{ success: boolean; message: string } | null>(null);

  const [targetDocInput, setTargetDocInput] = useState('');
  const [savingDoc, setSavingDoc] = useState(false);
  const [docSaveMessage, setDocSaveMessage] = useState<{ success: boolean; message: string } | null>(null);
  // The server only ever reports WHETHER a token is saved (and its last 4 characters), so the field
  // is never pre-filled and a saved token can't be read back out of the page.
  const refreshGitHubToken = async () => {
    try {
      setGithubStatus(await api.getGitHubToken());
    } catch (err) {
      console.error('Failed to fetch GitHub token setting:', err);
    }
  };

  const handleSaveGitHubToken = async () => {
    setSavingToken(true);
    setTokenSaveMessage(null);
    try {
      const status = await api.updateGitHubToken(githubTokenInput.trim());
      setGithubStatus(status);
      setGithubTokenInput('');
      setTokenSaveMessage({
        success: true,
        message: status.github_login ? `Verified with GitHub as ${status.github_login} and saved.` : 'GitHub token saved.',
      });
    } catch (err) {
      setTokenSaveMessage({ success: false, message: err instanceof Error && err.message ? err.message : 'Failed to save GitHub token.' });
    } finally {
      setSavingToken(false);
    }
  };

  const handleRemoveGitHubToken = async () => {
    if (!window.confirm('Remove your GitHub token? Sonic will go back to the shared server token.')) return;
    setSavingToken(true);
    setTokenSaveMessage(null);
    try {
      setGithubStatus(await api.updateGitHubToken(null));
      setGithubTokenInput('');
      setTokenSaveMessage({ success: true, message: 'GitHub token removed.' });
    } catch (err) {
      setTokenSaveMessage({ success: false, message: err instanceof Error && err.message ? err.message : 'Failed to remove GitHub token.' });
    } finally {
      setSavingToken(false);
    }
  };

  const refreshStatus = async () => {
    try {
      const result = await api.getCalendarStatus();
      setStatus(result);
    } catch (err) {
      console.error('Failed to fetch Google connection status:', err);
    }
  };

  const refreshTargetDoc = async () => {
    try {
      const result = await api.getTargetDoc();
      setTargetDocInput(result.target_doc_id || '');
    } catch (err) {
      console.error('Failed to fetch target document setting:', err);
    }
  };

  useEffect(() => {
    const params = getHashQueryParams();
    if (params.get('connected') === 'google') {
      setRedirectBanner({ success: true, message: 'Google account connected successfully.' });
    } else if (params.get('calendar_error')) {
      const reason = params.get('calendar_error');
      const message =
        reason === 'access_denied'
          ? 'Google authorization was cancelled.'
          : reason === 'expired'
          ? 'That connection attempt expired — please try again.'
          : 'Could not complete the Google connection — please try again.';
      setRedirectBanner({ success: false, message });
    }

    Promise.all([refreshStatus(), refreshTargetDoc(), refreshGitHubToken()]).finally(() => setLoading(false));
  }, []);

  
  const handleConnect = async () => {
    setConnecting(true);
    setRedirectBanner(null);
    try {
      const { authorization_url } = await api.startCalendarConnection(window.location.href);
      window.location.href = authorization_url;
    } catch (err: any) {
      setRedirectBanner({ success: false, message: err.message || 'Failed to start the Google connection.' });
      setConnecting(false);
    }
  };

  const handleDisconnect = async () => {
    if (!window.confirm('Disconnect Google? The assistant will no longer be able to access your Calendar, Gmail, or Drive.')) return;
    setDisconnecting(true);
    try {
      await api.disconnectCalendar();
      await refreshStatus();
    } catch (err) {
      console.error('Failed to disconnect Google:', err);
      alert('Failed to disconnect Google.');
    } finally {
      setDisconnecting(false);
    }
  };

  const handleSaveTargetDoc = async () => {
    setSavingDoc(true);
    setDocSaveMessage(null);
    try {
      const result = await api.updateTargetDoc(targetDocInput.trim() || null);
      setTargetDocInput(result.target_doc_id || '');
      if (result.warning) {
        setDocSaveMessage({ success: false, message: result.warning });
      } else if (result.target_doc_id) {
        setDocSaveMessage({ success: true, message: 'Target document saved.' });
      } else {
        setDocSaveMessage({ success: true, message: 'Target document cleared.' });
      }
    } catch (err: any) {
      setDocSaveMessage({ success: false, message: err.message || 'Failed to save target document.' });
    } finally {
      setSavingDoc(false);
    }
  };

  if (loading) {
    return <div className="self-service-loading">Checking your connections...</div>;
  }

  const missingExpandedScopes = status?.connected
    ? EXPANDED_SCOPES.filter(scope => !(status.scopes || []).includes(scope))
    : [];

  return (
    <div className="self-service-container">
      <header className="self-service-header">
        <h1>Integrations</h1>
      </header>

      <div className="vertical-card-stack">
        <section className="service-card">
          <div className="card-badge">GOOGLE</div>
          <h2>Google (Calendar, Gmail, Drive, Docs)</h2>
          <p className="card-description">
            Connect your Google account so the assistant can check your schedule, search and
            summarize your Gmail and Drive, and — only when you ask and approve — schedule
            events, send emails, or update a document on your behalf. Each connection is
            private to your account.
          </p>

          {isGuest ? (
            <div className="security-warning-lockout">
              Google integrations aren't available for guest sessions — sign in with a real account to connect one.
            </div>
          ) : status?.connected ? (
            <>
              <p className="card-description">
                Connected as <strong>{status.account_email || 'your Google account'}</strong>.
              </p>
              {missingExpandedScopes.length > 0 && (
                <div className="status-banner error">
                  This connection predates Gmail/Drive/Docs access — reconnect to enable those
                  capabilities.
                  <div style={{ marginTop: '0.5rem' }}>
                    <button className="action-button" onClick={handleConnect} disabled={connecting}>
                      {connecting ? 'Redirecting to Google...' : 'Reconnect Google'}
                    </button>
                  </div>
                </div>
              )}
              <button className="action-button" onClick={handleDisconnect} disabled={disconnecting}>
                {disconnecting ? 'Disconnecting...' : 'Disconnect'}
              </button>
            </>
          ) : (
            <button className="action-button" onClick={handleConnect} disabled={connecting}>
              {connecting ? 'Redirecting to Google...' : 'Connect Google'}
            </button>
          )}

          {redirectBanner && (
            <div className={`status-banner ${redirectBanner.success ? 'success' : 'error'}`}>
              {redirectBanner.message}
            </div>
          )}
        </section>

        {!isGuest && (
          <section className="service-card">
            <div className="card-badge">TARGET DOCUMENT</div>
            <h2>Target Document</h2>
            <p className="card-description">
              A Google Doc the assistant can append to when you ask it to — for example, after
              summarizing an email or file. Paste the document's URL or ID below. This isn't
              tied to any specific use case; you can point it at whatever document you'd like
              updated.
            </p>

            <div className="form-group">
              <input
                type="text"
                value={targetDocInput}
                onChange={(e) => setTargetDocInput(e.target.value)}
                placeholder="https://docs.google.com/document/d/..."
                style={{ width: '100%', boxSizing: 'border-box' }}
              />
            </div>
            <button className="action-button" onClick={handleSaveTargetDoc} disabled={savingDoc}>
              {savingDoc ? 'Saving...' : 'Save'}
            </button>

            {docSaveMessage && (
              <div className={`status-banner ${docSaveMessage.success ? 'success' : 'error'}`}>
                {docSaveMessage.message}
              </div>
            )}
          </section>
        )}
        {!isGuest && (
          <section className="service-card">
            <div className="card-badge">GITHUB TOKEN</div>
            <h2>GitHub Token</h2>
            <p className="card-description">
              Your own personal access token, so Sonic can read, review and open pull requests and issues on
              your repositories as you. It is verified with GitHub, stored encrypted, and never shown again after
              you save it. Without one, Sonic uses the shared server token.
            </p>

            {githubStatus.configured && (
              <div className="status-banner success">
                Token saved (ends in …{githubStatus.last4})
                {githubStatus.github_login ? ` — connected as ${githubStatus.github_login}` : ''}
              </div>
            )}

            <div className="form-group">
              <input
                type="password"
                autoComplete="off"
                spellCheck={false}
                value={githubTokenInput}
                onChange={(e) => setGithubTokenInput(e.target.value)}
                placeholder={githubStatus.configured ? 'Paste a new token to replace it' : 'ghp_… or github_pat_…'}
                style={{ width: '100%', boxSizing: 'border-box' }}
              />
            </div>
            <button
              className="action-button"
              onClick={handleSaveGitHubToken}
              disabled={savingToken || !githubTokenInput.trim()}
            >
              {savingToken ? 'Working…' : githubStatus.configured ? 'Replace token' : 'Save token'}
            </button>
            {githubStatus.configured && (
              <button
                className="action-button"
                onClick={handleRemoveGitHubToken}
                disabled={savingToken}
                style={{ marginLeft: 8 }}
              >
                Remove
              </button>
            )}

            {tokenSaveMessage && (
              <div className={`status-banner ${tokenSaveMessage.success ? 'success' : 'error'}`}>
                {tokenSaveMessage.message}
              </div>
            )}
          </section>
        )}
        
      </div>
    </div>
  );
};

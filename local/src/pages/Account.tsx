import React from 'react';
import { useNavigate } from 'react-router-dom';
import { getEffectivePrincipal, isGuestPrincipal } from '../api';
import ChatSettingsCard from '../components/ChatSettingsCard';
import ProfileCard from '../components/ProfileCard';
import RunnerSettingsCard from '../components/RunnerSettingsCard';
import './__styles__/Account.css';

/**
 * Account settings: where the user chip in the rail leads. For now it shows who you are signed in as, lets you pick the theme, and points to the
 * pages that already hold your data. The chat box's "..." menu keeps only what acts on the conversation you are in (scope, answers, thinking, help, export, delete); the choices about how Sonic behaves live here.
 */
export const AccountPage: React.FC<{ theme: string; toggleTheme: () => void }> = ({ theme, toggleTheme }) => {
  const navigate = useNavigate();
  const principal = getEffectivePrincipal();
  const guest = isGuestPrincipal(principal);
  const dark = theme === 'shadow';

  return (
    <div className="account-page">
      <h2 className="account-heading">Account settings</h2>

      <section className="account-card">
        <h3>Profile</h3>
        <dl className="account-rows">
          <div><dt>Signed in as</dt><dd>{guest ? 'Guest (shared sandbox)' : principal}</dd></div>
          <div><dt>Account type</dt><dd>{guest ? 'Guest' : 'Registered'}</dd></div>
        </dl>
        {guest && <p className="account-note">Guests share one temporary identity, so nothing here is personal. Sign in to keep your own conversations, memory, and settings.</p>}
      </section>

      {!guest && <ProfileCard />}

      <ChatSettingsCard mascotAllowed={principal !== 'guest_bty'} />

      {!guest && <RunnerSettingsCard />}

      <section className="account-card">
        <h3>Appearance</h3>
        <div className="account-row-action">
          <div>
            <div className="account-label">Theme</div>
            <div className="account-hint">{dark ? 'Dark theme is on.' : 'Light theme is on.'}</div>
          </div>
          <button type="button" className="account-btn" onClick={toggleTheme}>{dark ? 'Switch to light' : 'Switch to dark'}</button>
        </div>
      </section>

      {!guest && (
        <section className="account-card">
          <h3>Your data</h3>
          <p className="account-note">
            Your conversations are kept until you delete them. Photos and files you attach are removed after 30 days, or sooner if you delete the conversation.
            What you attach is read by Google's Gemini so Sonic can answer.
          </p>
          <div className="account-row-action">
            <div>
              <div className="account-label">Memory</div>
              <div className="account-hint">What Sonic remembers about you, and how to remove it.</div>
            </div>
            <button type="button" className="account-btn" onClick={() => navigate('/memory')}>Open</button>
          </div>
          <div className="account-row-action">
            <div>
              <div className="account-label">Integrations</div>
              <div className="account-hint">Connected accounts such as calendar and GitHub.</div>
            </div>
            <button type="button" className="account-btn" onClick={() => navigate('/integrations')}>Open</button>
          </div>
          <div className="account-row-action">
            <div>
              <div className="account-label">Privacy</div>
              <div className="account-hint">What we keep, who sees it, and how to remove it.</div>
            </div>
            <button type="button" className="account-btn" onClick={() => navigate('/privacy')}>Read</button>
          </div>
        </section>
      )}
    </div>
  );
};

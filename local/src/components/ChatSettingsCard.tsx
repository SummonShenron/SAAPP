import React, { useEffect, useState } from 'react';
import { api } from '../api';
import { loadShowPatchy, saveShowPatchy } from '../chatPreferences';

interface Props {
  /** The shared portfolio guest has no mascot choice (the embedded demo's identity). */
  mascotAllowed: boolean;
}

/**
 * The Account page's "Chat" card: the choices that used to sit in the chat box's "..." menu. Message first is in "About you" (it already was); this holds the mascot
 * and, for an admin only, the incident digest. Each switch saves as soon as it changes.
 */
export const ChatSettingsCard: React.FC<Props> = ({ mascotAllowed }) => {
  const [showPatchy, setShowPatchy] = useState<boolean>(() => loadShowPatchy());
  const [digest, setDigest] = useState<{ enabled: boolean; available: boolean } | null>(null);
  const [digestError, setDigestError] = useState('');

  useEffect(() => {
    let cancelled = false;
    api.getOpsDigest()
      .then(value => { if (!cancelled) setDigest(value); })
      .catch(() => { /* not an admin, or no such setting here: the row stays hidden */ });
    return () => { cancelled = true; };
  }, []);

  const changeMascot = (next: boolean) => {
    setShowPatchy(next);
    saveShowPatchy(next);
  };

  const changeDigest = async (enabled: boolean) => {
    setDigestError('');
    setDigest(prev => (prev ? { ...prev, enabled } : prev));
    try {
      setDigest(await api.updateOpsDigest(enabled));
    } catch {
      setDigest(prev => (prev ? { ...prev, enabled: !enabled } : prev));
      setDigestError('That did not save. Try again.');
    }
  };

  const showDigest = !!digest && digest.available;
  if (!mascotAllowed && !showDigest) return null;

  return (
    <section className="account-card">
      <h3>Chat</h3>
      {mascotAllowed && (
        <div className="account-row-action">
          <div>
            <div className="account-label">Show SAAPP, the little robot</div>
            <div className="account-hint">He sits on the message box and reacts while Sonic works. Turn him off for a plain logo.</div>
          </div>
          <input type="checkbox" className="profile-switch" checked={showPatchy} onChange={event => changeMascot(event.target.checked)} aria-label="Show SAAPP, the little robot" />
        </div>
      )}
      {showDigest && digest && (
        <div className="account-row-action">
          <div>
            <div className="account-label">Incident digest</div>
            <div className="account-hint">For admins: when you open a conversation, Sonic tells you about new production incidents, once each and at most every 12 hours. Nothing new means no message.</div>
            {digestError && <div className="account-error" role="alert">{digestError}</div>}
          </div>
          <input type="checkbox" className="profile-switch" checked={digest.enabled} onChange={event => void changeDigest(event.target.checked)} aria-label="Incident digest" />
        </div>
      )}
    </section>
  );
};

export default ChatSettingsCard;

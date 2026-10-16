import React, { useEffect, useState } from 'react';
import { api, ProfileSaveError, type UserProfile } from '../api';
import TagInput from './TagInput';

// The limits the server enforces (backend/utils/profile_utils.py); the server is the one that decides, these only keep the boxes honest.
const NAME_MAX = 40;
const PRONOUN_CHOICES = ['she/her', 'he/him', 'they/them', 'any'];
const PRONOUN_MAX = 30;
const WORK_MAX = 60;

const EMPTY: UserProfile = {
  name: '', pronouns: '', work: '', interests: [], style: { length: 'balanced', tone: 'balanced', humor: 'default' }, avoid: [], offers: true,
};

/**
 * "About you": what a person tells Sonic about themselves on purpose. Every field is optional, and each says what Sonic does with it. Saved as a whole
 * with one button; the server names the field and the reason when it refuses a value.
 */
const ProfileCard: React.FC = () => {
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [custom, setCustom] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<{ field: string; message: string } | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [messageFirst, setMessageFirst] = useState<boolean | null>(null);

  useEffect(() => {
    api.getProfile()
      .then(p => { setProfile({ ...EMPTY, ...p, style: { ...EMPTY.style, ...p.style } }); setCustom(!!p.pronouns && !PRONOUN_CHOICES.includes(p.pronouns)); })
      .catch(() => setLoadFailed(true));
    api.getProactiveOpening().then(out => setMessageFirst(out.enabled)).catch(() => setMessageFirst(null));
  }, []);

  if (loadFailed) return <section className="account-card"><h3>About you</h3><p className="account-note">Could not load your profile. Reload the page to try again.</p></section>;
  if (!profile) return <section className="account-card"><h3>About you</h3><p className="account-note">Loading...</p></section>;

  const update = (patch: Partial<UserProfile>) => { setProfile({ ...profile, ...patch }); setSaved(false); setError(null); };
  const updateStyle = (patch: Partial<UserProfile['style']>) => update({ style: { ...profile.style, ...patch } });
  const fieldError = (field: string) => (error?.field === field ? <div className="account-error" role="alert">{error.message}</div> : null);

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      const out = await api.updateProfile(profile);
      setProfile({ ...EMPTY, ...out, style: { ...EMPTY.style, ...out.style } });
      setSaved(true);
    } catch (err) {
      setError(err instanceof ProfileSaveError ? { field: err.field, message: err.message } : { field: '', message: err instanceof Error ? err.message : 'Could not save.' });
    } finally {
      setSaving(false);
    }
  };

  const toggleMessageFirst = async (next: boolean) => {
    setMessageFirst(next);
    try { setMessageFirst((await api.updateProactiveOpening(next)).enabled); } catch { setMessageFirst(!next); }
  };

  const pronounValue = custom ? '__other' : profile.pronouns;

  return (
    <section className="account-card profile-card">
      <h3>About you</h3>
      <p className="account-note profile-intro">Tell Sonic what you'd like it to know. Everything here is optional, and you can change or clear it any time. Sonic uses it quietly: it won't read it back to you.</p>

      <div className="profile-field">
        <label className="account-label" htmlFor="profile-name">Name</label>
        <input id="profile-name" className="account-input" type="text" value={profile.name} maxLength={NAME_MAX} placeholder="What should Sonic call you?" onChange={e => update({ name: e.target.value })} />
        <div className="account-hint">Sonic will use it now and then, not in every reply.</div>
        {fieldError('name')}
      </div>

      <div className="profile-field">
        <label className="account-label" htmlFor="profile-pronouns">Pronouns</label>
        <select
          id="profile-pronouns"
          className="account-input"
          value={pronounValue}
          onChange={e => {
            if (e.target.value === '__other') { setCustom(true); update({ pronouns: '' }); }
            else { setCustom(false); update({ pronouns: e.target.value }); }
          }}
        >
          <option value="">Not set</option>
          {PRONOUN_CHOICES.map(p => <option key={p} value={p}>{p}</option>)}
          <option value="__other">Something else...</option>
        </select>
        {custom && (
          <input className="account-input" type="text" value={profile.pronouns} maxLength={PRONOUN_MAX} placeholder="Your pronouns" aria-label="Your pronouns" onChange={e => update({ pronouns: e.target.value })} />
        )}
        <div className="account-hint">Used if Sonic ever refers to you in the third person.</div>
        {fieldError('pronouns')}
      </div>

      <div className="profile-field">
        <label className="account-label" htmlFor="profile-work">What you do</label>
        <input id="profile-work" className="account-input" type="text" value={profile.work ?? ''} maxLength={WORK_MAX} placeholder="Your job title, or something like retired or student" onChange={e => update({ work: e.target.value })} />
        <div className="account-hint">Helps Sonic pitch explanations at the right level. It won't bring it up unless it matters.</div>
        {fieldError('work')}
      </div>

      <div className="profile-field">
        <label className="account-label" htmlFor="profile-interests">Interests</label>
        <TagInput id="profile-interests" label="interests" values={profile.interests} onChange={interests => update({ interests })} max={10} maxLength={40} placeholder="Add one and press Enter" />
        <div className="account-hint">Things you enjoy. Sonic keeps them in mind and may ask about one when it opens a conversation (only if you've turned that on below).</div>
        {fieldError('interests')}
      </div>

      <div className="profile-field">
        <div className="account-label">How you like answers</div>
        <div className="profile-style">
          <label>Length
            <select className="account-input" value={profile.style.length} onChange={e => updateStyle({ length: e.target.value })}>
              <option value="concise">Short</option><option value="balanced">Balanced</option><option value="detailed">Detailed</option>
            </select>
          </label>
          <label>Tone
            <select className="account-input" value={profile.style.tone} onChange={e => updateStyle({ tone: e.target.value })}>
              <option value="direct">Direct</option><option value="balanced">Balanced</option><option value="warm">Warm</option>
            </select>
          </label>
          <label>Humor
            <select className="account-input" value={profile.style.humor} onChange={e => updateStyle({ humor: e.target.value })}>
              <option value="none">None</option><option value="default">Sonic's usual</option><option value="more">More</option>
            </select>
          </label>
        </div>
        <div className="account-hint">When you're having a hard time, Sonic still softens and slows down whatever you pick.</div>
        {fieldError('style')}
      </div>

      <div className="profile-field">
        <label className="account-label" htmlFor="profile-avoid">Please don't bring up</label>
        <TagInput id="profile-avoid" label="topics to leave alone" values={profile.avoid} onChange={avoid => update({ avoid })} max={10} maxLength={60} placeholder="A topic to leave alone, then Enter" />
        <div className="account-hint">Sonic won't raise these on its own. If you bring one up yourself, it will talk about it normally.</div>
        {fieldError('avoid')}
      </div>

      <div className="account-row-action">
        <div>
          <div className="account-label">Let Sonic bring something up on its own</div>
          <div className="account-hint">At a quiet moment it may ask what else is going on, mention something it leans toward, or something it has read. At most once in a conversation.</div>
        </div>
        <input type="checkbox" className="profile-switch" checked={profile.offers} onChange={e => update({ offers: e.target.checked })} aria-label="Let Sonic bring something up on its own" />
      </div>

      {messageFirst !== null && (
        <div className="account-row-action">
          <div>
            <div className="account-label">Let Sonic start conversations</div>
            <div className="account-hint">It may open a chat with one question about something you told it, like how a date went. Saved as soon as you change it.</div>
          </div>
          <input type="checkbox" className="profile-switch" checked={messageFirst} onChange={e => toggleMessageFirst(e.target.checked)} aria-label="Let Sonic start conversations" />
        </div>
      )}

      <div className="profile-actions">
        <button type="button" className="account-btn" onClick={save} disabled={saving}>{saving ? 'Saving...' : 'Save'}</button>
        {saved && <span className="profile-saved" role="status">Saved.</span>}
        {error && !error.field && <span className="account-error" role="alert">{error.message}</span>}
      </div>
    </section>
  );
};

export default ProfileCard;

import React, { useEffect, useState } from 'react';
import { api, type HeadsUpItem, type HeadsUpSettings } from '../api';
import './__styles__/CalendarHeadsUps.css';

/**
 * The person's own switches for calendar heads-ups: when they open a chat, Sonic may say one short line about something coming up. Off until they turn it on,
 * and a way to see exactly what it would mention, without anything being said or recorded.
 */
export const CalendarHeadsUps: React.FC = () => {
  const [settings, setSettings] = useState<HeadsUpSettings | null>(null);
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const [preview, setPreview] = useState<HeadsUpItem[] | null>(null);
  const [previewing, setPreviewing] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api.getCalendarHeadsUps()
      .then(result => { if (!cancelled) setSettings(result.settings); })
      .catch((err: unknown) => { if (!cancelled) setError(err instanceof Error ? err.message : 'Failed to load these settings.'); });
    return () => { cancelled = true; };
  }, []);

  const change = async (changes: Partial<HeadsUpSettings>) => {
    if (!settings) return;
    const before = settings;
    setSettings({ ...settings, ...changes });
    setSaving(true);
    setError('');
    setPreview(null);
    try {
      setSettings((await api.putCalendarHeadsUps(changes)).settings);
    } catch (err) {
      setSettings(before);
      setError(err instanceof Error ? err.message : 'Failed to save that.');
    } finally {
      setSaving(false);
    }
  };

  const showPreview = async () => {
    setPreviewing(true);
    setError('');
    try {
      setPreview((await api.previewCalendarHeadsUps()).items);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to look at your calendar.');
    } finally {
      setPreviewing(false);
    }
  };

  return (
    <section className="service-card">
      <div className="card-badge">HEADS-UPS</div>
      <h2>Calendar heads-ups</h2>
      <p className="card-description">
        When you open a chat, Sonic can say one short line about something coming up on your calendar today, like an appointment, or tomorrow's exam the evening before. It reads your calendar
        when you open the chat, mentions each thing once, and never presses you about it. It skips private events, invitations you haven't accepted, and anything you've declined.
        The same switch lets Sonic check your calendar for a plan you've told it about: it can fill in a time or place you didn't give and say when your calendar shows something different
        (it goes with what you said). It only reads your calendar, keeps nothing from it, and never uses a place when discreet wording is on.
      </p>
      {error && <div className="status-banner error" role="alert">{error}</div>}
      {settings && (
        <div className="headsups-switches">
          <label className="headsups-switch">
            <input type="checkbox" checked={settings.enabled} disabled={saving} onChange={e => change({ enabled: e.target.checked })} />
            <span><strong>Mention what's coming up</strong><br /><span className="headsups-hint">Off until you turn it on.</span></span>
          </label>
          <label className="headsups-switch">
            <input type="checkbox" checked={settings.include_health} disabled={saving || !settings.enabled} onChange={e => change({ include_health: e.target.checked })} />
            <span><strong>Include health and therapy appointments</strong><br /><span className="headsups-hint">Doctor, therapy and similar. Funerals, court dates and legal events are never mentioned.</span></span>
          </label>
          <label className="headsups-switch">
            <input type="checkbox" checked={settings.discreet} disabled={saving || !settings.enabled} onChange={e => change({ discreet: e.target.checked })} />
            <span><strong>Discreet wording</strong><br /><span className="headsups-hint">Say "an appointment" instead of its title, in case someone else can see your screen.</span></span>
          </label>
        </div>
      )}
      {settings?.enabled && (
        <div className="headsups-preview">
          <button type="button" className="action-button" onClick={showPreview} disabled={previewing}>{previewing ? 'Looking...' : "Show what Sonic would mention"}</button>
          {preview && preview.length === 0 && <p className="card-description">Nothing on your calendar today or tomorrow would be mentioned.</p>}
          {preview && preview.length > 0 && (
            <ul className="headsups-list">
              {preview.map((item, index) => <li key={index}><strong>{item.title}</strong> <span className="headsups-hint">{item.when}</span></li>)}
            </ul>
          )}
        </div>
      )}
    </section>
  );
};

export default CalendarHeadsUps;

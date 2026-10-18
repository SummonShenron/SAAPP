import React, { useCallback, useEffect, useState } from 'react';
import { api, type MeetingsState } from '../api';
import './__styles__/MeetingControl.css';

interface Props {
  sessionId: string;
  /** Called once the meeting has started, so the chat can open it with a first message. */
  onStarted: (meetingName: string) => void;
}

/**
 * The chat header's office hours button. Pressing Start is the only way a meeting begins (Sonic never starts one), and "End and save notes" is the only way it ends.
 * A meeting can be started any time, early or late: there is no calendar read here and nothing polls.
 */
export const MeetingControl: React.FC<Props> = ({ sessionId, onStarted }) => {
  const [state, setState] = useState<MeetingsState | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [menuOpen, setMenuOpen] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api.getMeetingsState(sessionId)
      .then(next => { if (!cancelled) setState(next); })
      .catch(() => { /* no meetings control is the normal outcome when there is nothing to show */ });
    return () => { cancelled = true; };
  }, [sessionId]);

  const refresh = useCallback(async () => {
    setState(await api.getMeetingsState(sessionId));
  }, [sessionId]);

  if (!state || state.meetings.length === 0) return null;

  const start = async (id: string, name: string) => {
    setMenuOpen(false);
    setBusy(true);
    setMessage('');
    try {
      await api.startMeeting(id, sessionId);
      await refresh();
      onStarted(name);
    } catch (err) {
      setMessage(err instanceof Error ? err.message : 'Failed to start the meeting.');
    } finally {
      setBusy(false);
    }
  };

  const end = async (runId: string) => {
    setBusy(true);
    setMessage('');
    try {
      const result = await api.endMeeting(runId);
      await refresh();
      if (result.note) setMessage('Notes saved.');
      else if (result.reason === 'low_power') setMessage('Ended. Sonic is in low-power mode, so no notes were written.');
      else setMessage('Ended. Nothing was said, so there are no notes.');
    } catch (err) {
      // It may already have closed itself after a quiet spell, with its notes saved: show what is true now.
      await refresh().catch(() => undefined);
      const text = err instanceof Error ? err.message : 'Failed to end the meeting.';
      setMessage(text.includes("isn't running") ? 'That meeting already closed itself after a quiet spell. Its notes are on the Meetings page.' : text);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="meeting-control">
      {message && <span className="meeting-control-msg" role="status">{message}</span>}
      {state.active ? (
        <button type="button" className="topbar-btn meeting-btn meeting-btn-active" disabled={busy} onClick={() => end(state.active!.run_id)}
          title={`Ends ${state.active.name} and saves its notes`}>
          {busy ? 'Saving...' : 'End and save notes'}
        </button>
      ) : state.meetings.length === 1 ? (
        <button type="button" className="topbar-btn meeting-btn" disabled={busy}
          onClick={() => start(state.meetings[0].id, state.meetings[0].name)}
          title={`Start ${state.meetings[0].name} with Sonic`}>
          Start office hours
        </button>
      ) : (
        <div className="meeting-menu-wrap">
          <button type="button" className="topbar-btn meeting-btn" disabled={busy} aria-expanded={menuOpen} onClick={() => setMenuOpen(open => !open)}>
            Start office hours
          </button>
          {menuOpen && (
            <div className="meeting-menu" role="menu">
              {state.meetings.map(meeting => (
                <button key={meeting.id} type="button" role="menuitem" className="meeting-menu-item" onClick={() => start(meeting.id, meeting.name)}>
                  {meeting.name}
                </button>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
};

export default MeetingControl;

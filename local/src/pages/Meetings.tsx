import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api, getEffectivePrincipal, type Meeting, type MeetingNote, type MeetingSpec } from '../api';
import './__styles__/SelfService.css';
import './__styles__/Memory.css';
import './__styles__/Tasks.css';
import './__styles__/Meetings.css';

interface Draft {
  name: string;
  match: string;
  purpose: string;
  agenda: string;
  cadence: 'none' | 'daily' | 'weekly';
  weekday: number;
  time: string;
}

const WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
const EMPTY_DRAFT: Draft = { name: '', match: '', purpose: '', agenda: '', cadence: 'none', weekday: 6, time: '12:00' };

function pad(n: number): string {
  return String(n).padStart(2, '0');
}

function formatHeld(iso: string | null): string {
  if (!iso) return '';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleDateString(undefined, { weekday: 'long', month: 'short', day: 'numeric' });
}

function toSpec(draft: Draft): MeetingSpec {
  return {
    name: draft.name.trim(),
    match: draft.match.trim(),
    purpose: draft.purpose.trim(),
    agenda: draft.agenda.split('\n').map(line => line.trim()).filter(Boolean),
    cadence: draft.cadence,
    weekday: draft.weekday,
    time: draft.time,
  };
}

export const MeetingsPage: React.FC = () => {
  const navigate = useNavigate();
  const [meetings, setMeetings] = useState<Meeting[]>([]);
  const [maxMeetings, setMaxMeetings] = useState(5);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [editing, setEditing] = useState<string | null>(null); // 'new', a meeting id, or null
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState('');
  const [busyId, setBusyId] = useState<string | null>(null);
  const [openNotesId, setOpenNotesId] = useState<string | null>(null);
  const [notes, setNotes] = useState<MeetingNote[]>([]);
  const [notesLoading, setNotesLoading] = useState(false);
  const [editingNote, setEditingNote] = useState<string | null>(null);
  const [noteDraft, setNoteDraft] = useState({ summary: '', action_items: '' });

  useEffect(() => {
    let cancelled = false;
    api.listMeetings()
      .then(({ meetings: found, limits }) => { if (!cancelled) { setMeetings(found); setMaxMeetings(limits.max_meetings); } })
      .catch((err: unknown) => { if (!cancelled) setError(err instanceof Error ? err.message : 'Failed to load your meetings.'); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  const beginEdit = (meeting: Meeting | null) => {
    setFormError('');
    if (meeting) {
      setEditing(meeting.id);
      setDraft({
        name: meeting.name, match: meeting.match, purpose: meeting.purpose, agenda: meeting.agenda.join('\n'),
        cadence: meeting.cadence, weekday: meeting.weekday ?? 6, time: meeting.hour === null ? '12:00' : `${pad(meeting.hour)}:${pad(meeting.minute ?? 0)}`,
      });
    } else {
      setEditing('new');
      setDraft(EMPTY_DRAFT);
    }
  };

  const handleSave = async () => {
    setSaving(true);
    setFormError('');
    try {
      const spec = toSpec(draft);
      if (editing === 'new') {
        const created = await api.createMeeting(spec);
        setMeetings(prev => [...prev, created]);
      } else if (editing) {
        const updated = await api.updateMeeting(editing, spec);
        setMeetings(prev => prev.map(m => (m.id === updated.id ? { ...updated, notes: m.notes, last_held: m.last_held } : m)));
      }
      setEditing(null);
    } catch (err) {
      setFormError(err instanceof Error ? err.message : 'Failed to save that meeting.');
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (meeting: Meeting) => {
    const extra = meeting.notes > 0 ? ` and its ${meeting.notes} saved note${meeting.notes === 1 ? '' : 's'}` : '';
    if (!window.confirm(`Delete "${meeting.name}"${extra}? This cannot be undone.`)) return;
    setError('');
    setBusyId(meeting.id);
    try {
      await api.deleteMeeting(meeting.id);
      setMeetings(prev => prev.filter(m => m.id !== meeting.id));
      if (openNotesId === meeting.id) setOpenNotesId(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete that meeting.');
    } finally {
      setBusyId(null);
    }
  };

  // The only way a meeting starts from here: a fresh conversation is made, the meeting is started in it, and the chat opens with the first line already sent.
  const handleStart = async (meeting: Meeting) => {
    setError('');
    setBusyId(meeting.id);
    try {
      const sessionId = crypto.randomUUID();
      await api.startMeeting(meeting.id, sessionId);
      localStorage.setItem(`conversation-id-${getEffectivePrincipal()}`, sessionId);
      navigate('/chat', { state: { autoSend: `Let's start our ${meeting.name}.` } });
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to start the meeting.');
      setBusyId(null);
    }
  };

  const toggleNotes = async (meeting: Meeting) => {
    if (openNotesId === meeting.id) { setOpenNotesId(null); return; }
    setOpenNotesId(meeting.id);
    setNotes([]);
    setEditingNote(null);
    setNotesLoading(true);
    try {
      setNotes((await api.listMeetingNotes(meeting.id)).notes);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load the notes.');
    } finally {
      setNotesLoading(false);
    }
  };

  const saveNote = async (note: MeetingNote) => {
    setError('');
    try {
      const updated = await api.updateMeetingNote(note.id, {
        summary: noteDraft.summary,
        action_items: noteDraft.action_items.split('\n').map(line => line.trim()).filter(Boolean),
      });
      setNotes(prev => prev.map(n => (n.id === updated.id ? updated : n)));
      setEditingNote(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update that note.');
    }
  };

  const removeNote = async (note: MeetingNote) => {
    if (!window.confirm('Delete this note? This cannot be undone.')) return;
    setError('');
    try {
      await api.deleteMeetingNote(note.id);
      setNotes(prev => prev.filter(n => n.id !== note.id));
      setMeetings(prev => prev.map(m => (m.id === note.meeting_id ? { ...m, notes: Math.max(0, m.notes - 1) } : m)));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete that note.');
    }
  };

  if (loading) return <div className="self-service-loading">Loading your meetings...</div>;

  const atLimit = meetings.length >= maxMeetings;
  const patch = (changes: Partial<Draft>) => setDraft(prev => ({ ...prev, ...changes }));

  return (
    <div className="self-service-container">
      <header className="self-service-header">
        <h1>Meetings</h1>
        <p>Standing meetings you have with Sonic, like a weekly check-in. Press Start office hours and Sonic runs the agenda in a chat; you end it, and the notes are kept here.</p>
      </header>

      <div className="vertical-card-stack">
        <section className="service-card memory-card">
          <div className="card-badge">{meetings.length} OF {maxMeetings}</div>
          <h2>Your Meetings</h2>
          <p className="card-description">
            A meeting starts only when you press the button, whenever you like: early, late, or not at all that week. You can say when you mean to hold it, and it is marked due for that day. A Google Calendar
            is optional: add the event's title and Sonic will call it "our" meeting when you ask about your calendar.
          </p>
          {error && <div className="memory-edit-error" role="alert">{error}</div>}

          {meetings.length === 0 && editing === null && (
            <div className="empty-manifest-notice">No meetings yet. Try a weekly check-in: what you did, what you applied to, and how aligned it all was.</div>
          )}

          <div className="memory-list tasks-list">
            {meetings.map(meeting => (
              <div key={meeting.id} className="task-item">
                <div className="memory-row task-row">
                  <div className="memory-row-main">
                    <div className="memory-row-badges">
                      {meeting.schedule && <span className="memory-badge memory-source-badge">{meeting.schedule}</span>}
                      {meeting.due_today && <span className="memory-badge task-new-badge">due today</span>}
                      {meeting.match && <span className="memory-badge memory-source-badge">on calendar as: {meeting.match}</span>}
                      {meeting.last_held && <span className="memory-badge memory-source-badge">last held {formatHeld(meeting.last_held)}</span>}
                    </div>
                    <div className="memory-row-text task-title">{meeting.name}</div>
                    {meeting.purpose && <div className="task-meta">{meeting.purpose}</div>}
                    {meeting.agenda.length > 0 && (
                      <ol className="meeting-agenda">
                        {meeting.agenda.map((item, index) => <li key={index}>{item}</li>)}
                      </ol>
                    )}
                  </div>
                  <div className="task-actions">
                    <button type="button" className={`memory-edit-save${meeting.due_today ? ' meeting-start-now' : ''}`} disabled={busyId === meeting.id} onClick={() => handleStart(meeting)}>
                      Start office hours
                    </button>
                    <button type="button" className="memory-edit-cancel" onClick={() => toggleNotes(meeting)}>
                      {openNotesId === meeting.id ? 'Hide notes' : `Notes (${meeting.notes})`}
                    </button>
                    <button type="button" className="memory-edit-cancel" disabled={busyId === meeting.id} onClick={() => beginEdit(meeting)}>Edit</button>
                    <button type="button" className="delete-row-btn" disabled={busyId === meeting.id} onClick={() => handleDelete(meeting)}>Delete</button>
                  </div>
                </div>

                {openNotesId === meeting.id && (
                  <div className="task-results">
                    {notesLoading && <div className="task-meta">Loading...</div>}
                    {!notesLoading && notes.length === 0 && <div className="task-meta">No notes yet. They are written when you press "End and save notes" at the end of a meeting.</div>}
                    {notes.map(note => (
                      <div key={note.id} className="task-run">
                        <div className="task-run-head">{formatHeld(note.held_at)}{note.automatic ? ' · ended automatically after a quiet spell' : ''}</div>
                        {editingNote === note.id ? (
                          <div className="meeting-note-edit">
                            <textarea className="memory-edit-text" rows={4} value={noteDraft.summary} aria-label="Summary" onChange={e => setNoteDraft(prev => ({ ...prev, summary: e.target.value }))} />
                            <label className="task-label" htmlFor={`items-${note.id}`}>To do next (one per line)</label>
                            <textarea id={`items-${note.id}`} className="memory-edit-text" rows={3} value={noteDraft.action_items} onChange={e => setNoteDraft(prev => ({ ...prev, action_items: e.target.value }))} />
                            <div className="memory-edit-row">
                              <button type="button" className="memory-edit-cancel" onClick={() => setEditingNote(null)}>Cancel</button>
                              <button type="button" className="memory-edit-save" onClick={() => saveNote(note)}>Save</button>
                            </div>
                          </div>
                        ) : (
                          <>
                            <div className="task-headline">{note.summary || 'No summary.'}</div>
                            {note.decisions.length > 0 && (
                              <div className="meeting-note-block"><strong>Decided</strong><ul className="meeting-note-list">{note.decisions.map((d, i) => <li key={i}>{d}</li>)}</ul></div>
                            )}
                            {note.action_items.length > 0 && (
                              <div className="meeting-note-block"><strong>To do next</strong><ul className="meeting-note-list">{note.action_items.map((d, i) => <li key={i}>{d}</li>)}</ul></div>
                            )}
                            <div className="task-actions meeting-note-actions">
                              <button type="button" className="memory-edit-cancel" onClick={() => { setEditingNote(note.id); setNoteDraft({ summary: note.summary, action_items: note.action_items.join('\n') }); }}>Edit</button>
                              <button type="button" className="delete-row-btn" onClick={() => removeNote(note)}>Delete</button>
                            </div>
                          </>
                        )}
                      </div>
                    ))}
                    {notes.length > 0 && <div className="memory-edit-hint">Notes are written by Sonic from what was said and can be wrong. Edit anything that is off: Sonic reads them at the next meeting.</div>}
                  </div>
                )}
              </div>
            ))}
          </div>

          {editing === null && (
            <div className="task-new-row">
              <button type="button" className="memory-edit-save" disabled={atLimit} onClick={() => beginEdit(null)}>New meeting</button>
              {atLimit && <span className="task-meta">You're at the limit of {maxMeetings}. Delete one to add another.</span>}
            </div>
          )}
        </section>

        {editing !== null && (
          <section className="service-card memory-card">
            <h2>{editing === 'new' ? 'New Meeting' : 'Edit Meeting'}</h2>
            <div className="task-form">
              <label className="task-label" htmlFor="meeting-name">Name</label>
              <input id="meeting-name" className="manifest-search-input" type="text" maxLength={60} autoFocus value={draft.name}
                placeholder="e.g. Weekly alignment check-in" onChange={e => patch({ name: e.target.value })} />
              <label className="task-label" htmlFor="meeting-cadence">When do you mean to hold it? <span className="task-optional">optional</span></label>
              <div className="meeting-when-row">
                <select id="meeting-cadence" className="memory-category-select" value={draft.cadence} onChange={e => patch({ cadence: e.target.value as Draft['cadence'] })}>
                  <option value="none">No set time</option>
                  <option value="weekly">Every week</option>
                  <option value="daily">Every day</option>
                </select>
                {draft.cadence === 'weekly' && (
                  <select className="memory-category-select" aria-label="Day of the week" value={draft.weekday} onChange={e => patch({ weekday: Number(e.target.value) })}>
                    {WEEKDAYS.map((day, index) => <option key={day} value={index}>{day}</option>)}
                  </select>
                )}
                {draft.cadence !== 'none' && (
                  <input className="memory-category-select" type="time" aria-label="Time of day" value={draft.time} onChange={e => patch({ time: e.target.value })} />
                )}
              </div>
              <div className="memory-edit-hint">It only marks the meeting as due that day, in your timezone. You can still start it early, late, or skip it.</div>
              <label className="task-label" htmlFor="meeting-match">Title on your Google Calendar <span className="task-optional">optional</span></label>
              <input id="meeting-match" className="manifest-search-input" type="text" maxLength={80} value={draft.match}
                placeholder="Only if it is on a calendar, e.g. alignment check in" onChange={e => patch({ match: e.target.value })} />
              <label className="task-label" htmlFor="meeting-purpose">What is it for? <span className="task-optional">optional</span></label>
              <textarea id="meeting-purpose" className="memory-edit-text" rows={3} maxLength={300} value={draft.purpose}
                placeholder="e.g. Review the week and how aligned it was with getting back to enterprise AI." onChange={e => patch({ purpose: e.target.value })} />
              <label className="task-label" htmlFor="meeting-agenda">Agenda <span className="task-optional">one item per line, up to 8</span></label>
              <textarea id="meeting-agenda" className="memory-edit-text" rows={5} value={draft.agenda}
                placeholder={'What did you get done this week?\nWhich jobs did you apply to?\nHow aligned was it?'} onChange={e => patch({ agenda: e.target.value })} />
              <div className="memory-edit-hint">Sonic opens the meeting with the first agenda item, asks one question at a time, and brings your goals, what your tasks found since last time, and the last meeting's notes.</div>
            </div>
            {formError && <div className="memory-edit-error" role="alert">{formError}</div>}
            <div className="memory-edit-row task-wizard-nav">
              <button type="button" className="memory-edit-cancel" disabled={saving} onClick={() => setEditing(null)}>Cancel</button>
              <button type="button" className="memory-edit-save" disabled={saving || draft.name.trim().length < 2} onClick={handleSave}>{saving ? 'Saving...' : 'Save meeting'}</button>
            </div>
          </section>
        )}
      </div>
    </div>
  );
};

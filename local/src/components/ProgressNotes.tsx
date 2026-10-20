import React, { useEffect, useState } from 'react';
import { api, type ProgressLabel, type ProgressNote } from '../api';
import './__styles__/ProgressNotes.css';

const LABELS: { value: ProgressLabel; text: string }[] = [
  { value: 'note', text: 'Note' },
  { value: 'win', text: 'Win' },
  { value: 'blocker', text: 'Blocker' },
];

function when(note: ProgressNote): string {
  const date = new Date(note.at);
  return Number.isNaN(date.getTime()) ? note.day : date.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' });
}

/** One meeting's progress notes: what you log to it between sessions. Sonic lists the ones that meeting has not covered when you start it, in your own words. */
export const ProgressNotes: React.FC<{ meetingId: string; meetingName: string }> = ({ meetingId, meetingName }) => {
  const [notes, setNotes] = useState<ProgressNote[]>([]);
  const [maxText, setMaxText] = useState(400);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [text, setText] = useState('');
  const [label, setLabel] = useState<ProgressLabel>('note');
  const [saving, setSaving] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const [editText, setEditText] = useState('');

  useEffect(() => {
    let cancelled = false;
    api.listProgressNotes(meetingId)
      .then(data => { if (!cancelled) { setNotes(data.notes); setMaxText(data.limits.max_text); } })
      .catch(err => { if (!cancelled) setError(err instanceof Error ? err.message : 'Failed to load your progress notes.'); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [meetingId]);

  const add = async () => {
    if (!text.trim()) return;
    setSaving(true);
    setError('');
    try {
      const created = await api.addProgressNote(meetingId, text.trim(), label);
      setNotes(prev => [created, ...prev]);
      setText('');
      setLabel('note');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save that note.');
    } finally {
      setSaving(false);
    }
  };

  const save = async (note: ProgressNote) => {
    try {
      const updated = await api.updateProgressNote(note.id, { text: editText });
      setNotes(prev => prev.map(n => (n.id === note.id ? updated : n)));
      setEditing(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update that note.');
    }
  };

  const remove = async (note: ProgressNote) => {
    if (!window.confirm('Delete this note?')) return;
    try {
      await api.deleteProgressNote(note.id);
      setNotes(prev => prev.filter(n => n.id !== note.id));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete that note.');
    }
  };

  const waiting = notes.filter(n => !n.covered).length;

  return (
    <div className="task-results progress-notes-panel">
      <p className="task-meta">
        {waiting} waiting for the next "{meetingName}". Log what happens between meetings, in your own words; when you start it, Sonic has the notes it has not covered yet in front of it. In any chat,
        write <strong>log to {meetingName}: ...</strong> (or <strong>log a win to {meetingName}: ...</strong>); inside a running meeting, <strong>log: ...</strong> goes to it. To update one, quote part of its text,
        for example <strong>add a win to my note "applied to three jobs": got a second interview</strong>.
      </p>
      {error && <div className="memory-edit-error" role="alert">{error}</div>}

      <div className="progress-note-add">
        <textarea
          className="memory-edit-text"
          rows={2}
          maxLength={maxText}
          value={text}
          placeholder="Applied to three jobs"
          aria-label="New progress note"
          onChange={e => setText(e.target.value)}
        />
        <div className="memory-edit-row">
          <select className="progress-note-label" value={label} aria-label="Kind of note" onChange={e => setLabel(e.target.value as ProgressLabel)}>
            {LABELS.map(l => <option key={l.value} value={l.value}>{l.text}</option>)}
          </select>
          <button type="button" className="memory-edit-save" disabled={saving || !text.trim()} onClick={add}>{saving ? 'Saving...' : 'Add note'}</button>
        </div>
      </div>

      {loading && <div className="task-meta">Loading...</div>}
      {!loading && notes.length === 0 && <div className="task-meta">Nothing logged to this meeting yet. Anything you log shows up here, and in its next session.</div>}
      <div className="memory-list tasks-list">
        {notes.map(note => (
          <div key={note.id} className="memory-row task-row">
            <div className="memory-row-main">
              <div className="memory-row-badges">
                <span className="memory-badge memory-source-badge">{when(note)}</span>
                {note.label !== 'note' && <span className={`memory-badge progress-note-${note.label}`}>{note.label}</span>}
                {note.covered
                  ? <span className="memory-badge memory-source-badge">covered in this meeting</span>
                  : <span className="memory-badge task-new-badge">for the next one</span>}
              </div>
              {editing === note.id ? (
                <div className="meeting-note-edit">
                  <textarea className="memory-edit-text" rows={2} maxLength={maxText} value={editText} aria-label="Edit note" onChange={e => setEditText(e.target.value)} />
                  <div className="memory-edit-row">
                    <button type="button" className="memory-edit-cancel" onClick={() => setEditing(null)}>Cancel</button>
                    <button type="button" className="memory-edit-save" disabled={!editText.trim()} onClick={() => save(note)}>Save</button>
                  </div>
                </div>
              ) : (
                <div className="memory-row-text">{note.text}</div>
              )}
            </div>
            {editing !== note.id && (
              <div className="task-actions">
                <button type="button" className="memory-edit-cancel" onClick={() => { setEditing(note.id); setEditText(note.text); }}>Edit</button>
                <button type="button" className="delete-row-btn" onClick={() => remove(note)}>Delete</button>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
};

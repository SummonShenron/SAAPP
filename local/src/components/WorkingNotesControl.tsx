import React, { useCallback, useEffect, useRef, useState } from 'react';
import { api, type WorkingNoteItem, type WorkingNotes } from '../api';
import Icon from './icons';
import './__styles__/WorkingNotesControl.css';

interface Props {
  sessionId: string;
  /** True while a reply is being made; the notes are brought up to date after a coding reply, so they are read again a little after it ends. */
  busy: boolean;
  /** How many messages the person has sent in this conversation (a note is not written before a few). */
  turns: number;
}

/** Look again this long after a reply ends: the update is one small model call that runs after the reply is sent. */
const RECHECK_MS = [6000, 16000];

/**
 * The chat header's working notes: what this conversation has decided, ruled out and left open, as Sonic keeps it for coding work. It only appears once there is a note.
 * Everything in it can be read, edited, deleted or added to here; what the person wrote or changed is never touched by Sonic again. Nothing here leaves the conversation.
 */
export const WorkingNotesControl: React.FC<Props> = ({ sessionId, busy, turns }) => {
  const [notes, setNotes] = useState<WorkingNotes | null>(null);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<{ id: string; text: string } | null>(null);
  const [adding, setAdding] = useState<{ kind: string; text: string } | null>(null);
  const [message, setMessage] = useState('');
  const wasBusy = useRef(false);
  const wrapRef = useRef<HTMLDivElement | null>(null);

  const load = useCallback(async () => {
    try {
      setNotes(await api.getWorkingNotes(sessionId));
    } catch {
      /* no notes control is the normal outcome when there is nothing to show */
    }
  }, [sessionId]);

  useEffect(() => {
    setNotes(null);
    setOpen(false);
    setEditing(null);
    setAdding(null);
    void load();
  }, [sessionId, load]);

  useEffect(() => {
    if (busy) {
      wasBusy.current = true;
      return undefined;
    }
    if (!wasBusy.current || turns < 3) return undefined;
    wasBusy.current = false;
    const timers = RECHECK_MS.map(ms => window.setTimeout(() => void load(), ms));
    return () => timers.forEach(timer => window.clearTimeout(timer));
  }, [busy, turns, load]);

  useEffect(() => {
    if (!open) return undefined;
    const onDown = (event: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [open]);

  if (!notes || notes.empty) return null;

  const count = (notes.goal ? 1 : 0) + notes.sections.reduce((sum, section) => sum + section.items.length, 0);

  const change = async (itemId: string, text: string | null, kind?: string) => {
    setMessage('');
    try {
      setNotes(await api.editWorkingNote(sessionId, itemId, text, kind));
      setEditing(null);
      setAdding(null);
    } catch (err) {
      setMessage(err instanceof Error ? err.message : 'Failed to change that note.');
    }
  };

  const clear = async () => {
    if (!window.confirm('Clear the working notes for this conversation?')) return;
    try {
      await api.clearWorkingNotes(sessionId);
      setNotes(null);
      setOpen(false);
    } catch (err) {
      setMessage(err instanceof Error ? err.message : 'Failed to clear the notes.');
    }
  };

  const row = (item: WorkingNoteItem) => (
    <li key={item.id} className="working-notes-item">
      {editing?.id === item.id ? (
        <form className="working-notes-edit" onSubmit={event => { event.preventDefault(); void change(item.id, editing.text); }}>
          <input autoFocus value={editing.text} maxLength={140} onChange={event => setEditing({ id: item.id, text: event.target.value })} aria-label="Edit this note" />
          <button type="submit" className="working-notes-icon" aria-label="Save"><Icon name="check" size={14} /></button>
          <button type="button" className="working-notes-icon" aria-label="Cancel" onClick={() => setEditing(null)}><Icon name="close" size={14} /></button>
        </form>
      ) : (
        <>
          <button type="button" className="working-notes-text" onClick={() => setEditing({ id: item.id, text: item.text })}
            title={item.by === 'person' ? 'Written by you. Click to edit.' : item.evidence ? `From the conversation: "${item.evidence}". Click to edit.` : 'Click to edit.'}>
            {item.text}
            {item.by === 'person' && <span className="working-notes-yours">yours</span>}
          </button>
          <button type="button" className="working-notes-icon" aria-label="Delete this note" onClick={() => void change(item.id, null)}><Icon name="trash" size={14} /></button>
        </>
      )}
    </li>
  );

  return (
    <div className="working-notes" ref={wrapRef}>
      <button type="button" className="topbar-btn working-notes-btn" aria-expanded={open} aria-label="Working notes for this conversation" title="Working notes for this conversation" onClick={() => setOpen(v => !v)}>
        <Icon name="notes" size={18} />
        <span className="working-notes-count" aria-hidden="true">{count}</span>
      </button>
      {open && (
        <div className="working-notes-panel" role="dialog" aria-label="Working notes">
          <p className="working-notes-intro">Kept for this conversation only. Sonic reads these while it works on code. Change anything and Sonic won't undo it.</p>
          {notes.goal && (
            <section>
              <h4>Goal</h4>
              <ul>{row(notes.goal)}</ul>
            </section>
          )}
          {notes.sections.map(section => (
            <section key={section.kind}>
              <h4>{section.label}</h4>
              <ul>{section.items.map(row)}</ul>
              {adding?.kind === section.kind ? (
                <form className="working-notes-edit" onSubmit={event => { event.preventDefault(); void change('new', adding.text, section.kind); }}>
                  <input autoFocus value={adding.text} maxLength={140} placeholder="Add a note" onChange={event => setAdding({ kind: section.kind, text: event.target.value })} aria-label={`Add to ${section.label}`} />
                  <button type="submit" className="working-notes-icon" aria-label="Add"><Icon name="check" size={14} /></button>
                  <button type="button" className="working-notes-icon" aria-label="Cancel" onClick={() => setAdding(null)}><Icon name="close" size={14} /></button>
                </form>
              ) : (
                <button type="button" className="working-notes-add" onClick={() => setAdding({ kind: section.kind, text: '' })}>+ Add</button>
              )}
            </section>
          ))}
          {message && <p className="working-notes-msg" role="status">{message}</p>}
          <button type="button" className="working-notes-clear" onClick={() => void clear()}>Clear these notes</button>
        </div>
      )}
    </div>
  );
};

export default WorkingNotesControl;

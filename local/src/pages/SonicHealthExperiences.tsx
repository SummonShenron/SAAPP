import React, { useCallback, useEffect, useState } from 'react';
import { api, type ExperienceNote, type ExperienceReflection, type ExperiencesView } from '../api';
import { shortDate } from './sonicHealthFormat';

const SIGNAL_LABELS: Record<string, string> = {
  pushback: 'the next message read as pushback',
  step_limit: 'the turn ran into its step limit',
  answer_check: 'the answer check found an unsupported claim',
  praise: 'the next message praised it',
};

/**
 * Sonic's record of its own work: abstract, dated notes of things that really happened, which it may cite as "a case in my record". New ones are written when an objective signal fires and wait
 * here, never cited and never put in a prompt, until approved. A note holds no text from any conversation: one sentence, checked by code. Editing a note re-checks it the same way.
 * Each note that can be cited may also carry a reflection: what Sonic takes from it for itself, kept pending until approved, said only as an extra sentence when the note is cited.
 */
export const ExperiencesTab: React.FC = () => {
  const [view, setView] = useState<ExperiencesView | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const [editing, setEditing] = useState<{ id: string; text: string } | null>(null);
  const [editingReflection, setEditingReflection] = useState<{ id: string; text: string } | null>(null);

  const load = useCallback(async () => {
    try {
      setView(await api.getExperiences());
      setError('');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load the record.');
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const run = async (id: string, action: () => Promise<ExperiencesView>) => {
    setBusy(id);
    setError('');
    try {
      setView(await action());
      setEditing(null);
      setEditingReflection(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'That did not work.');
    } finally {
      setBusy(null);
    }
  };

  const writeReflections = async () => {
    setBusy('write');
    setError('');
    setNotice('');
    try {
      const result = await api.writeExperienceReflections();
      setView(result.view);
      setNotice(`${result.pending} reflection${result.pending === 1 ? '' : 's'} written and waiting; ${result.nothing} had nothing concrete to say${result.errors ? `; ${result.errors} failed and can be tried again` : ''}.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not write the reflections.');
    } finally {
      setBusy(null);
    }
  };

  const reflectionBlock = (id: string, reflection: ExperienceReflection | undefined) => {
    if (!reflection || !reflection.status || reflection.status === 'none' || reflection.status === 'removed') return null;
    const pending = reflection.status === 'pending';
    return (
      <div className="sh-reflection">
        <div className="sh-row-meta">{pending ? 'What it would take from this (pending: not said until you approve it):' : 'What it takes from this (approved, said only when the note is cited):'}</div>
        {editingReflection?.id === id ? (
          <>
            <textarea className="sh-textarea" value={editingReflection.text} rows={3} onChange={event => setEditingReflection({ id, text: event.target.value })} aria-label="Edit this reflection" />
            <div className="sh-row-buttons">
              <button type="button" className="sh-btn sh-btn-primary" disabled={busy === id} onClick={() => run(id, () => api.decideExperienceReflection(id, 'edit', editingReflection.text))}>Save (back to pending)</button>
              <button type="button" className="sh-btn" onClick={() => setEditingReflection(null)}>Cancel</button>
            </div>
          </>
        ) : (
          <>
            <div className="sh-row-text">{reflection.text}</div>
            <div className="sh-row-buttons">
              {pending && <button type="button" className="sh-btn sh-btn-primary" disabled={busy === id} onClick={() => run(id, () => api.decideExperienceReflection(id, 'approve'))}>Approve</button>}
              <button type="button" className="sh-btn" disabled={busy === id} onClick={() => setEditingReflection({ id, text: reflection.text })}>Edit</button>
              <button type="button" className="sh-btn" disabled={busy === id} onClick={() => { if (window.confirm('Remove this reflection for good?')) void run(id, () => api.decideExperienceReflection(id, 'remove')); }}>Remove</button>
            </div>
          </>
        )}
      </div>
    );
  };

  const row = (note: ExperienceNote, pending: boolean) => (
    <li key={note.id} className="sh-row">
      {editing?.id === note.id ? (
        <div className="sh-row-body">
          <textarea className="sh-textarea" value={editing.text} rows={3} onChange={event => setEditing({ id: note.id, text: event.target.value })} aria-label="Edit this note" />
          <div className="sh-row-buttons">
            <button type="button" className="sh-btn sh-btn-primary" disabled={busy === note.id} onClick={() => run(note.id, () => api.editExperience(note.id, editing.text))}>Save</button>
            <button type="button" className="sh-btn" onClick={() => setEditing(null)}>Cancel</button>
          </div>
        </div>
      ) : (
        <div className="sh-row-body">
          <div className="sh-row-text">{note.what}</div>
          <div className="sh-row-meta">
            {shortDate(note.on)} · {note.kind} · {SIGNAL_LABELS[note.signal] ?? note.signal} · {note.area}{note.count > 1 ? ` · happened ${note.count} times` : ''}
          </div>
          <div className="sh-row-buttons">
            {pending && <button type="button" className="sh-btn sh-btn-primary" disabled={busy === note.id} onClick={() => run(note.id, () => api.approveExperience(note.id))}>Approve</button>}
            <button type="button" className="sh-btn" disabled={busy === note.id} onClick={() => setEditing({ id: note.id, text: note.what })}>Edit</button>
            <button type="button" className="sh-btn" disabled={busy === note.id} onClick={() => { if (window.confirm('Remove this note for good?')) void run(note.id, () => api.deleteExperience(note.id)); }}>Delete</button>
          </div>
          {!pending && reflectionBlock(`db-${note.id}`, note.reflection)}
        </div>
      )}
    </li>
  );

  if (!view) return <section className="service-card sh-card"><h2>Its record</h2><p className="card-description">{error || 'Loading...'}</p></section>;

  const withoutReflection = view.registry.filter(note => !note.reflection.status).length + view.approved.filter(note => !note.reflection?.status).length;

  return (
    <>
      <section className="service-card sh-card">
        <div className="card-badge">{view.pending.length} WAITING</div>
        <h2>New notes for its record</h2>
        <p className="card-description">
          When an objective signal fires (pushback, a step limit, an unsupported claim, praise) one small call writes a one-sentence abstract note of what happened. It waits here and is never cited until you
          approve it. At most {view.limits.per_day} a day and {view.limits.waiting} waiting; a note close to one already recorded adds to that one's count instead; waiting notes expire after {view.limits.expires_days} days.
        </p>
        {error && <div className="account-error" role="alert">{error}</div>}
        {view.pending.length === 0 ? <p className="card-description">Nothing waiting.</p> : <ul className="sh-list">{view.pending.map(note => row(note, true))}</ul>}
      </section>
      <section className="service-card sh-card">
        <div className="card-badge">{view.approved.length} APPROVED</div>
        <h2>Notes you approved</h2>
        {view.approved.length === 0 ? <p className="card-description">None yet.</p> : <ul className="sh-list">{view.approved.map(note => row(note, false))}</ul>}
      </section>
      <section className="service-card sh-card">
        <div className="card-badge">{view.registry.length} NOTES</div>
        <h2>Reflections on its record</h2>
        <p className="card-description">
          For each note it can cite, a reflection is what it takes from that for itself, as a system: for a failure, the concrete thing it does about it when that shape shows up (never just that it happened). Written
          by the small model, held to the same checks as the reading shelf's, and pending until you approve it. {withoutReflection} note{withoutReflection === 1 ? ' has' : 's have'} none yet; up to {view.limits.reflections_at_once} are written at a time.
        </p>
        <div className="sh-row-buttons">
          <button type="button" className="sh-btn sh-btn-primary" disabled={busy === 'write' || withoutReflection === 0} onClick={() => void writeReflections()}>{busy === 'write' ? 'Writing...' : 'Write reflections'}</button>
        </div>
        {notice && <p className="card-description" role="status">{notice}</p>}
        <details>
          <summary>The notes written by hand, from its history, on each working leaning ({view.registry.length})</summary>
          <ul className="sh-list">
            {view.registry.map(note => (
              <li key={note.id} className="sh-row">
                <div className="sh-row-body">
                  <div className="sh-row-text">{note.what}</div>
                  <div className="sh-row-meta">{shortDate(note.on)} · {note.kind} · on {note.leaning.replace(/_/g, ' ')}</div>
                  {reflectionBlock(note.id, note.reflection)}
                </div>
              </li>
            ))}
          </ul>
        </details>
      </section>
    </>
  );
};

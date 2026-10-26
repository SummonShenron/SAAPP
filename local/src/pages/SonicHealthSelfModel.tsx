import React, { useCallback, useEffect, useState } from 'react';
import { api, type SelfModelStatement, type SelfModelView } from '../api';

/**
 * What kind of system Sonic is, said only from evidence. Code gathers the facts (this week's mix of work, its routines, goals, reading, leanings, record, tool use and coding numbers); the small model
 * writes a few statements that each cite the facts they rest on; code checks every one (cited facts exist, any number is in them, a system speaking, nothing felt or wanted, no stake in its numbers).
 * Statements wait here until approved. An approved one goes stale when a fact it rests on reads differently, and is then not said until it is looked at again.
 */
export const SelfModelTab: React.FC = () => {
  const [view, setView] = useState<SelfModelView | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const [editing, setEditing] = useState<{ id: string; text: string } | null>(null);

  const load = useCallback(async () => {
    try {
      setView(await api.getSelfModel());
      setError('');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load the self-model.');
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const run = async (id: string, action: () => Promise<SelfModelView>) => {
    setBusy(id);
    setError('');
    try {
      setView(await action());
      setEditing(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'That did not work.');
    } finally {
      setBusy(null);
    }
  };

  const write = async () => {
    setBusy('write');
    setError('');
    setNotice('');
    try {
      const result = await api.writeSelfModel();
      setView(result.view);
      setNotice(result.error ? 'The model could not write them just now. Try again.' : `${result.stored ?? 0} statement${result.stored === 1 ? '' : 's'} written and waiting; ${result.dropped ?? 0} failed a check and were dropped (from ${result.facts ?? 0} facts).`);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not write the statements.');
    } finally {
      setBusy(null);
    }
  };

  const statement = (item: SelfModelStatement, pending: boolean) => (
    <li key={item.id} className="sh-row">
      {editing?.id === item.id ? (
        <div className="sh-row-body">
          <textarea className="sh-textarea" value={editing.text} rows={3} onChange={event => setEditing({ id: item.id, text: event.target.value })} aria-label="Edit this statement" />
          <div className="sh-row-buttons">
            <button type="button" className="sh-btn sh-btn-primary" disabled={busy === item.id} onClick={() => run(item.id, () => api.editSelfModelStatement(item.id, editing.text))}>Save (back to pending)</button>
            <button type="button" className="sh-btn" onClick={() => setEditing(null)}>Cancel</button>
          </div>
        </div>
      ) : (
        <div className="sh-row-body">
          <div className="sh-row-text">{item.text}{item.stale ? ' (stale: a fact it rests on has changed, so it is not being said)' : ''}</div>
          <details>
            <summary className="sh-row-meta">Rests on {item.cites.length} fact{item.cites.length === 1 ? '' : 's'}</summary>
            <ul className="sh-list">{item.cites.map(cite => <li key={cite.key} className="sh-row-meta">{cite.text}</li>)}</ul>
          </details>
          <div className="sh-row-buttons">
            {pending && <button type="button" className="sh-btn sh-btn-primary" disabled={busy === item.id} onClick={() => run(item.id, () => api.approveSelfModelStatement(item.id))}>Approve</button>}
            <button type="button" className="sh-btn" disabled={busy === item.id} onClick={() => setEditing({ id: item.id, text: item.text })}>Edit</button>
            <button type="button" className="sh-btn" disabled={busy === item.id} onClick={() => { if (window.confirm('Retire this statement?')) void run(item.id, () => api.retireSelfModelStatement(item.id)); }}>Retire</button>
          </div>
        </div>
      )}
    </li>
  );

  if (!view) return <section className="service-card sh-card"><h2>Self-model</h2><p className="card-description">{error || 'Loading...'}</p></section>;

  return (
    <>
      <section className="service-card sh-card">
        <div className="card-badge">{view.pending.length} WAITING</div>
        <h2>What kind of system I am</h2>
        <p className="card-description">
          Statements Sonic can say about itself, each written from the facts below and checked by code. They wait here until you approve them; up to {view.limits.used} approved, current ones are
          used when someone asks what kind of system it is, what it spends its time on, or how it works.
        </p>
        <div className="sh-row-buttons">
          <button type="button" className="sh-btn sh-btn-primary" disabled={busy === 'write'} onClick={() => void write()}>{busy === 'write' ? 'Writing...' : 'Write from the current facts'}</button>
        </div>
        {notice && <p className="card-description" role="status">{notice}</p>}
        {error && <div className="account-error" role="alert">{error}</div>}
        {view.pending.length === 0 ? <p className="card-description">Nothing waiting.</p> : <ul className="sh-list">{view.pending.map(item => statement(item, true))}</ul>}
      </section>
      <section className="service-card sh-card">
        <div className="card-badge">{view.approved.length} APPROVED</div>
        <h2>What it says about itself</h2>
        {view.approved.length === 0 ? <p className="card-description">None yet.</p> : <ul className="sh-list">{view.approved.map(item => statement(item, false))}</ul>}
      </section>
      <section className="service-card sh-card">
        <div className="card-badge">{view.facts.length} FACTS</div>
        <h2>The facts it is built from</h2>
        <p className="card-description">Gathered by code from the real stores, rounded, and kept for ten minutes. Every statement above cites some of these; nothing else may be relied on.</p>
        <ul className="sh-list">{view.facts.map(fact => <li key={fact.key} className="sh-row"><div className="sh-row-body"><div className="sh-row-text">{fact.text}</div></div></li>)}</ul>
      </section>
    </>
  );
};

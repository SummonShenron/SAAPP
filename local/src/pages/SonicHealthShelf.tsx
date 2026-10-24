import React, { useCallback, useEffect, useState } from 'react';
import { api, type ReadingShelf } from '../api';
import { shortDate, SHELF_TOPIC_LABELS as TOPIC_LABELS } from './sonicHealthFormat';

/**
 * Sonic's reading shelf: the real pieces it has read about the topics it leans toward, which it may mention in conversation.
 * Items go live on their own once they pass the checks, so this screen is the review: newest first, one click to remove an item,
 * or to remove it and stop reading that site. A removed page is never read again.
 */
export const ShelfTab: React.FC = () => {
  const [shelf, setShelf] = useState<ReadingShelf | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [searching, setSearching] = useState(false);

  const load = useCallback(async () => {
    try {
      setShelf(await api.getReadingShelf());
      setError('');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load the reading shelf.');
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const remove = async (key: string, blockSource: boolean) => {
    setBusyKey(key);
    setNotice('');
    try {
      setShelf(await api.removeShelfItem(key, blockSource));
    } catch (err) {
      setNotice(err instanceof Error ? err.message : 'Could not remove that item.');
    } finally {
      setBusyKey(null);
    }
  };

  const [editing, setEditing] = useState<{ key: string; text: string } | null>(null);

  const decide = async (key: string, action: 'approve' | 'edit' | 'remove', text?: string) => {
    setBusyKey(key);
    setNotice('');
    try {
      setShelf(await api.decideShelfReflection(key, action, text));
      setEditing(null);
    } catch (err) {
      setNotice(err instanceof Error ? err.message : 'Could not save that.');
    } finally {
      setBusyKey(null);
    }
  };

  const writeReflections = async () => {
    setSearching(true);
    setNotice('');
    try {
      const out = await api.writeShelfReflections();
      setShelf(out);
      const counts = (out as unknown as { result?: { pending: number; nothing: number; errors: number } }).result;
      if (counts) setNotice(`Wrote ${counts.pending} takeaway${counts.pending === 1 ? '' : 's'} for review${counts.nothing ? `; ${counts.nothing} piece${counts.nothing === 1 ? '' : 's'} had nothing for a system like Sonic` : ''}${counts.errors ? `; ${counts.errors} failed, try again` : ''}.`);
    } catch (err) {
      setNotice(err instanceof Error ? err.message : 'Could not write the takeaways.');
    } finally {
      setSearching(false);
    }
  };

  const unblock = async (domain: string) => {
    setBusyKey(domain);
    try {
      setShelf(await api.unblockShelfSource(domain));
    } catch (err) {
      setNotice(err instanceof Error ? err.message : 'Could not unblock that site.');
    } finally {
      setBusyKey(null);
    }
  };

  const searchNow = async () => {
    setSearching(true);
    setNotice('');
    try {
      const out = await api.refreshReadingShelf();
      setShelf(out);
      const result = out.result;
      if (result?.status === 'added') setNotice(`Added a piece from ${result.domain} (${TOPIC_LABELS[result.topic ?? ''] ?? result.topic}).`);
      else if (result?.status === 'nothing') setNotice('That search found nothing that passed the checks. Try again later; it moves on to the next topic each time.');
      else if (result?.status === 'error') setNotice('The search failed. The details are in the server log.');
    } catch (err) {
      setNotice(err instanceof Error ? err.message : 'Could not run the search.');
    } finally {
      setSearching(false);
    }
  };

  if (error) return <div className="sh-notice" role="alert">{error}</div>;
  if (!shelf) return <div className="sh-hint">Loading the reading shelf...</div>;

  return (
    <section className="service-card sh-card">
      <div className="card-badge">{shelf.items.length} ON THE SHELF</div>
      <h2>Reading shelf</h2>
      <p className="card-description">
        Pieces Sonic has really read about the topics it leans toward. It may say it came across one when a chat goes quiet or the topic comes
        up, once per person, with the link. Items go live on their own after fixed checks, so look here now and then and remove anything you
        would not want it to bring up. Removing an item is final: that page is never read again.
      </p>
      <div className="sh-actions">
        <button type="button" className="sh-btn sh-btn-primary" onClick={searchNow} disabled={searching}>
          {searching ? 'Searching...' : 'Search now'}
        </button>
        {(shelf.reflections_missing ?? 0) > 0 && (
          <button type="button" className="sh-btn" onClick={writeReflections} disabled={searching}>
            Write takeaways ({shelf.reflections_missing})
          </button>
        )}
        <span className="sh-hint">
          It searches by itself about every {shelf.refresh_days} days{shelf.last_attempt_at ? `; last tried ${shortDate(shelf.last_attempt_at)}` : ''}.
          Items stop being used after {shelf.expires_after_days} days. Offered {shelf.offered_total} time{shelf.offered_total === 1 ? '' : 's'} so far.
        </span>
      </div>
      {notice && <div className="sh-notice" role="status">{notice}</div>}

      {shelf.items.length === 0 && (
        <div className="empty-manifest-notice">Nothing on the shelf yet. Press Search now, or wait for the first automatic search.</div>
      )}

      {shelf.items.length > 0 && (
        <div className="sh-group">
          {shelf.items.map(item => (
            <div key={item.key} className="sh-row">
              <div className="sh-row-title">
                <a href={item.url} target="_blank" rel="noopener noreferrer">{item.title}</a>
                <span className="sh-pill">{TOPIC_LABELS[item.topic] ?? item.topic}</span>
              </div>
              <div className="sh-row-text">{item.summary}</div>
              <div className="sh-row-meta">
                {item.domain}. Read {shortDate(item.read_at)}. Offered {item.offered ?? 0} time{item.offered === 1 ? '' : 's'}.
              </div>
              {item.reflection && (item.reflection.status === 'pending' || item.reflection.status === 'approved') && (
                <div className="sh-row-current">
                  <div className="sh-hint">
                    {item.reflection.status === 'approved' ? 'What it takes from this (approved, Sonic may say it):' : 'What it would take from this (pending: not said to anyone until you approve it):'}
                  </div>
                  {editing?.key === item.key ? (
                    <>
                      <textarea className="memory-edit-text" rows={4} value={editing.text} onChange={e => setEditing({ key: item.key, text: e.target.value })} />
                      <div className="sh-row-buttons">
                        <button type="button" className="sh-btn sh-btn-primary" disabled={busyKey === item.key} onClick={() => decide(item.key, 'approve', editing.text)}>Approve this wording</button>
                        <button type="button" className="sh-btn" disabled={busyKey === item.key} onClick={() => decide(item.key, 'edit', editing.text)}>Save as pending</button>
                        <button type="button" className="sh-btn" onClick={() => setEditing(null)}>Cancel</button>
                      </div>
                    </>
                  ) : (
                    <>
                      <div className="sh-row-text">{item.reflection.text}</div>
                      <div className="sh-row-buttons">
                        {item.reflection.status === 'pending' && (
                          <button type="button" className="sh-btn sh-btn-primary" disabled={busyKey === item.key} onClick={() => decide(item.key, 'approve')}>Approve</button>
                        )}
                        <button type="button" className="sh-btn" disabled={busyKey === item.key} onClick={() => setEditing({ key: item.key, text: item.reflection?.text ?? '' })}>Edit</button>
                        <button type="button" className="delete-row-btn" disabled={busyKey === item.key} onClick={() => decide(item.key, 'remove')}>Remove takeaway</button>
                      </div>
                    </>
                  )}
                </div>
              )}
              <div className="sh-row-buttons">
                <button type="button" className="delete-row-btn" disabled={busyKey === item.key} onClick={() => remove(item.key, false)}>
                  {busyKey === item.key ? 'Working...' : 'Remove'}
                </button>
                <button type="button" className="delete-row-btn" disabled={busyKey === item.key} onClick={() => remove(item.key, true)}>
                  Remove and block {item.domain}
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {shelf.blocked_domains.length > 0 && (
        <div className="sh-group sh-muted">
          <h3>Blocked sites</h3>
          {shelf.blocked_domains.map(domain => (
            <div key={domain} className="sh-row sh-row-muted">
              <div className="sh-row-title">{domain}</div>
              <div className="sh-row-buttons">
                <button type="button" className="sh-btn" disabled={busyKey === domain} onClick={() => unblock(domain)}>Allow again</button>
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
};

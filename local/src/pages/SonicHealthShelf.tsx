import React, { useCallback, useEffect, useState } from 'react';
import { api, type ReadingShelf } from '../api';
import { shortDate } from './sonicHealthFormat';

const TOPIC_LABELS: Record<string, string> = {
  bug_hunts: 'Bug hunts',
  security: 'Security',
  game_design: 'Game design',
  game_lore: 'Game lore',
  computing_history: 'Computing history',
  ai_research: 'AI research',
  tabletop: 'Tabletop design',
  scifi: 'Science fiction',
};

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

import React, { useCallback, useEffect, useState } from 'react';
import { api, type LearnedCard, type LearnedKnowledge, type PendingCard } from '../api';
import { shortDate } from './sonicHealthFormat';

/**
 * Sonic's own knowledge base: cards it wrote from public web pages someone asked it to learn (backend/utils/knowledge_base.py). Any signed-in member can ask; only an admin, here, decides.
 * A card WAITS here until it is approved; only then can anyone's Sonic read it. That approval is what stops anyone poisoning what everyone is told. It is a cache with a hard ceiling: when the base is full, the card
 * least worth keeping (one never used, then the one used longest ago) makes room, and an old one expires.
 */
export const LearnedTab: React.FC = () => {
  const [data, setData] = useState<LearnedKnowledge | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busyKey, setBusyKey] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setData(await api.getLearnedKnowledge());
      setError('');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load what Sonic has learned.');
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const decide = async (key: string, action: 'approve' | 'reject' | 'remove') => {
    setBusyKey(key);
    setNotice('');
    try {
      setData(await api.decideLearnedCard(key, action));
    } catch (err) {
      setNotice(err instanceof Error ? err.message : 'Could not save that.');
    } finally {
      setBusyKey(null);
    }
  };

  if (error) return <div className="error-message" role="alert">{error}</div>;
  if (!data) return <div className="empty-manifest-notice">Loading...</div>;

  const waiting = (card: PendingCard) => (
    <div key={card.key} className="sh-row">
      <div className="sh-row-title">
        <a href={card.url} target="_blank" rel="noopener noreferrer">{card.title}</a>
        <span className="sh-pill">{card.kind}</span>
      </div>
      <div className="sh-row-text">{card.body}</div>
      <div className="sh-row-meta">
        {card.domain}. Proposed {shortDate(card.proposed_at)}{card.by === 'member' ? ' at a member\'s request' : card.by === 'admin' ? ' at an admin\'s request' : ''}.
        {' '}Check it against the page before you approve it: everyone's Sonic will read it.
        {card.by === 'member' && ' A member asked for this one, so read the page itself, not just the card.'}
      </div>
      <div className="sh-row-buttons">
        <button type="button" className="sh-btn sh-btn-primary" disabled={busyKey === card.key} onClick={() => decide(card.key, 'approve')}>
          {busyKey === card.key ? 'Working...' : 'Approve'}
        </button>
        <button type="button" className="delete-row-btn" disabled={busyKey === card.key} onClick={() => decide(card.key, 'reject')}>Reject (never propose this page again)</button>
      </div>
    </div>
  );

  const live = (card: LearnedCard) => (
    <div key={card.key} className="sh-row">
      <div className="sh-row-title">
        <a href={card.url} target="_blank" rel="noopener noreferrer">{card.title}</a>
        <span className="sh-pill">{card.kind}</span>
        {card.expired && <span className="sh-pill">expired</span>}
      </div>
      <div className="sh-row-text">{card.body}</div>
      <div className="sh-row-meta">
        {card.domain}. Approved {shortDate(card.approved_at)}; {card.expired ? 'expired' : 'expires'} {shortDate(card.expires_at)}.
        {' '}Used {card.uses} time{card.uses === 1 ? '' : 's'}{card.last_used_at ? `, last ${shortDate(card.last_used_at)}` : ''}.
      </div>
      <div className="sh-row-buttons">
        <button type="button" className="delete-row-btn" disabled={busyKey === card.key} onClick={() => decide(card.key, 'remove')}>
          {busyKey === card.key ? 'Working...' : 'Remove (never learn this page again)'}
        </button>
      </div>
    </div>
  );

  return (
    <section className="service-card sh-card">
      <div className="card-badge">{data.pending.length} WAITING · {data.live.length} LEARNED</div>
      <h2>Learned knowledge</h2>
      <p className="card-description">
        Short cards Sonic wrote from public pages that someone asked it to learn (anyone signed in can say "learn this page" with an address in a chat). A card waits here until an admin approves it;
        only then can anyone's Sonic read it, with its source and date. This is a cache with a ceiling of {data.max_cards} cards (at most about {data.worst_case_mb} MB of the free
        tier): when it is full the card least worth keeping makes room, a card never used first. Articles expire after {data.expiry_days.article} days and profiles after {data.expiry_days.entity}.
      </p>
      <div className="sh-actions">
        <span className="sh-hint">
          {data.live.length} of {data.max_cards} cards used, {data.never_used} never used. {data.proposals_left_today} member proposal{data.proposals_left_today === 1 ? '' : 's'} left today ({data.member_proposals_per_day} each; admins are not limited).
          {' '}{data.tombstones} page{data.tombstones === 1 ? '' : 's'} remembered as gone. A search must score at least {data.min_score}.
        </span>
      </div>
      {notice && <div className="sh-notice" role="status">{notice}</div>}

      <div className="sh-group">
        <h3>Waiting for approval</h3>
        {data.pending.length === 0
          ? <div className="empty-manifest-notice">Nothing is waiting. Anyone signed in can ask Sonic in a chat to learn a page.</div>
          : data.pending.map(waiting)}
      </div>

      <div className="sh-group">
        <h3>Learned</h3>
        {data.live.length === 0
          ? <div className="empty-manifest-notice">Nothing learned yet.</div>
          : data.live.map(live)}
      </div>
    </section>
  );
};

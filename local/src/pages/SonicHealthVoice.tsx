import React, { useCallback, useEffect, useState } from 'react';
import { api, type VoiceReviewAction, type VoiceReviewItem, type VoiceReviewView } from '../api';
import { ACTION_LABELS, FILTERS, FIRST_ROWS, actionsFor, filterItems, stateLine, summaryLine, thinWarning, usageLine, type VoiceFilter } from './voiceReviewLogic';

const HELP_VOICE = { example: 'voice.examples', fact: 'voice.facts' };           // the help-mode tip each card carries (components/helpTips.ts)
const TONE_LABELS: Record<string, string> = { light: 'light moment', hurting: 'someone hurting', any: 'any moment' };

/** Sonic Health, "Its voice": the voice examples and the situation facts Sonic may show itself when it answers, who approved each, and how often each one was matched to a message (backend/utils/voice_review.py). */
export const VoiceTab: React.FC = () => {
  const [view, setView] = useState<VoiceReviewView | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const [filters, setFilters] = useState<Record<VoiceReviewItem['kind'], VoiceFilter>>({ example: 'all', fact: 'all' });
  const [expanded, setExpanded] = useState<Record<VoiceReviewItem['kind'], boolean>>({ example: false, fact: false });

  const load = useCallback(async () => {
    try {
      setView(await api.getVoiceReview());
      setError('');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load the voice.');
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const decide = async (item: VoiceReviewItem, action: VoiceReviewAction) => {
    setBusy(`${item.kind}:${item.id}`);
    setError('');
    try {
      setView(await api.decideVoiceReview(item.kind, item.id, action));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'That did not work.');
    } finally {
      setBusy(null);
    }
  };

  if (!view) return <section className="service-card sh-card"><h2>Its voice</h2><p className="card-description">{error || 'Loading...'}</p></section>;

  const row = (item: VoiceReviewItem) => {
    const key = `${item.kind}:${item.id}`;
    return (
      <li key={key} className="sh-row">
        <div className="sh-row-body">
          {item.text.map(line => <div key={line} className="sh-row-text">{line}</div>)}
          <div className="sh-row-meta">
            {item.kind === 'example' ? `${TONE_LABELS[item.tone] ?? item.tone}. ` : ''}Matched by messages like: &ldquo;{item.asks[0] ?? item.situation}&rdquo;
          </div>
          <div className="sh-row-meta">{`${stateLine(item)} ${usageLine(item, view.days)}`.trim()}</div>
          <div className="sh-row-buttons">
            {actionsFor(item).map(action => (
              <button key={action} type="button" className={action === 'approve' ? 'sh-btn sh-btn-primary' : 'sh-btn'} disabled={busy === key} onClick={() => void decide(item, action)}>{ACTION_LABELS[action]}</button>
            ))}
          </div>
        </div>
      </li>
    );
  };

  const card = (kind: VoiceReviewItem['kind'], title: string, blurb: string) => {
    const shown = filterItems(view.items, kind, filters[kind]);
    const rows = expanded[kind] ? shown : shown.slice(0, FIRST_ROWS);
    const warning = kind === 'example' ? thinWarning(view.items) : '';
    return (
      <section className="service-card sh-card" data-help={HELP_VOICE[kind]}>
        <div className="card-badge">{view.items.filter(item => item.kind === kind && item.approved).length} IN USE</div>
        <h2>{title}</h2>
        <p className="card-description">{blurb}</p>
        <p className="card-description" role="status">{summaryLine(view.items, kind, view.days)}</p>
        {warning && <p className="sh-hint" role="alert">{warning}</p>}
        <div className="sh-filters" role="group" aria-label={`Filter the ${title.toLowerCase()}`}>
          {FILTERS.map(filter => (
            <button key={filter.id} type="button" className={filters[kind] === filter.id ? 'sh-btn sh-btn-primary' : 'sh-btn'} aria-pressed={filters[kind] === filter.id}
              onClick={() => { setFilters(current => ({ ...current, [kind]: filter.id })); setExpanded(current => ({ ...current, [kind]: false })); }}>{filter.label}</button>
          ))}
        </div>
        {shown.length === 0 ? <p className="card-description">Nothing matches that filter.</p> : <ul className="sh-list">{rows.map(row)}</ul>}
        {shown.length > FIRST_ROWS && (
          <div className="sh-row-buttons">
            <button type="button" className="sh-btn" onClick={() => setExpanded(current => ({ ...current, [kind]: !current[kind] }))}>{expanded[kind] ? 'Show fewer' : `Show all ${shown.length}`}</button>
          </div>
        )}
      </section>
    );
  };

  return (
    <>
      {error && <div className="account-error" role="alert">{error}</div>}
      {card('example', 'Voice examples',
        'Invented examples of how Sonic answers, shown next to a message that resembles the one it was written for, so the reply matches the feel and not the wording. Removing one here stops it being used straight away (within a minute); it does not edit the registry file, and "Back to the registry" undoes your decision.')}
      {card('fact', 'Situation facts',
        'One-sentence facts about how a kind of situation works (an interview loop, a hiring queue, a review), shown only to a hurting person facing that situation, so Sonic can say something true and not guess. Every fact starts as a draft: none is used until you approve it here.')}
    </>
  );
};

import React, { useEffect, useState } from 'react';
import { api, type UserFact, type OpenLoop, type OpenLoopChanges, type MemoryStored, type MemoryDomain } from '../api';
import './__styles__/SelfService.css';
import './__styles__/Memory.css';

const CATEGORY_COLORS: Record<string, string> = {
  identity: 'cat-identity',
  preference: 'cat-preference',
  setting: 'cat-setting',
  trait: 'cat-trait',
  career: 'cat-career',
  project: 'cat-project',
  goal: 'cat-goal',
  relationship: 'cat-relationship',
  pattern: 'cat-pattern',
};

const SOURCE_LABELS: Record<string, string> = {
  explicit: 'You told me',
  user_edit: 'You wrote this',
  safety_support: 'You told me',
  inferred: 'Inferred',
  pattern: 'Recurring pattern',
};

// What a note can be filed under when it is edited. A note already under another category (a recurring pattern, a coding habit) keeps it as an option.
const EDITABLE_CATEGORIES = ['preference', 'identity', 'setting', 'trait', 'career', 'project', 'goal', 'relationship'];
const MAX_FACT_CHARS = 500;

// Which part of their life a note is about. Sonic uses it to keep a job and the person's own projects apart ("work" can mean either), so a wrong one is worth fixing here.
const DOMAIN_CHOICES: { value: MemoryDomain; label: string }[] = [
  { value: 'work', label: 'Work' },
  { value: 'own', label: 'My own projects' },
  { value: 'personal', label: 'Personal life' },
  { value: 'general', label: 'General' },
];

function confidenceTone(value: number): string {
  if (value >= 0.7) return 'confidence-high';
  if (value >= 0.4) return 'confidence-medium';
  return 'confidence-low';
}

function formatDate(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
}

// Where a detail of something coming up came from, as the person would say it.
const FIELD_SOURCE_LABELS: Record<string, string> = {
  user: 'you said',
  assistant_confirmed: 'you confirmed',
  calendar: 'your calendar',
  user_edit: 'you wrote this',
};
const EVENT_FIELDS: { key: 'with' | 'where' | 'time'; label: string }[] = [
  { key: 'with', label: 'With' },
  { key: 'where', label: 'Where' },
  { key: 'time', label: 'Time' },
];

interface LoopDraft { due_date: string; time: string; with: string; where: string }

function loopToDraft(loop: OpenLoop): LoopDraft {
  return { due_date: loop.due_date, time: loop.fields?.time?.value ?? '', with: loop.fields?.with?.value ?? '', where: loop.fields?.where?.value ?? '' };
}

function todayKey(): string {
  const now = new Date();
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

// A due date is a calendar day on the user's own clock, so it is shown as that day, never shifted by timezone.
function formatDueDay(day: string): string {
  const [year, month, date] = day.split('-').map(Number);
  const parsed = new Date(year, (month || 1) - 1, date || 1);
  if (Number.isNaN(parsed.getTime())) return day;
  return parsed.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' });
}

export const MemoryPage: React.FC = () => {
  const [facts, setFacts] = useState<UserFact[]>([]);
  const [loops, setLoops] = useState<OpenLoop[]>([]);
  const [stored, setStored] = useState<MemoryStored>({ notes: 0, archived: 0 });
  const [loading, setLoading] = useState<boolean>(true);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [clearing, setClearing] = useState<boolean>(false);
  const [search, setSearch] = useState<string>('');
  const [categoryFilter, setCategoryFilter] = useState<string>('all');
  const [domainFilter, setDomainFilter] = useState<string>('all');
  const [domainSavingId, setDomainSavingId] = useState<string | null>(null);
  const [editingLoopId, setEditingLoopId] = useState<string | null>(null);
  const [loopDraft, setLoopDraft] = useState<LoopDraft>({ due_date: '', time: '', with: '', where: '' });
  const [loopBusyId, setLoopBusyId] = useState<string | null>(null);
  const [loopError, setLoopError] = useState<string>('');
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState<string>('');
  const [draftCategory, setDraftCategory] = useState<string>('preference');
  const [savingId, setSavingId] = useState<string | null>(null);
  const [editError, setEditError] = useState<string>('');

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api.listMemoryFacts()
      .then((data: UserFact[]) => { if (!cancelled) setFacts(data); })
      .catch((err: any) => { console.error('Failed to load memory facts:', err); if (!cancelled) setFacts([]); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    let cancelled = false;
    api.getMemoryStored()
      .then((data: MemoryStored) => { if (!cancelled) setStored(data); })
      .catch((err: unknown) => { console.error('Failed to load stored notes:', err); });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    let cancelled = false;
    api.listOpenLoops()
      .then((data: OpenLoop[]) => { if (!cancelled) setLoops(data); })
      .catch((err: unknown) => { console.error('Failed to load upcoming items:', err); if (!cancelled) setLoops([]); });
    return () => { cancelled = true; };
  }, []);

  const handleDeleteLoop = async (loopId: string, loopText: string) => {
    if (!window.confirm(`Forget "${loopText}"? Sonic won't ask about it.`)) return;
    try {
      await api.deleteOpenLoop(loopId);
      setLoops(prev => prev.filter(l => l.id !== loopId));
    } catch (err) {
      console.error('Failed to delete upcoming item:', err);
      alert('Failed to remove that. Please try again.');
    }
  };

  const startEditingLoop = (loop: OpenLoop) => {
    setEditingLoopId(loop.id);
    setLoopDraft(loopToDraft(loop));
    setLoopError('');
  };

  // Sends only what the person changed. A cleared box goes back to "not known".
  const saveLoopChanges = async (loop: OpenLoop, extra: OpenLoopChanges = {}) => {
    const before = loopToDraft(loop);
    const changes: OpenLoopChanges = { ...extra };
    if (editingLoopId === loop.id) {
      (['due_date', 'time', 'with', 'where'] as const).forEach(key => {
        if (loopDraft[key].trim() !== before[key]) changes[key] = loopDraft[key].trim();
      });
    }
    if (Object.keys(changes).length === 0) { setEditingLoopId(null); return; }
    setLoopBusyId(loop.id);
    setLoopError('');
    try {
      const saved = await api.updateOpenLoop(loop.id, changes);
      setLoops(prev => prev.map(l => (l.id === saved.id ? saved : l)));
      setEditingLoopId(null);
    } catch (err) {
      console.error('Failed to change an upcoming item:', err);
      setLoopError(err instanceof Error ? err.message : 'Failed to save that change.');
    } finally {
      setLoopBusyId(null);
    }
  };

  const handleDeleteFact = async (factId: string, factText: string) => {
    if (!window.confirm(`Forget "${factText}"? This can't be undone.`)) return;
    setDeletingId(factId);
    try {
      await api.deleteMemoryFact(factId);
      setFacts(prev => prev.filter(f => f.id !== factId));
    } catch (err) {
      console.error('Failed to delete memory fact:', err);
      alert('Failed to delete that memory. Please try again.');
    } finally {
      setDeletingId(null);
    }
  };

  const startEditing = (fact: UserFact) => {
    setEditingId(fact.id);
    setDraft(fact.fact);
    setDraftCategory(fact.category);
    setEditError('');
  };

  const cancelEditing = () => {
    setEditingId(null);
    setEditError('');
  };

  const handleSaveEdit = async (fact: UserFact) => {
    const text = draft.trim();
    if (!text) { setEditError("A memory can't be empty. Use the bin icon to forget it instead."); return; }
    if (text === fact.fact.trim() && draftCategory === fact.category) { cancelEditing(); return; }
    setSavingId(fact.id);
    setEditError('');
    try {
      const saved = await api.editMemoryFact(fact.id, text, draftCategory !== fact.category ? draftCategory : undefined);
      setFacts(prev => prev.map(f => (f.id === saved.id ? saved : f)));
      setEditingId(null);
      // Its links were judged again for the new wording and other notes show it in their "Goes with" chips, so their rows are refreshed too.
      api.listMemoryFacts().then(setFacts).catch((err: unknown) => console.error('Failed to refresh memory after an edit:', err));
    } catch (err) {
      console.error('Failed to edit memory fact:', err);
      setEditError(err instanceof Error ? err.message : 'Failed to save that memory. Please try again.');
    } finally {
      setSavingId(null);
    }
  };

  const handleSetDomain = async (fact: UserFact, domain: MemoryDomain) => {
    if (domain === fact.domain) return;
    setDomainSavingId(fact.id);
    setEditError('');
    try {
      const saved = await api.setMemoryDomain(fact.id, domain);
      // Only the label changed, so only this row is replaced (its links and the rest of the list are untouched).
      setFacts(prev => prev.map(f => (f.id === saved.id ? { ...f, ...saved } : f)));
    } catch (err) {
      console.error('Failed to change the memory domain:', err);
      alert(err instanceof Error ? err.message : 'Failed to change that. Please try again.');
    } finally {
      setDomainSavingId(null);
    }
  };

  // Two spellings of one name inside linked notes are treated as one person. If they are not, saying so is final until it is taken back.
  const handleDifferentPeople = async (names: string[]) => {
    try {
      await api.setNameAlias(names[0], names[1], true);
      api.listMemoryFacts().then(setFacts).catch((err: unknown) => console.error('Failed to refresh memory after naming two people:', err));
    } catch (err) {
      console.error('Failed to save the names as different people:', err);
      alert(err instanceof Error ? err.message : 'Failed to save that. Please try again.');
    }
  };

  const handleUnlink = async (factId: string, otherId: string) => {
    try {
      await api.deleteMemoryLink(factId, otherId);
      // The link is removed from both notes, so both rows change.
      setFacts(prev => prev.map(f =>
        f.id === factId || f.id === otherId
          ? { ...f, related_facts: (f.related_facts ?? []).filter(r => r.id !== (f.id === factId ? otherId : factId)) }
          : f
      ));
    } catch (err) {
      console.error('Failed to remove memory link:', err);
      alert('Failed to remove that link. Please try again.');
    }
  };

  const handleClearAll = async () => {
    if (!window.confirm("Forget everything Sonic remembers about you? This permanently deletes your saved memories, the notes from your past conversations, any archived copies of them, the tasks you set up for Sonic along with what they found, and your meetings with Sonic and their notes. Your chats and the feedback you've rated aren't affected. This cannot be undone.")) return;
    setClearing(true);
    try {
      await api.clearMemoryFacts();
      setFacts([]);
      setLoops([]);
      setStored({ notes: 0, archived: 0 });
    } catch (err) {
      console.error('Failed to clear memory:', err);
      alert('Failed to clear memory. Please try again.');
    } finally {
      setClearing(false);
    }
  };

  const categories = Array.from(new Set(facts.map(f => f.category))).sort();

  const filteredFacts = facts
    .filter(f => categoryFilter === 'all' || f.category === categoryFilter)
    .filter(f => domainFilter === 'all' || (domainFilter === 'unsorted' ? !f.domain : f.domain === domainFilter))
    .filter(f => f.fact.toLowerCase().includes(search.toLowerCase()))
    .sort((a, b) => (a.updated_at < b.updated_at ? 1 : -1));

  if (loading) {
    return <div className="self-service-loading">Loading what Sonic remembers about you...</div>;
  }

  return (
    <div className="self-service-container">
      <header className="self-service-header">
        <h1>Memory</h1>
        <p>Here's what Sonic remembers about you — how sure it is, and where it came from.</p>
      </header>

      <div className="vertical-card-stack">
        {loops.length > 0 && (
          <section className="service-card memory-card">
            <div className="card-badge">{loops.filter(l => l.status !== 'cancelled').length} COMING UP</div>
            <h2>Coming Up</h2>
            <p className="card-description">
              Things you mentioned are on the way, with what Sonic knows about each and where it came from. A detail you never gave shows as not known, and Sonic says so
              instead of guessing. Fix anything that's wrong, cancel something that fell through, or remove it. Once the day has passed, Sonic may ask how it went, once.
            </p>
            <div className="memory-list">
              {loops.map(loop => {
                const cancelled = loop.status === 'cancelled';
                const past = loop.due_date < todayKey();
                const busy = loopBusyId === loop.id;
                const editing = editingLoopId === loop.id;
                return (
                  <div key={loop.id} className={`memory-row${past || cancelled ? ' memory-row-past' : ''}${cancelled ? ' memory-row-cancelled' : ''}`}>
                    <div className="memory-row-main">
                      <div className="memory-row-badges">
                        <span className="memory-badge memory-source-badge">{formatDueDay(loop.due_date)}</span>
                        {cancelled && <span className="memory-badge memory-status-badge">cancelled</span>}
                        {!cancelled && past && <span className="memory-badge memory-status-badge">past</span>}
                        {!cancelled && loop.confirmed_at && <span className="memory-badge memory-source-badge">checked by you</span>}
                      </div>
                      <div className="memory-row-text">{loop.text}</div>
                      {editing ? (
                        <div className="memory-event-form">
                          <label>Day <input type="date" value={loopDraft.due_date} min={todayKey()} onChange={e => setLoopDraft(d => ({ ...d, due_date: e.target.value }))} /></label>
                          <label>Time <input type="time" value={loopDraft.time} onChange={e => setLoopDraft(d => ({ ...d, time: e.target.value }))} /></label>
                          <label>With <input type="text" value={loopDraft.with} maxLength={40} placeholder="not known" onChange={e => setLoopDraft(d => ({ ...d, with: e.target.value }))} /></label>
                          <label>Where <input type="text" value={loopDraft.where} maxLength={60} placeholder="not known" onChange={e => setLoopDraft(d => ({ ...d, where: e.target.value }))} /></label>
                          <div className="memory-edit-row">
                            <span className="memory-edit-count" />
                            <button type="button" className="memory-edit-cancel" onClick={() => { setEditingLoopId(null); setLoopError(''); }} disabled={busy}>Cancel</button>
                            <button type="button" className="memory-edit-save" onClick={() => saveLoopChanges(loop)} disabled={busy}>{busy ? 'Saving...' : 'Save'}</button>
                          </div>
                          {loopError && <div className="memory-edit-error" role="alert">{loopError}</div>}
                          <div className="memory-edit-hint">What you write here is marked "you wrote this". Clear a box to go back to not known; Sonic never fills it in with a guess.</div>
                        </div>
                      ) : (
                        <div className="memory-event-fields">
                          {EVENT_FIELDS.map(({ key, label }) => {
                            const field = loop.fields?.[key];
                            return field
                              ? <span key={key} className="memory-event-field">{label}: {field.display} <span className="memory-event-source">({FIELD_SOURCE_LABELS[field.source] ?? field.source})</span></span>
                              : <span key={key} className="memory-event-field memory-event-unknown">{label}: not known</span>;
                          })}
                        </div>
                      )}
                    </div>
                    <div className="memory-row-actions">
                      {!cancelled && !loop.confirmed_at && Object.keys(loop.fields ?? {}).length > 0 && (
                        <button type="button" className="memory-event-action" title="Mark this as checked: it's right" onClick={() => saveLoopChanges(loop, { confirm: true })} disabled={busy || editing}>
                          Looks right
                        </button>
                      )}
                      {!cancelled && (
                        <button type="button" className="memory-edit-btn" title="Edit this" aria-label="Edit this upcoming item" onClick={() => startEditingLoop(loop)} disabled={busy || editing}>
                          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                            <path d="M12 20h9" />
                            <path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z" />
                          </svg>
                        </button>
                      )}
                      <button
                        type="button"
                        className="memory-event-action"
                        title={cancelled ? 'Bring this back' : "It fell through: Sonic won't ask about it"}
                        onClick={() => saveLoopChanges(loop, { status: cancelled ? 'open' : 'cancelled' })}
                        disabled={busy || editing}
                      >
                        {cancelled ? 'Restore' : 'Cancel it'}
                      </button>
                      <button
                        type="button"
                        className="memory-delete-btn"
                        title="Forget this"
                        onClick={() => handleDeleteLoop(loop.id, loop.text)}
                      >
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                          <polyline points="3 6 5 6 21 6" />
                          <path d="M19 6l-2 14H7L5 6" />
                          <line x1="10" y1="11" x2="10" y2="17" />
                          <line x1="14" y1="11" x2="14" y2="17" />
                        </svg>
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          </section>
        )}
        <section className="service-card memory-card">
          <div className="card-badge">{facts.length} SAVED</div>
          <h2>Saved Memories</h2>
          <p className="card-description">
            Search, review, or forget individual memories. Clearing everything is permanent.
            Each one is filed under a part of your life (work, your own projects, personal, or general) so Sonic doesn't mix your job with what you build for yourself. If one is filed wrong, change it on its row and Sonic won't move it again.
          </p>
          {(stored.notes > 0 || stored.archived > 0) && (
            <p className="card-description">
              Sonic also keeps {stored.notes} short note{stored.notes === 1 ? '' : 's'} from your past conversations, which it searches to recall what you have talked about
              {stored.archived > 0 ? `, and ${stored.archived} older cop${stored.archived === 1 ? 'y' : 'ies'} kept when its memory was tidied up` : ''}.
              They are not listed here, but Clear All removes them too.
            </p>
          )}

          <div className="memory-controls-row">
            <input
              type="text"
              placeholder="Search your memories..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="manifest-search-input"
            />
            <select
              value={categoryFilter}
              onChange={(e) => setCategoryFilter(e.target.value)}
              className="memory-category-select"
            >
              <option value="all">All categories</option>
              {categories.map(cat => (
                <option key={cat} value={cat}>{cat}</option>
              ))}
            </select>
            <select
              value={domainFilter}
              onChange={(e) => setDomainFilter(e.target.value)}
              className="memory-category-select"
              aria-label="Filter by part of your life"
            >
              <option value="all">All parts of your life</option>
              {DOMAIN_CHOICES.map(choice => <option key={choice.value} value={choice.value}>{choice.label}</option>)}
              {facts.some(f => !f.domain) && <option value="unsorted">Not sorted yet</option>}
            </select>
            <button
              type="button"
              className="delete-row-btn memory-clear-all-btn"
              onClick={handleClearAll}
              disabled={clearing || (facts.length === 0 && loops.length === 0 && stored.notes === 0 && stored.archived === 0)}
            >
              {clearing ? 'Clearing...' : 'Clear All'}
            </button>
          </div>

          {filteredFacts.length === 0 ? (
            <div className="empty-manifest-notice">
              {facts.length === 0
                ? 'Nothing remembered yet — as you chat, Sonic will start building this up.'
                : 'No memories match your search.'}
            </div>
          ) : (
            <div className="memory-list">
              {filteredFacts.map(fact => (
                <div key={fact.id} className="memory-row">
                  <div className="memory-row-main">
                    <div className="memory-row-badges">
                      <span className={`memory-badge ${CATEGORY_COLORS[fact.category] || 'cat-preference'}`}>
                        {fact.category}
                      </span>
                      <span className="memory-badge memory-source-badge">
                        {SOURCE_LABELS[fact.source] || fact.source}
                      </span>
                      <select
                        className="memory-domain-select"
                        value={fact.domain ?? ''}
                        aria-label="Which part of your life this memory is about"
                        title={fact.domain_locked ? 'You chose this, so Sonic keeps it' : 'Change which part of your life this is about'}
                        disabled={domainSavingId === fact.id}
                        onChange={(e) => handleSetDomain(fact, e.target.value as MemoryDomain)}
                      >
                        {!fact.domain && <option value="" disabled>Not sorted yet</option>}
                        {DOMAIN_CHOICES.map(choice => <option key={choice.value} value={choice.value}>{choice.label}</option>)}
                      </select>
                      {fact.goal_status && fact.goal_status !== 'active' && (
                        <span className="memory-badge memory-status-badge">{fact.goal_status}</span>
                      )}
                    </div>
                    {editingId === fact.id ? (
                      <div className="memory-edit">
                        <textarea
                          className="memory-edit-text"
                          value={draft}
                          rows={3}
                          maxLength={MAX_FACT_CHARS}
                          autoFocus
                          aria-label="Edit this memory"
                          onChange={(e) => setDraft(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === 'Escape') cancelEditing();
                            if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) handleSaveEdit(fact);
                          }}
                        />
                        <div className="memory-edit-row">
                          <select
                            className="memory-category-select"
                            value={draftCategory}
                            aria-label="Category"
                            onChange={(e) => setDraftCategory(e.target.value)}
                          >
                            {(EDITABLE_CATEGORIES.includes(fact.category) ? EDITABLE_CATEGORIES : [fact.category, ...EDITABLE_CATEGORIES]).map(cat => (
                              <option key={cat} value={cat}>{cat}</option>
                            ))}
                          </select>
                          <span className="memory-edit-count">{draft.length}/{MAX_FACT_CHARS}</span>
                          <button type="button" className="memory-edit-cancel" onClick={cancelEditing} disabled={savingId === fact.id}>Cancel</button>
                          <button type="button" className="memory-edit-save" onClick={() => handleSaveEdit(fact)} disabled={savingId === fact.id || !draft.trim()}>
                            {savingId === fact.id ? 'Saving...' : 'Save'}
                          </button>
                        </div>
                        {editError && <div className="memory-edit-error" role="alert">{editError}</div>}
                        <div className="memory-edit-hint">Your wording replaces the old one and is treated as something you told Sonic directly.</div>
                      </div>
                    ) : (
                      <div className="memory-row-text">{fact.fact}</div>
                    )}
                    {editingId !== fact.id && (fact.name_variants ?? []).map(variant => (
                      <div key={variant.names.join('|')} className="memory-name-variant">
                        “{variant.names.join('” and “')}” look like one person written two ways. Sonic writes “{variant.shown_as}”, the spelling you used most recently.
                        <button type="button" className="memory-event-action" onClick={() => handleDifferentPeople(variant.names)}>They're different people</button>
                      </div>
                    ))}
                    {editingId !== fact.id && fact.related_facts && fact.related_facts.length > 0 && (
                      <div className="memory-links">
                        <span className="memory-links-label">Goes with:</span>
                        {fact.related_facts.map(linked => (
                          <span key={linked.id} className="memory-link-chip">
                            {linked.fact}
                            <button
                              type="button"
                              className="memory-link-remove"
                              title="These don't belong together"
                              aria-label={`Unlink from: ${linked.fact}`}
                              onClick={() => handleUnlink(fact.id, linked.id)}
                            >
                              ×
                            </button>
                          </span>
                        ))}
                      </div>
                    )}
                    <div className="memory-row-meta">
                      <span className={`memory-confidence ${confidenceTone(fact.effective_confidence)}`}>
                        <span
                          className="memory-confidence-bar"
                          style={{ width: `${Math.round(fact.effective_confidence * 100)}%` }}
                        />
                        <span className="memory-confidence-label">
                          {Math.round(fact.effective_confidence * 100)}% confident
                        </span>
                      </span>
                      <span className="memory-row-date">Updated {formatDate(fact.updated_at)}</span>
                    </div>
                  </div>
                  <div className="memory-row-actions">
                    <button
                      type="button"
                      className="memory-edit-btn"
                      title="Edit this memory"
                      aria-label="Edit this memory"
                      onClick={() => startEditing(fact)}
                      disabled={editingId === fact.id || savingId !== null}
                    >
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                        <path d="M12 20h9" />
                        <path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z" />
                      </svg>
                    </button>
                    <button
                      type="button"
                      className="memory-delete-btn"
                      title="Forget this memory"
                      onClick={() => handleDeleteFact(fact.id, fact.fact)}
                      disabled={deletingId !== null}
                    >
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                        <polyline points="3 6 5 6 21 6" />
                        <path d="M19 6l-2 14H7L5 6" />
                        <line x1="10" y1="11" x2="10" y2="17" />
                        <line x1="14" y1="11" x2="14" y2="17" />
                      </svg>
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>
      </div>
    </div>
  );
};

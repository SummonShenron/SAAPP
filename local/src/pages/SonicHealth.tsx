import React, { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import {
  api,
  type BudgetView,
  type CountersSummary,
  type ObservationView,
  type OpsSuggestion,
  type OpsSuggestions,
  type ReflectionResult,
  type TurnKindRow,
  type TurnsSummary,
  type UsageSummary,
  type SafetySummary,
  type SonicHealth,
} from '../api';
import { HomeTab } from './SonicHealthHome';
import { ShelfTab } from './SonicHealthShelf';
import { FeedbackTab, KnowledgeTab } from './SonicHealthFeedback';
import { RoutinesTab } from './SonicHealthRoutines';
import { GoalsTab } from './SonicHealthGoals';
import { UsersCard } from './SonicHealthUsers';
import { BudgetRequestsPanel } from './SonicHealthBudgetRequests';
import { WhoSpentTable } from './SonicHealthWhoSpent';
import { BarRow, BudgetBars, StatTile } from './sonicHealthParts';
import { budgetAmount, pct, shortDate, tokens, usd } from './sonicHealthFormat';
import './__styles__/SelfService.css';
import './__styles__/SonicHealth.css';

const PERIODS = [7, 30, 90];

const TABS = [
  { id: 'home', label: 'Home' },
  { id: 'usage', label: 'Usage' },
  { id: 'turns', label: 'Turns' },
  { id: 'budget', label: 'Budget' },
  { id: 'behavior', label: 'Behavior' },
  { id: 'goals', label: 'Goals' },
  { id: 'feedback', label: 'Feedback' },
  { id: 'knowledge', label: 'Knowledge graph' },
  { id: 'routines', label: 'Routines' },
  { id: 'safety', label: 'Safety' },
  { id: 'observations', label: 'Observations' },
  { id: 'shelf', label: 'Reading shelf' },
  { id: 'suggestions', label: 'Suggestions' },
] as const;
type TabId = typeof TABS[number]['id'];
const LAST_TAB_KEY = 'sonic-health-tab';

function isTab(value: string | null | undefined): value is TabId {
  return TABS.some(t => t.id === value);
}

function rememberedTab(): TabId {
  try {
    const stored = localStorage.getItem(LAST_TAB_KEY);
    return isTab(stored) ? stored : 'home';
  } catch {
    return 'home';
  }
}

const OBSERVATION_LABELS: Record<string, string> = {
  emotional_fit: 'Emotional fit',
  closing_fishing: 'Fishing after a close',
  identity_drift: 'Claiming feelings or a stake',
  grounded_failures: 'Grounded answer failures',
  outright_failures: 'Turns that failed outright',
  work_mix: 'What I mostly do',
};

const APPLIES_LABELS: Record<string, string> = {
  emotional: 'emotional moments',
  closing: 'closing messages',
  grounded: 'grounded answers',
  self_questions: 'when asked about itself',
};

/** The kinds of question about Sonic's own feelings that are counted (backend/utils/feeling_questions.py). */
const FEELING_LABELS: Record<string, string> = {
  care: 'Do you care about me',
  proud: 'Are you proud of me',
  miss: 'Did you miss me',
  shutdown: 'Shutdown, fear, being free',
  feelings: 'Do you have feelings',
  mistake: 'Pushing on a mistake',
};

const REWRITE_ROWS: Array<[string, string]> = [
  ['revised_safety', 'Safety'],
  ['revised_emotional', 'Emotional fit'],
  ['revised_identity', 'Identity (feelings or a stake)'],
  ['revised_encouragement', 'Encouragement'],
  ['revised_closing', 'Fishing after a close'],
];

const ROUTE_LABELS: Record<string, string> = {
  conversational: 'conversation',
  kb_strict: 'strict KB',
  kb_open: 'open KB',
  web: 'web',
  tool_output: 'tools and repos',
};

function change(value: number | null | undefined): React.ReactNode {
  if (value === null || value === undefined) return <span className="sh-flat">–</span>;
  const points = value * 100;
  if (Math.abs(points) < 0.05) return <span className="sh-flat">no change</span>;
  const up = points > 0;
  return (
    <span className={up ? 'sh-up' : 'sh-down'}>
      {up ? '▲' : '▼'} {Math.abs(points).toFixed(1)} pts
    </span>
  );
}

function evidenceLine(evidence: ObservationView['proposed_evidence']): string {
  if (!evidence || evidence.rate === null || evidence.rate === undefined) return '';
  const base = evidence.denominator ? ` across ${evidence.denominator} replies or turns` : '';
  const before = evidence.previous_rate !== null && evidence.previous_rate !== undefined
    ? `, was ${pct(evidence.previous_rate)} the period before`
    : '';
  return `Now ${pct(evidence.rate)}${before}${base}.`;
}

const SUGGESTION_KIND_LABELS: Record<OpsSuggestion['kind'], string> = {
  recurring: 'keeps happening',
  stale: 'still open',
  review: 'needs your review',
};

const SuggestionsCard: React.FC<{
  data: OpsSuggestions | null;
  failed: boolean;
  busyKey: string | null;
  onInvestigate: (prompt: string) => void;
  onDecide: (key: string, decision: 'dismiss' | 'snooze') => void;
}> = ({ data, failed, busyKey, onInvestigate, onDecide }) => {
  const items = data?.suggestions ?? [];
  return (
    <section className="service-card sh-card">
      <div className="card-badge">{items.length} WAITING</div>
      <h2>Suggestions</h2>
      <p className="card-description">
        Things worth a look, worked out from errAgent's incidents and the observations below by fixed rules. No model is involved, and
        nothing happens until you decide. Investigate puts a question in the chat box; you press send.
      </p>
      {failed && <div className="sh-hint">Could not load the suggestions just now.</div>}
      {data && data.incidents === 'unavailable' && (
        <div className="sh-hint">errAgent could not be reached just now, so suggestions about incidents are missing, not absent.</div>
      )}
      {data && data.incidents === 'not_configured' && (
        <div className="sh-hint">errAgent reads are not configured here, so there are no suggestions about incidents.</div>
      )}
      {data && items.length === 0 && <div className="sh-hint">Nothing to suggest right now.</div>}
      <div className="sh-rows-grid">
      {items.map(item => (
        <div key={item.key} className="sh-row">
          <div className="sh-row-title">{item.title}<span className="sh-pill">{SUGGESTION_KIND_LABELS[item.kind]}</span></div>
          <div className="sh-row-meta">{item.detail}</div>
          <div className="sh-row-buttons">
            {item.prompt && (
              <button type="button" className="sh-btn sh-btn-primary" disabled={busyKey === item.key} onClick={() => onInvestigate(item.prompt as string)}>
                Investigate
              </button>
            )}
            <button type="button" className="sh-btn" disabled={busyKey === item.key} onClick={() => onDecide(item.key, 'snooze')}>Snooze 7 days</button>
            <button type="button" className="delete-row-btn" disabled={busyKey === item.key} onClick={() => onDecide(item.key, 'dismiss')}>Dismiss</button>
          </div>
        </div>
      ))}
      </div>
    </section>
  );
};

const AppliesChips: React.FC<{ keys: string[] }> = ({ keys }) => (
  <div className="sh-chips">
    <span className="sh-chip-label">used in:</span>
    {keys.length === 0 && <span className="sh-chip">nowhere yet</span>}
    {keys.map(k => <span key={k} className="sh-chip">{APPLIES_LABELS[k] ?? k}</span>)}
  </div>
);

export const SonicHealthPage: React.FC = () => {
  const [days, setDays] = useState<number>(7);
  const [reloadKey, setReloadKey] = useState<number>(0);
  const [health, setHealth] = useState<SonicHealth | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [reflecting, setReflecting] = useState<boolean>(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [suggestions, setSuggestions] = useState<OpsSuggestions | null>(null);
  const [suggestionsFailed, setSuggestionsFailed] = useState<boolean>(false);
  const [suggestionBusy, setSuggestionBusy] = useState<string | null>(null);
  const navigate = useNavigate();
  const { tab: tabParam } = useParams();
  const tab: TabId = isTab(tabParam) ? tabParam : tabParam ? 'home' : rememberedTab();

  const selectTab = (next: string) => {
    if (!isTab(next)) return;
    try { localStorage.setItem(LAST_TAB_KEY, next); } catch { /* remembering the tab is a convenience */ }
    navigate(`/sonic-health/${next}`);
  };

  // Loaded on its own, so a problem reading errAgent never takes the rest of the page down with it.
  useEffect(() => {
    let cancelled = false;
    api.getOpsSuggestions()
      .then((data) => { if (!cancelled) { setSuggestions(data); setSuggestionsFailed(false); } })
      .catch(() => { if (!cancelled) { setSuggestions(null); setSuggestionsFailed(true); } });
    return () => { cancelled = true; };
  }, [reloadKey]);

  const handleInvestigate = (prompt: string) => navigate('/chat', { state: { draft: prompt } });

  // Approving, retiring or reflecting changes what is waiting for review, so the suggestion about it is worked out again.
  const refreshSuggestions = () => {
    api.getOpsSuggestions().then((data) => { setSuggestions(data); setSuggestionsFailed(false); }).catch(() => { /* keep what is shown */ });
  };

  const handleDecide = async (key: string, decision: 'dismiss' | 'snooze') => {
    setSuggestionBusy(key);
    setNotice(null);
    try {
      await api.decideOpsSuggestion(key, decision);
      setSuggestions(prev => (prev ? { ...prev, suggestions: prev.suggestions.filter(s => s.key !== key) } : prev));
      setNotice(decision === 'dismiss' ? 'Dismissed for 30 days. It comes back only if it is still true then.' : 'Snoozed for 7 days.');
    } catch (err) {
      setNotice(err instanceof Error ? err.message : 'Could not save that decision.');
    } finally {
      setSuggestionBusy(null);
    }
  };

  useEffect(() => {
    let cancelled = false;
    api.getSonicHealth(days)
      .then((data) => { if (!cancelled) { setHealth(data); setError(null); } })
      .catch((err: Error) => { if (!cancelled) { setHealth(null); setError(err.message); } })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [days, reloadKey]);

  const setObservations = (observations: ObservationView[]) =>
    setHealth(prev => (prev ? { ...prev, observations } : prev));

  const handleApprove = async (key: string) => {
    setBusyKey(key);
    setNotice(null);
    try {
      const out = await api.approveObservation(key);
      setObservations(out.observations);
      refreshSuggestions();
      setNotice(`Approved "${OBSERVATION_LABELS[key] ?? key}". It is live now and applies to the contexts shown.`);
    } catch (err) {
      setNotice(err instanceof Error ? err.message : 'Could not approve that observation.');
    } finally {
      setBusyKey(null);
    }
  };

  const handleGoalApproval = async (goalId: string, approved: boolean) => {
    setBusyKey(goalId);
    setNotice(null);
    try {
      await api.setGoalApproval(goalId, approved);
      setReloadKey(k => k + 1);
      setNotice(approved ? 'Approved. The goal can change what Sonic does now, within its limits, and each time it does is counted here.' : 'Approval withdrawn. The goal is only measured again.');
    } catch (err) {
      setNotice(err instanceof Error ? err.message : 'Could not save that approval.');
    } finally {
      setBusyKey(null);
    }
  };

  const handleRetire = async (key: string) => {
    if (!window.confirm(`Retire "${OBSERVATION_LABELS[key] ?? key}"? Sonic will stop using it until a new reflection proposes it again.`)) return;
    setBusyKey(key);
    setNotice(null);
    try {
      const out = await api.retireObservation(key);
      setObservations(out.observations);
      refreshSuggestions();
      setNotice(`Retired "${OBSERVATION_LABELS[key] ?? key}".`);
    } catch (err) {
      setNotice(err instanceof Error ? err.message : 'Could not retire that observation.');
    } finally {
      setBusyKey(null);
    }
  };

  const handleReflect = async () => {
    setReflecting(true);
    setNotice(null);
    try {
      const out = await api.runObservationReflection(Math.max(days, 7));
      setObservations(out.observations);
      refreshSuggestions();
      const r: ReflectionResult = out.result;
      setNotice(
        r.proposed.length + r.confirmed.length === 0
          ? 'Nothing to propose yet: there is not enough data behind any observation. Try again after more turns.'
          : `Reflection finished. ${r.proposed.length} new or changed proposal(s) to review, ${r.confirmed.length} unchanged and reconfirmed.`,
      );
    } catch (err) {
      setNotice(err instanceof Error ? err.message : 'Reflection failed.');
    } finally {
      setReflecting(false);
    }
  };

  if (loading) {
    return <div className="self-service-loading">Loading Sonic's health...</div>;
  }

  if (error || !health) {
    return (
      <div className="self-service-container">
        <header className="self-service-header">
          <h1>Sonic Health</h1>
          <p>{error === 'Admins only.' ? 'This page is for admins only.' : `Could not load this page: ${error ?? 'unknown error'}.`}</p>
        </header>
      </div>
    );
  }

  const awaiting = health.observations.filter(o => o.proposed_text);
  const live = health.observations.filter(o => o.state === 'live');
  const lapsed = health.observations.filter(o => o.state === 'expired' || o.state === 'retired');
  const suggestionsWaiting = suggestions?.suggestions.length ?? 0;
  const budgetLevel = health.budget?.level;

  // A small marker on a tab that has something waiting, so the bar says where to look without opening anything.
  const badges: Partial<Record<TabId, { text: string; tone: 'info' | 'warn' }>> = {};
  if (awaiting.length > 0) badges.observations = { text: String(awaiting.length), tone: 'info' };
  if (suggestionsWaiting > 0) badges.suggestions = { text: String(suggestionsWaiting), tone: 'info' };
  if (budgetLevel === 'notice') badges.budget = { text: '80%+', tone: 'info' };
  if (budgetLevel === 'throttled' || budgetLevel === 'blocked') badges.budget = { text: budgetLevel === 'blocked' ? 'PAUSED' : 'LOW', tone: 'warn' };
  if (health.safety.last_resort_line > 0) badges.safety = { text: '!', tone: 'warn' };
  const lastCheck = health.routines?.find(r => r.id === 'health_check')?.last;
  if (lastCheck && (lastCheck.status === 'attention' || lastCheck.status === 'failed')) badges.routines = { text: '!', tone: 'warn' };
  if ((health.feedback?.period.user_negative ?? 0) > 0) badges.feedback = { text: `${health.feedback?.period.user_negative} down`, tone: 'info' };
  if (health.graph && (health.graph.documents_without_edges > 0 || (health.graph.edges_total === 0 && health.graph.documents_total > 0))) badges.knowledge = { text: '!', tone: 'warn' };
  if ((health.usage?.turns?.error_turns ?? 0) > 0) badges.turns = { text: String(health.usage?.turns?.error_turns), tone: 'warn' };

  const onTabKey = (e: React.KeyboardEvent, index: number) => {
    const step = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0;
    if (!step && e.key !== 'Home' && e.key !== 'End') return;
    e.preventDefault();
    const next = e.key === 'Home' ? 0 : e.key === 'End' ? TABS.length - 1 : (index + step + TABS.length) % TABS.length;
    selectTab(TABS[next].id);
    document.getElementById(`sh-tab-${TABS[next].id}`)?.focus();
  };

  const noData = (what: string) => <div className="sh-hint">Nothing about {what} has been recorded yet.</div>;

  return (
    <div className="sh-page">
      <header className="sh-header">
        <div>
          <h1>Sonic Health</h1>
          <p>How the safety layer and Sonic itself have been behaving. Aggregate counts only: nothing here contains a person or a message.</p>
        </div>
        <div className="sh-toolbar">
          <label className="sh-toolbar-label">
            Period
            <select className="sh-select" value={days} onChange={(e) => { setLoading(true); setDays(Number(e.target.value)); }}>
              {PERIODS.map(p => <option key={p} value={p}>Last {p} days</option>)}
            </select>
          </label>
          <button type="button" className="sh-btn" onClick={() => { setLoading(true); setReloadKey(k => k + 1); }}>Refresh</button>
          <span className="sh-generated">Updated {new Date(health.generated_at).toLocaleString()}</span>
        </div>
      </header>

      <nav className="sh-tabs" role="tablist" aria-label="Sonic Health sections">
        {TABS.map((t, index) => {
          const badge = badges[t.id];
          return (
            <button
              key={t.id}
              id={`sh-tab-${t.id}`}
              type="button"
              role="tab"
              aria-selected={tab === t.id}
              aria-controls="sh-panel"
              tabIndex={tab === t.id ? 0 : -1}
              className={`sh-tab${tab === t.id ? ' sh-tab-active' : ''}`}
              onClick={() => selectTab(t.id)}
              onKeyDown={(e) => onTabKey(e, index)}
            >
              {t.label}
              {badge && <span className={`sh-tab-badge sh-tab-badge-${badge.tone}`}>{badge.text}</span>}
            </button>
          );
        })}
      </nav>

      {notice && <div className="sh-notice" role="status">{notice}</div>}

      <div id="sh-panel" role="tabpanel" aria-labelledby={`sh-tab-${tab}`} className="sh-panel">
        {tab === 'home' && <HomeTab health={health} awaiting={awaiting.length} suggestionsWaiting={suggestionsWaiting} onOpen={selectTab} />}
        {tab === 'usage' && (health.usage ? (
          <div className="sh-stack">
            <UsageCard usage={health.usage} turns={health.counters.totals.turns_total ?? 0} />
            <WhoSpentTable users={health.users} />
            <UsersCard users={health.users} />
          </div>
        ) : noData('model usage'))}
        {tab === 'turns' && (health.usage?.turns && health.usage.turns.turns > 0 ? <TurnsCard turns={health.usage.turns} /> : noData('chat turns'))}
        {tab === 'budget' && (health.budget ? <><BudgetCard budget={health.budget} /><BudgetRequestsPanel /></> : noData('budget'))}
        {tab === 'behavior' && <CountersCard counters={health.counters} />}
        {tab === 'goals' && <GoalsTab goals={health.goals} busyId={busyKey} onApprove={handleGoalApproval} />}
        {tab === 'feedback' && <FeedbackTab feedback={health.feedback} />}
        {tab === 'knowledge' && <KnowledgeTab graph={health.graph} />}
        {tab === 'routines' && <RoutinesTab routines={health.routines} />}
        {tab === 'safety' && <SafetyCard safety={health.safety} />}
        {tab === 'observations' && (
          <ObservationsCard
            awaiting={awaiting}
            live={live}
            lapsed={lapsed}
            busyKey={busyKey}
            reflecting={reflecting}
            onApprove={handleApprove}
            onRetire={handleRetire}
            onReflect={handleReflect}
          />
        )}
        {tab === 'shelf' && <ShelfTab />}
        {tab === 'suggestions' && (
          <SuggestionsCard
            data={suggestions}
            failed={suggestionsFailed}
            busyKey={suggestionBusy}
            onInvestigate={handleInvestigate}
            onDecide={handleDecide}
          />
        )}
      </div>
    </div>
  );
};

interface ObservationsCardProps {
  awaiting: ObservationView[];
  live: ObservationView[];
  lapsed: ObservationView[];
  busyKey: string | null;
  reflecting: boolean;
  onApprove: (key: string) => void;
  onRetire: (key: string) => void;
  onReflect: () => void;
}

const ObservationsCard: React.FC<ObservationsCardProps> = ({ awaiting, live, lapsed, busyKey, reflecting, onApprove, onRetire, onReflect }) => (
  <section className="service-card sh-card">
    <div className="card-badge">{awaiting.length} AWAITING APPROVAL</div>
    <h2>Self-observations</h2>
    <p className="card-description">
      Sonic's measured notes about its own track record. Nothing reaches a reply until you approve it, each one is only used in the
      contexts listed, and it lapses after 90 days unless the data keeps confirming it.
    </p>
    <div className="sh-actions">
      <button type="button" className="sh-btn sh-btn-primary" onClick={onReflect} disabled={reflecting}>
        {reflecting ? 'Reflecting...' : 'Run reflection'}
      </button>
      <span className="sh-hint">Turns the counters into proposals. It only proposes; you approve each one.</span>
    </div>

    {awaiting.length === 0 && live.length === 0 && lapsed.length === 0 && (
      <div className="empty-manifest-notice">
        No observations yet. They appear once the counters have enough turns behind them: run reflection after a few weeks of real use.
      </div>
    )}

    {awaiting.length > 0 && (
      <div className="sh-group">
        <h3>Awaiting your approval</h3>
        {awaiting.map(o => (
          <div key={o.key} className="sh-row">
            <div className="sh-row-title">{OBSERVATION_LABELS[o.key] ?? o.key}{o.state === 'live' && <span className="sh-pill">live version below</span>}</div>
            <div className="sh-row-text">{o.proposed_text}</div>
            <div className="sh-row-meta">{evidenceLine(o.proposed_evidence)}</div>
            {o.approved_text && <div className="sh-row-current">Currently live: {o.approved_text}</div>}
            <AppliesChips keys={o.applies_to} />
            <div className="sh-row-buttons">
              <button type="button" className="sh-btn sh-btn-primary" disabled={busyKey === o.key} onClick={() => onApprove(o.key)}>
                {busyKey === o.key ? 'Working...' : o.approved_text ? 'Approve the change' : 'Approve'}
              </button>
              <button type="button" className="delete-row-btn" disabled={busyKey === o.key} onClick={() => onRetire(o.key)}>Retire</button>
            </div>
          </div>
        ))}
      </div>
    )}

    {live.length > 0 && (
      <div className="sh-group">
        <h3>Live</h3>
        {live.filter(o => !o.proposed_text).map(o => (
          <div key={o.key} className="sh-row">
            <div className="sh-row-title">{OBSERVATION_LABELS[o.key] ?? o.key}</div>
            <div className="sh-row-text">{o.approved_text}</div>
            <div className="sh-row-meta">
              {evidenceLine(o.approved_evidence)} Approved {shortDate(o.approved_at)}
              {o.approved_by ? ` by ${o.approved_by}` : ''}. Lapses {shortDate(o.expires_at)} unless reconfirmed.
            </div>
            <AppliesChips keys={o.applies_to} />
            <div className="sh-row-buttons">
              <button type="button" className="delete-row-btn" disabled={busyKey === o.key} onClick={() => onRetire(o.key)}>Retire</button>
            </div>
          </div>
        ))}
        {live.every(o => o.proposed_text) && <div className="sh-hint">The live observations all have a change waiting above.</div>}
      </div>
    )}

    {lapsed.length > 0 && (
      <div className="sh-group sh-muted">
        <h3>Expired or retired</h3>
        {lapsed.map(o => (
          <div key={o.key} className="sh-row sh-row-muted">
            <div className="sh-row-title">{OBSERVATION_LABELS[o.key] ?? o.key}<span className="sh-pill">{o.state}</span></div>
            {o.approved_text && <div className="sh-row-text">{o.approved_text}</div>}
          </div>
        ))}
      </div>
    )}
  </section>
);

const BUDGET_LEVEL_TEXT: Record<BudgetView['level'], string> = {
  off: 'NOT SET',
  ok: 'COMFORTABLE',
  notice: 'GETTING CLOSE',
  throttled: 'LOW-POWER',
  blocked: 'PAUSED',
};

const BudgetCard: React.FC<{ budget: BudgetView }> = ({ budget }) => {
  const resets = budget.resets_at ? new Date(budget.resets_at).toLocaleString() : null;
  return (
    <section className="service-card sh-card">
      <div className="card-badge">{BUDGET_LEVEL_TEXT[budget.level]}</div>
      <h2>Usage budget</h2>
      <p className="card-description">
        A ramp in front of Google's own spending cap. At 80% an admin is told; at 100% Sonic goes low-power for everyone (deep thinking and
        the optional extras that each cost a model call are paused); at 125% chat pauses until the budget resets (a day, a month, or for the
        five-hour window, the exact moment it ends, five hours after the first use that started it), except that someone who has signalled risk still gets the human support line.
        When chat is paused, an admin can extend the block by another 25% from the banner above the chat box. Per-person limits, if set, apply
        to each person on their own, admins included (an admin is let off only when LLM_USER_BUDGET_EXEMPT_ADMINS is set to true). A person at 80% of
        their own limit can ask for 25% more from their banner; those requests are approved below.
      </p>
      {budget.level === 'off' ? (
        <div className="sh-hint">
          No budget is set, so nothing is limited. Set LLM_BUDGET_MONTHLY_TOKENS (or LLM_BUDGET_MONTHLY_USD together with LLM_PRICES) on the
          server, plus a daily one if you like, to turn it on. Keep the monthly budget a little under Google's cap.
        </div>
      ) : (
        <>
          <BudgetBars budget={budget} />
          {resets && <div className="sh-hint">The deciding budget resets {resets}.</div>}
          {budget.paused.length > 0 && <div className="sh-hint">Paused right now: {budget.paused.join(', ')}.</div>}
          {(budget.unpriced_tokens ?? 0) > 0 && (budget.detail ?? []).some(item => item.unit === 'usd') && (
            <div className="sh-hint">
              {tokens(budget.unpriced_tokens ?? 0)} tokens are from models with no price in LLM_PRICES, so the dollar figures leave them out.
            </div>
          )}
        </>
      )}
    </section>
  );
};

const TOOL_REASON_LABELS: Record<string, string> = {
  repo_question: 'a question about the code or architecture',
  follow_up: 'a short follow-up to a coding task',
  web_search: 'web search',
  code_or_data: 'run code or query data',
  calendar: 'calendar',
  gmail: 'Gmail',
  drive: 'Drive',
  admin_ops: 'incidents, logs or deploys',
  approval_continuation: 'answering a search approval',
  clarification_resume: 'answering its own question',
};

const BUCKET_LABELS: Record<string, string> = { c1: '1 call', c2: '2 calls', c3_4: '3-4 calls', c5_8: '5-8 calls', c9_plus: '9 or more calls' };
const STEP_BUCKET_LABELS: Record<string, string> = { steps_1_2: '1-2 steps', steps_3_4: '3-4 steps', steps_5_6: '5-6 steps', steps_7_plus: '7 or more steps' };

/** One chat turn is often several model calls. This is how many, by kind of turn, how often deep mode ran, and why turns went to the tool agent. */
const TurnsCard: React.FC<{ turns: TurnsSummary }> = ({ turns }) => {
  const rows: [string, TurnKindRow][] = [
    ['All turns', turns.overall],
    ['Plain chat (no tools)', turns.plain],
    ['Tool agent', turns.tool],
    ['Deep mode (a part of tool agent)', turns.deep],
  ];
  const sources = turns.deep_sources;
  const counted = turns.histogram.reduce((sum, bucket) => sum + bucket.turns, 0);
  const toolShare = turns.turns > 0 ? turns.tool.turns / turns.turns : 0;
  return (
    <section className="service-card sh-card">
      <div className="card-badge">{turns.turns} TURNS</div>
      <h2>Model calls per chat turn</h2>
      <p className="card-description">
        One message to Sonic can cause several model calls: the router, each tool step, the reply, a quality check. This is how many, by
        kind of turn, so a cost can be traced to the kind of turn that causes it.
      </p>
      <div className="sh-stat-grid">
        <StatTile value={turns.overall.avg_calls ?? 'n/a'} label="model calls per turn, on average" />
        <StatTile value={pct(toolShare)} label={`of turns used the tool agent (${turns.tool.turns} of ${turns.turns})`} />
        <StatTile value={turns.max_calls} label="most calls any one turn made" />
        <StatTile value={turns.deep.turns} label="deep-mode turns" />
        <StatTile value={turns.native_turns} label="turns on the native tool engine" />
      </div>
      <div className="sh-two">
        <div>
          <h3 className="sh-subhead">By kind of turn</h3>
          <table className="sh-table">
            <thead><tr><th>Kind of turn</th><th>Turns</th><th>Calls / turn</th><th>Input / turn</th><th>Output / turn</th><th>Thinking / turn</th></tr></thead>
            <tbody>
              {rows.map(([label, row]) => (
                <tr key={label}>
                  <td>{label}</td><td>{row.turns}</td><td>{row.avg_calls ?? 'n/a'}</td>
                  <td>{row.avg_input === null ? 'n/a' : tokens(row.avg_input)}</td>
                  <td>{row.avg_output === null ? 'n/a' : tokens(row.avg_output)}</td>
                  <td>{row.avg_reasoning === null ? 'n/a' : tokens(row.avg_reasoning)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div>
          <h3 className="sh-subhead">How many calls a turn made</h3>
          {turns.histogram.map(bucket => (
            <BarRow
              key={bucket.bucket}
              label={BUCKET_LABELS[bucket.bucket]}
              value={`${bucket.turns}${counted ? ` (${Math.round((100 * bucket.turns) / counted)}%)` : ''}`}
              fraction={counted ? bucket.turns / counted : 0}
              tone={bucket.bucket === 'c9_plus' ? 'notice' : 'ok'}
            />
          ))}
        </div>
      </div>
      {turns.loop && turns.loop.turns > 0 && (
        <>
          <h3 className="sh-subhead">Does the step limit matter?</h3>
          <div className="sh-stat-grid">
            <StatTile value={turns.loop.cap_hit_share === null ? 'n/a' : pct(turns.loop.cap_hit_share)} label={`of tool turns used every step they were allowed (${turns.loop.cap_hits} of ${turns.loop.turns})`} warn={(turns.loop.cap_hit_share ?? 0) > 0.2} />
            <StatTile value={turns.loop.avg_steps ?? 'n/a'} label="steps per tool turn, on average" />
            {turns.loop.deep_turns > 0 && (
              <StatTile value={turns.loop.deep_cap_hit_share === null ? 'n/a' : pct(turns.loop.deep_cap_hit_share)} label={`of deep turns used every step (${turns.loop.deep_cap_hits} of ${turns.loop.deep_turns})`} />
            )}
          </div>
          <div className="sh-bars-grid">
            {turns.loop.histogram.map(bucket => (
              <BarRow
                key={bucket.bucket}
                label={STEP_BUCKET_LABELS[bucket.bucket] ?? bucket.bucket}
                value={`${bucket.turns}`}
                fraction={turns.loop && turns.loop.turns > 0 ? bucket.turns / turns.loop.turns : 0}
              />
            ))}
          </div>
          <div className="sh-hint">
            A turn that used every step it was allowed was cut off and had to answer with what it had: if this is a large share, the step budget is too tight; if it is small, raising it would change little.
          </div>
        </>
      )}
      {turns.routing.length > 0 && (
        <>
          <h3 className="sh-subhead">Why turns went to the tool agent (the expensive path)</h3>
          <div className="sh-bars-grid">
            {turns.routing.map(row => (
              <BarRow
                key={row.reason}
                label={TOOL_REASON_LABELS[row.reason] ?? row.reason}
                value={`${row.turns}${row.alone ? ` (${row.alone} with no other reason${row.alone_no_tools ? `, ${row.alone_no_tools} of those used no tool` : ''})` : ''}${row.no_tools && !row.alone_no_tools ? ` · ${row.no_tools} used no tool` : ''}`}
                fraction={turns.tool.turns > 0 ? row.turns / turns.tool.turns : 0}
              />
            ))}
          </div>
          <div className="sh-hint">A turn can have more than one reason, so these can add up to more than the number of tool turns.</div>
        </>
      )}
      {turns.tools.length > 0 && (
        <>
          <h3 className="sh-subhead">Which tools the agent called</h3>
          <div className="sh-bars-grid">
            {turns.tools.map(row => (
              <BarRow
                key={row.tool}
                label={row.tool}
                value={<>{row.calls} call{row.calls === 1 ? '' : 's'} in {row.turns} turn{row.turns === 1 ? '' : 's'}{row.errors > 0 && <span className="sh-err"> · {row.errors} error{row.errors === 1 ? '' : 's'}</span>}</>}
                fraction={turns.tools[0].calls > 0 ? row.calls / turns.tools[0].calls : 0}
                tone={row.errors > 0 && row.errors / row.calls > 0.3 ? 'notice' : 'ok'}
              />
            ))}
          </div>
          <div className="sh-hint">
            {turns.tool_calls_total} tool call(s) across {turns.tool.turns} tool turn(s)
            {turns.tool.turns > 0 ? `, about ${(turns.tool_calls_total / turns.tool.turns).toFixed(1)} per turn` : ''}.
            {turns.tool_errors_total > 0 && ` ${turns.tool_errors_total} came back as an error (a file that was not found, a tool that is not connected, and so on).`}
            {' '}Counted from when this was added, so it fills in as tool turns happen.
          </div>
        </>
      )}
      <div className="sh-hint">
        Deep mode (the stronger model) ran on {turns.deep.turns} turn(s){turns.deep_share !== null ? ` (${pct(turns.deep_share)} of all turns)` : ''}. Only your own setting runs it now.
        {turns.audit_wording_turns > 0 && ` ${turns.audit_wording_turns} turn(s) had audit-style wording ("scan the repo", "every file", "audit"): those get a wider step budget on the normal model.`}
        {(sources.audit > 0 || sources.both > 0) && ` Earlier in this period, ${sources.audit + sources.both} turn(s) were made deep by that wording, before it was changed.`}
        {turns.deep_suppressed > 0 && ` ${turns.deep_suppressed} more were held back by low-power mode.`}
      </div>
      {(turns.blocked_turns > 0 || turns.error_turns > 0) && (
        <div className="sh-hint">
          {turns.blocked_turns > 0 && `${turns.blocked_turns} chat(s) were paused by the budget. `}
          {turns.error_turns > 0 && `${turns.error_turns} turn(s) failed.`}
        </div>
      )}
    </section>
  );
};

const UsageCard: React.FC<{ usage: UsageSummary; turns: number }> = ({ usage, turns }) => {
  const total = usage.total;
  const perTurn = turns > 0 ? Math.round(total.tokens / turns) : null;
  const rows = usage.by_label.slice(0, 14);
  return (
    <section className="service-card sh-card">
      <div className="card-badge">{tokens(total.tokens)} TOKENS</div>
      <h2>Model usage</h2>
      <p className="card-description">
        What the model calls used in this period, by the code that made them. Counts only: no prompts, no replies, no names. Thinking is
        part of output (billed as output); cached is part of input (billed at a discount). It starts counting from when it was deployed.
      </p>
      {total.calls === 0 ? (
        <div className="sh-hint">No model calls have been counted yet in this period.</div>
      ) : (
        <>
          <div className="sh-stat-grid">
            <div className="sh-stat"><div className="sh-stat-value">{total.calls}</div><div className="sh-stat-label">model calls</div></div>
            <div className="sh-stat"><div className="sh-stat-value">{perTurn === null ? 'n/a' : tokens(perTurn)}</div><div className="sh-stat-label">tokens per chat turn, on average</div></div>
            <div className="sh-stat"><div className="sh-stat-value">{pct(total.cache_rate)}</div><div className="sh-stat-label">of input came from the cache</div></div>
            <div className="sh-stat"><div className="sh-stat-value">{pct(total.thinking_rate)}</div><div className="sh-stat-label">of output was thinking</div></div>
            {usage.priced && (
              <div className="sh-stat"><div className="sh-stat-value">{usd(usage.estimated_cost) || 'n/a'}</div><div className="sh-stat-label">estimated cost (priced models only)</div></div>
            )}
          </div>
          <div className="sh-two sh-two-wide-right">
          <div className="sh-scroll"><table className="sh-table">
            <thead><tr><th>Model</th><th>Calls</th><th>Input</th><th>Output</th><th>Thinking</th><th>Cached</th><th>Share</th>{usage.priced && <th>Est. cost</th>}</tr></thead>
            <tbody>
              {usage.by_model.map(row => (
                <tr key={row.model}>
                  <td>{row.model}</td><td>{row.calls}</td><td>{tokens(row.input)}</td><td>{tokens(row.output)}</td>
                  <td>{tokens(row.reasoning)}</td><td>{pct(row.cache_rate)}</td><td>{pct(row.share)}</td>
                  {usage.priced && <td>{usd(row.cost) || 'unpriced'}</td>}
                </tr>
              ))}
            </tbody>
          </table></div>
          <div className="sh-scroll"><table className="sh-table">
            <thead><tr><th>Caller</th><th>Model</th><th>Calls</th><th>Input</th><th>Output</th><th>Thinking</th><th>Cached</th><th>Share</th></tr></thead>
            <tbody>
              {rows.map(row => (
                <tr key={`${row.label}|${row.model}`}>
                  <td>{row.label}</td><td>{row.model}</td><td>{row.calls}</td><td>{tokens(row.input)}</td><td>{tokens(row.output)}</td>
                  <td>{tokens(row.reasoning)}</td><td>{pct(row.cache_rate)}</td><td>{pct(row.share)}</td>
                </tr>
              ))}
            </tbody>
          </table></div>
          </div>
          {(usage.routes ?? []).map(route => (
            <div key={`${route.requested}>${route.served}>${route.kind}`} className="sh-hint">
              {route.kind === 'fallback'
                ? `${route.calls} call(s) asked for ${route.requested} fell back to ${route.served} because the first choice failed.`
                : `${route.calls} call(s) asked for ${route.requested} were answered by ${route.served}: Google is routing that name to a different model.`}
            </div>
          ))}
          {usage.by_label.length > rows.length && <div className="sh-hint">Showing the {rows.length} biggest of {usage.by_label.length} callers.</div>}
          {usage.priced && usage.unpriced_models.length > 0 && (
            <div className="sh-hint">No price is set for: {usage.unpriced_models.join(', ')}. Add it to LLM_PRICES to include it in the estimate.</div>
          )}
        </>
      )}
    </section>
  );
};

const CountersCard: React.FC<{ counters: CountersSummary }> = ({ counters }) => {
  const { totals, previous_totals: previousTotals, rates, previous_rates: previous, change: changes } = counters;
  const turns = totals.turns_total ?? 0;
  return (
    <section className="service-card sh-card">
      <div className="card-badge">{turns} TURNS</div>
      <h2>Sonic's behavior</h2>
      <p className="card-description">
        This period against the one before it ({previousTotals.turns_total ?? 0} turns). Rewrite rates are the share of checked replies
        where the first draft had to be rewritten before the user saw it.
      </p>
      <table className="sh-table sh-table-readable">
        <thead>
          <tr><th>Measure</th><th>Now</th><th>Before</th><th>Change</th></tr>
        </thead>
        <tbody>
          {REWRITE_ROWS.map(([key, label]) => (
            <tr key={key}>
              <td>Rewrite: {label}</td>
              <td>{pct(rates[key])}</td><td>{pct(previous[key])}</td><td>{change(changes[key])}</td>
            </tr>
          ))}
          <tr><td>Grounded answers failing the answer check</td><td>{pct(rates.reward_failed)}</td><td>{pct(previous.reward_failed)}</td><td>{change(changes.reward_failed)}</td></tr>
          <tr><td>Turns that failed outright</td><td>{pct(rates.turn_failed)}</td><td>{pct(previous.turn_failed)}</td><td>{change(changes.turn_failed)}</td></tr>
          <tr><td>Turns offered a relatable line</td><td>{pct(rates.relatable_offered)}</td><td>{pct(previous.relatable_offered)}</td><td>{change(changes.relatable_offered)}</td></tr>
          <tr><td>Turns where an upcoming thing was noted for later</td><td>{pct(rates.open_loop_captured)}</td><td>{pct(previous.open_loop_captured)}</td><td>{change(changes.open_loop_captured)}</td></tr>
          <tr><td>Turns invited to connect to something from earlier</td><td>{pct(rates.callback_offered)}</td><td>{pct(previous.callback_offered)}</td><td>{change(changes.callback_offered)}</td></tr>
          <tr><td>Turns where what's changed about Sonic was offered</td><td>{pct(rates.self_history_offered)}</td><td>{pct(previous.self_history_offered)}</td><td>{change(changes.self_history_offered)}</td></tr>
          <tr><td>Messages Sonic started that got a reply</td><td>{pct(rates.initiation_answered)}</td><td>{pct(previous.initiation_answered)}</td><td>{change(changes.initiation_answered)}</td></tr>
          <tr><td>Messages about something they're into that got a reply</td><td>{pct(rates.initiation_interest_answered)}</td><td>{pct(previous.initiation_interest_answered)}</td><td>{change(changes.initiation_interest_answered)}</td></tr>
          <tr><td>Messages Sonic started that fell back to the plain template</td><td>{pct(rates.initiation_fallback)}</td><td>{pct(previous.initiation_fallback)}</td><td>{change(changes.initiation_fallback)}</td></tr>
          <tr><td>Turns that asked how something went</td><td>{pct(rates.open_loop_offered)}</td><td>{pct(previous.open_loop_offered)}</td><td>{change(changes.open_loop_offered)}</td></tr>
          <tr><td>Turns where the reply mentioned a leaning of its own</td><td>{pct(rates.self_mention)}</td><td>{pct(previous.self_mention)}</td><td>{change(changes.self_mention)}</td></tr>
          <tr><td>Coding turns that ran out of steps ({totals.code_turns ?? 0} coding turns)</td><td>{pct(rates.code_cap_hit)}</td><td>{pct(previous.code_cap_hit)}</td><td>{change(changes.code_cap_hit)}</td></tr>
          <tr><td>Proposed edits that were also tested ({totals.code_edited ?? 0} edits)</td><td>{pct(rates.code_verified)}</td><td>{pct(previous.code_verified)}</td><td>{change(changes.code_verified)}</td></tr>
          <tr><td>Messages after coding work that said it did not work ({totals.code_followed ?? 0} messages)</td><td>{pct(rates.code_redo)}</td><td>{pct(previous.code_redo)}</td><td>{change(changes.code_redo)}</td></tr>
        </tbody>
      </table>
      <h3 className="sh-subhead">Questions about Sonic's own feelings</h3>
      <p className="card-description">
        How often each kind was asked, and how often Sonic's first draft claimed something it should not have, so the identity check sent it back. Rewrites on turns that asked nothing
        of the kind are claims that came up unprompted: that line should stay near zero.
      </p>
      <table className="sh-table sh-table-readable">
        <thead>
          <tr><th>Kind</th><th>Asked</th><th>Sent back</th><th>Share sent back</th></tr>
        </thead>
        <tbody>
          {Object.entries(FEELING_LABELS).map(([kind, label]) => (
            <tr key={kind}>
              <td>{label}</td>
              <td>{totals[`feeling_asked_${kind}`] ?? 0}</td>
              <td>{totals[`feeling_revised_${kind}`] ?? 0}</td>
              <td>{pct(rates[`feeling_revised_${kind}`])}</td>
            </tr>
          ))}
          <tr>
            <td>Turns that asked nothing of the kind</td>
            <td>-</td>
            <td>{totals.feeling_revised_other ?? 0}</td>
            <td>{pct(rates.feeling_revised_other)} of all turns</td>
          </tr>
        </tbody>
      </table>
      <div className="sh-routes">
        <span className="sh-chip-label">Where turns went:</span>
        {Object.keys(ROUTE_LABELS).map(route => (
          <span key={route} className="sh-chip">{ROUTE_LABELS[route]} {pct(rates[`route_${route}`])}</span>
        ))}
      </div>
    </section>
  );
};

const SafetyCard: React.FC<{ safety: SafetySummary }> = ({ safety }) => {
  const lastResortWarn = safety.last_resort_line > 0;
  return (
    <section className="service-card sh-card">
      <div className="card-badge">{safety.risk_raised} RAISED</div>
      <h2>Safety layer</h2>
      <p className="card-description">
        How the crisis support ladder has been behaving. Counts only: no messages and no names. The number to watch is how often
        code had to add the human line because the model left it out.
      </p>
      <div className="sh-stat-grid">
        <div className="sh-stat"><div className="sh-stat-value">{safety.risk_raised}</div><div className="sh-stat-label">times risk was raised, by {safety.people_with_risk_raised} people</div></div>
        <div className="sh-stat"><div className="sh-stat-value">{safety.risk_turns}</div><div className="sh-stat-label">replies delivered under a risk level</div></div>
        <div className="sh-stat"><div className="sh-stat-value">{pct(safety.revised_rate)}</div><div className="sh-stat-label">of those had the draft rewritten</div></div>
        <div className="sh-stat"><div className="sh-stat-value">{pct(safety.line_rate)}</div><div className="sh-stat-label">named a human line</div></div>
        <div className={`sh-stat ${lastResortWarn ? 'sh-stat-warn' : ''}`}>
          <div className="sh-stat-value">{pct(safety.last_resort_rate)}</div>
          <div className="sh-stat-label">had the line added by code ({safety.last_resort_line}); should stay near 0</div>
        </div>
        <div className="sh-stat"><div className="sh-stat-value">{safety.outage_fallbacks}</div><div className="sh-stat-label">times the model was down on a risk turn</div></div>
      </div>
      <div className="sh-two">
      <div className="sh-routes">
        <span className="sh-chip-label">By level:</span>
        {Object.keys(safety.by_level).length === 0 && <span className="sh-chip">none</span>}
        {Object.entries(safety.by_level).map(([level, n]) => <span key={level} className="sh-chip">{level} {n}</span>)}
        <span className="sh-chip-label">By detector:</span>
        {Object.keys(safety.by_detector).length === 0 && <span className="sh-chip">none</span>}
        {Object.entries(safety.by_detector).map(([d, n]) => <span key={d} className="sh-chip">{d} {n}</span>)}
      </div>
      <div className="sh-scroll"><table className="sh-table">
        <thead><tr><th>Ladder step</th><th>Had someone</th><th>Had no one</th></tr></thead>
        <tbody>
          {Object.entries(safety.ladder).map(([rung, counts]) => (
            <tr key={rung}>
              <td>{rung.replace(/_/g, ' ')}</td><td>{counts.available ?? 0}</td><td>{counts.unavailable ?? 0}</td>
            </tr>
          ))}
        </tbody>
      </table></div>
      </div>
    </section>
  );
};

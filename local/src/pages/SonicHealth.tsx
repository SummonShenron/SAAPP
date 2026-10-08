import React, { useEffect, useState } from 'react';
import {
  api,
  type CountersSummary,
  type ObservationView,
  type ReflectionResult,
  type SafetySummary,
  type SonicHealth,
} from '../api';
import './__styles__/SelfService.css';
import './__styles__/SonicHealth.css';

const PERIODS = [7, 30, 90];

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

function pct(value: number | null | undefined): string {
  if (value === null || value === undefined) return 'n/a';
  const n = value * 100;
  return `${n >= 10 ? n.toFixed(0) : n.toFixed(1)}%`;
}

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

function shortDate(iso: string | null | undefined): string {
  if (!iso) return '';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
}

function evidenceLine(evidence: ObservationView['proposed_evidence']): string {
  if (!evidence || evidence.rate === null || evidence.rate === undefined) return '';
  const base = evidence.denominator ? ` across ${evidence.denominator} replies or turns` : '';
  const before = evidence.previous_rate !== null && evidence.previous_rate !== undefined
    ? `, was ${pct(evidence.previous_rate)} the period before`
    : '';
  return `Now ${pct(evidence.rate)}${before}${base}.`;
}

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
      setNotice(`Approved "${OBSERVATION_LABELS[key] ?? key}". It is live now and applies to the contexts shown.`);
    } catch (err) {
      setNotice(err instanceof Error ? err.message : 'Could not approve that observation.');
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

  return (
    <div className="self-service-container">
      <header className="self-service-header">
        <h1>Sonic Health</h1>
        <p>How the safety layer and Sonic itself have been behaving. Aggregate counts only: nothing on this page contains a person or a message.</p>
      </header>

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

      {notice && <div className="sh-notice" role="status">{notice}</div>}

      <div className="vertical-card-stack">
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
        <CountersCard counters={health.counters} />
        <SafetyCard safety={health.safety} />
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
      <table className="sh-table">
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
          <tr><td>Turns where the reply mentioned a leaning of its own</td><td>{pct(rates.self_mention)}</td><td>{pct(previous.self_mention)}</td><td>{change(changes.self_mention)}</td></tr>
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
      <div className="sh-routes">
        <span className="sh-chip-label">By level:</span>
        {Object.keys(safety.by_level).length === 0 && <span className="sh-chip">none</span>}
        {Object.entries(safety.by_level).map(([level, n]) => <span key={level} className="sh-chip">{level} {n}</span>)}
        <span className="sh-chip-label">By detector:</span>
        {Object.keys(safety.by_detector).length === 0 && <span className="sh-chip">none</span>}
        {Object.entries(safety.by_detector).map(([d, n]) => <span key={d} className="sh-chip">{d} {n}</span>)}
      </div>
      <table className="sh-table">
        <thead><tr><th>Ladder step</th><th>Had someone</th><th>Had no one</th></tr></thead>
        <tbody>
          {Object.entries(safety.ladder).map(([rung, counts]) => (
            <tr key={rung}>
              <td>{rung.replace(/_/g, ' ')}</td><td>{counts.available ?? 0}</td><td>{counts.unavailable ?? 0}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
};

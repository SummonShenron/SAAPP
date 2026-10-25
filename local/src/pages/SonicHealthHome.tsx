import React from 'react';
import type { SonicHealth } from '../api';
import { BarRow, BudgetBars, StatTile } from './sonicHealthParts';
import { WhoSpentTile } from './SonicHealthWhoSpent';
import { DailyChart } from './SonicHealthChart';
import { KB_LABELS, pct, tokens, usd } from './sonicHealthFormat';

export interface AttentionItem {
  key: string;
  text: string;
  tab: string;
  tone: 'info' | 'warn';
}

/** What needs a person's eyes right now, worked out from the same data the tabs show. Empty means nothing does. */
export function attentionItems(health: SonicHealth, awaiting: number, suggestionsWaiting: number): AttentionItem[] {
  const items: AttentionItem[] = [];
  const budget = health.budget;
  if (budget && (budget.level === 'throttled' || budget.level === 'blocked')) {
    items.push({ key: 'budget', tab: 'budget', tone: 'warn', text: budget.level === 'blocked' ? 'The usage budget has paused chat.' : 'Sonic is in low-power mode: the usage budget has been reached.' });
  } else if (budget && budget.level === 'notice') {
    items.push({ key: 'budget', tab: 'budget', tone: 'info', text: 'The usage budget is getting close (80% or more).' });
  }
  if (health.safety.last_resort_line > 0) {
    items.push({ key: 'safety', tab: 'safety', tone: 'warn', text: `Code had to add the human support line ${health.safety.last_resort_line} time(s) because the model left it out.` });
  }
  if (health.safety.outage_fallbacks > 0) {
    items.push({ key: 'outage', tab: 'safety', tone: 'warn', text: `The model was down on ${health.safety.outage_fallbacks} turn(s) where risk had been raised.` });
  }
  const graph = health.graph;
  if (graph && graph.documents_total > 0 && graph.edges_total === 0) {
    items.push({ key: 'graph', tab: 'knowledge', tone: 'warn', text: 'The knowledge graph is empty, so graph retrieval is adding nothing to answers.' });
  } else if (graph && graph.documents_without_edges > 0) {
    items.push({ key: 'graph', tab: 'knowledge', tone: 'warn', text: `${graph.documents_without_edges} document(s) have no knowledge-graph relationships.` });
  }
  const check = health.routines?.find(r => r.id === 'health_check')?.last;
  if (check && (check.status === 'attention' || check.status === 'failed')) {
    items.push({ key: 'health_check', tab: 'routines', tone: 'warn', text: check.status === 'failed' ? 'The last health check could not finish.' : `The last health check flagged something: ${check.result.replace(/^flagged /, '')}.` });
  }
  if (awaiting > 0) items.push({ key: 'observations', tab: 'observations', tone: 'info', text: `${awaiting} self-observation(s) are waiting for your approval.` });
  if (suggestionsWaiting > 0) items.push({ key: 'suggestions', tab: 'suggestions', tone: 'info', text: `${suggestionsWaiting} suggestion(s) are waiting for a decision.` });
  const failed = health.usage?.turns?.error_turns ?? 0;
  if (failed > 0) items.push({ key: 'errors', tab: 'turns', tone: 'warn', text: `${failed} chat turn(s) failed outright in this period.` });
  return items;
}

interface HomeProps {
  health: SonicHealth;
  awaiting: number;
  suggestionsWaiting: number;
  onOpen: (tab: string) => void;
}

const TOOL_REASON_SHORT: Record<string, string> = {
  repo_question: 'a question about the code',
  follow_up: 'a short follow-up',
  web_search: 'web search',
  code_or_data: 'run code or query data',
  calendar: 'calendar',
  gmail: 'Gmail',
  drive: 'Drive',
  admin_ops: 'incidents, logs, deploys',
  approval_continuation: 'answering an approval',
  clarification_resume: 'answering its own question',
  commit_request: 'commit, push or new branch',
};

/** A panel that is also a button: the whole thing opens the tab it summarises. */
const Tile: React.FC<{ title: string; tab: string; onOpen: (tab: string) => void; badge?: string; children: React.ReactNode }> = ({ title, tab, onOpen, badge, children }) => (
  <section className="sh-home-tile" onClick={() => onOpen(tab)} role="link" tabIndex={0} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onOpen(tab); } }}>
    <div className="sh-home-tile-head"><h3>{title}</h3>{badge && <span className="sh-pill">{badge}</span>}<span className="sh-home-open">Open ›</span></div>
    {children}
  </section>
);

export const HomeTab: React.FC<HomeProps> = ({ health, awaiting, suggestionsWaiting, onOpen }) => {
  const { usage, budget, safety, counters } = health;
  const turns = usage?.turns;
  const attention = attentionItems(health, awaiting, suggestionsWaiting);
  const total = usage?.total;
  const turnCount = counters.totals.turns_total ?? 0;
  const cost = usage?.priced ? usage.estimated_cost : null;
  const perTurnCost = cost !== null && cost !== undefined && turnCount > 0 ? cost / turnCount : null;
  const toolShare = turns && turns.turns > 0 ? turns.tool.turns / turns.turns : null;
  const rates = counters.rates;
  const changes = counters.change;
  const dailyLimit = budget?.detail?.find(item => item.scope === 'day' && item.unit === 'usd')?.limit ?? null;
  const topCallers = [...(usage?.by_label ?? [])].sort((a, b) => b.tokens - a.tokens).slice(0, 6);
  const biggest = topCallers.length ? topCallers[0].tokens : 0;
  const routing = (turns?.routing ?? []).slice(0, 4);
  const toolRows = (turns?.tools ?? []).slice(0, 5);

  return (
    <div className="sh-home">
      <section className="sh-attention" aria-label="Needs attention">
        <h3>Needs attention</h3>
        {attention.length === 0 ? (
          <div className="sh-hint">Nothing needs attention right now.</div>
        ) : (
          <ul>
            {attention.map(item => (
              <li key={item.key} className={`sh-attention-${item.tone}`}>
                <span>{item.text}</span>
                <button type="button" className="sh-btn" onClick={() => onOpen(item.tab)}>Open</button>
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* Three stacks: each is its own column, so a panel sits directly under the one above it whatever the height of its neighbours. */}
      <div className="sh-home-layout">
        <div className="sh-home-col">
          {budget && (
            <Tile title="Usage budget" tab="budget" onOpen={onOpen} badge={budget.level === 'off' ? 'NOT SET' : budget.level.toUpperCase()}>
              {budget.level === 'off' ? <div className="sh-hint">No budget is set, so nothing is limited.</div> : <BudgetBars budget={budget} />}
            </Tile>
          )}

          <Tile title="Safety layer" tab="safety" onOpen={onOpen} badge={`${safety.risk_raised} RAISED`}>
            <div className="sh-stat-grid sh-stat-grid-tight">
              <StatTile value={safety.risk_raised} label={`times risk was raised (${safety.people_with_risk_raised} people)`} />
              <StatTile value={pct(safety.line_rate)} label="named a human line" />
              <StatTile value={safety.last_resort_line} label="line added by code; should stay 0" warn={safety.last_resort_line > 0} />
              <StatTile value={safety.outage_fallbacks} label="model down on a risk turn" warn={safety.outage_fallbacks > 0} />
            </div>
          </Tile>

          <Tile title="Sonic's behavior" tab="behavior" onOpen={onOpen} badge={`${turnCount} TURNS`}>
            <table className="sh-table sh-table-tight">
              <thead><tr><th>Measure</th><th>Now</th><th>Change</th></tr></thead>
              <tbody>
                <tr><td>Turns that failed outright</td><td>{pct(rates.turn_failed)}</td><td>{changeText(changes.turn_failed)}</td></tr>
                <tr><td>Grounded answers failing the check</td><td>{pct(rates.reward_failed)}</td><td>{changeText(changes.reward_failed)}</td></tr>
                <tr><td>Messages Sonic started that got a reply</td><td>{pct(rates.initiation_answered)}</td><td>{changeText(changes.initiation_answered)}</td></tr>
              </tbody>
            </table>
          </Tile>

          {health.feedback && (
            <Tile title="Feedback" tab="feedback" onOpen={onOpen} badge={`${health.feedback.period.positive + health.feedback.period.negative} IN PERIOD`}>
              <div className="sh-stat-grid sh-stat-grid-tight">
                <StatTile value={health.feedback.period.user_positive} label="thumbs-up from people" />
                <StatTile value={health.feedback.period.user_negative} label="thumbs-down from people" warn={health.feedback.period.user_negative > 0} />
                <StatTile value={health.feedback.period.evaluator_negative} label="redone by the answer checker" />
                <StatTile value={health.feedback.applied.turns} label="turns it was shown to the model on" />
              </div>
            </Tile>
          )}

          {health.graph && (
            <Tile title="Knowledge graph" tab="knowledge" onOpen={onOpen} badge={`${health.graph.edges_total} RELATIONSHIPS`}>
              {health.graph.by_affiliate.map(row => (
                <BarRow
                  key={row.affiliate}
                  label={KB_LABELS[row.affiliate] ?? row.affiliate}
                  value={<>{row.edges}{row.documents_without_edges > 0 && <span className="sh-err"> · {row.documents_without_edges} doc(s) with none</span>}</>}
                  fraction={health.graph && health.graph.edges_total > 0 ? row.edges / Math.max(...health.graph.by_affiliate.map(r => r.edges), 1) : 0}
                  tone={row.documents_without_edges > 0 ? 'notice' : 'ok'}
                />
              ))}
            </Tile>
          )}
        </div>

        <div className="sh-home-center">
          {usage?.daily && usage.daily.length > 0 && (
            <section className="sh-home-tile sh-chart-tile">
              <div className="sh-home-tile-head"><h3>Day by day</h3><span className="sh-pill">{usage.daily.length} DAYS</span></div>
              <DailyChart daily={usage.daily} dailyLimitUsd={dailyLimit} onOpen={() => onOpen('usage')} />
            </section>
          )}

          {/* Who used the day and what each cost against the day's total. */}
          <WhoSpentTile users={health.users} onOpen={onOpen} />

          {topCallers.length > 0 && (
            <Tile title="Where the tokens went" tab="usage" onOpen={onOpen} badge="TOP CALLERS">
              {topCallers.map(row => (
                <BarRow
                  key={`${row.label}|${row.model}`}
                  label={<span title={`${row.label} (${row.model})`}>{row.label}</span>}
                  value={`${tokens(row.tokens)}${usage?.priced && row.cost !== null && row.cost !== undefined ? ` · ${usd(row.cost) || '$0.00'}` : ''}`}
                  fraction={biggest > 0 ? row.tokens / biggest : 0}
                />
              ))}
            </Tile>
          )}

        </div>

        <div className="sh-home-col">
          {usage && (
            <Tile title="Model spend" tab="usage" onOpen={onOpen} badge={total ? `${tokens(total.tokens)} TOKENS` : undefined}>
              {total && total.calls > 0 ? (
                <div className="sh-stat-grid sh-stat-grid-tight">
                  <StatTile value={cost !== null && cost !== undefined ? usd(cost) : 'n/a'} label="estimated cost" />
                  <StatTile value={perTurnCost !== null ? usd(perTurnCost) : 'n/a'} label="per chat turn" />
                  <StatTile value={total.calls} label="model calls" />
                  <StatTile value={pct(total.cache_rate)} label="of input from cache" />
                </div>
              ) : <div className="sh-hint">No model calls counted in this period yet.</div>}
            </Tile>
          )}

          {turns && turns.turns > 0 && (
            <Tile title="Chat turns" tab="turns" onOpen={onOpen} badge={`${turns.turns} TURNS`}>
              <div className="sh-stat-grid sh-stat-grid-tight">
                <StatTile value={turns.overall.avg_calls ?? 'n/a'} label="model calls per turn" />
                <StatTile value={turns.plain.avg_calls ?? 'n/a'} label="calls per plain chat turn" />
                <StatTile value={turns.tool.avg_calls ?? 'n/a'} label="calls per tool turn" />
                <StatTile value={turns.deep.turns} label="deep-mode turns" />
              </div>
              {toolShare !== null && <BarRow label="Turns that used the tool agent" value={`${turns.tool.turns} of ${turns.turns} (${pct(toolShare)})`} fraction={toolShare} tone={toolShare > 0.4 ? 'notice' : 'ok'} />}
            </Tile>
          )}

          {toolRows.length > 0 && (
            <Tile title="Tools called" tab="turns" onOpen={onOpen} badge={`${turns?.tool_calls_total ?? 0} CALLS`}>
              {toolRows.map(row => (
                <BarRow
                  key={row.tool}
                  label={row.tool}
                  value={<>{row.calls}{row.errors > 0 && <span className="sh-err"> · {row.errors} err</span>}</>}
                  fraction={toolRows[0].calls > 0 ? row.calls / toolRows[0].calls : 0}
                />
              ))}
            </Tile>
          )}

          {routing.length > 0 && turns && (
            <Tile title="Why turns used tools" tab="turns" onOpen={onOpen} badge="EXPENSIVE PATH">
              {routing.map(row => (
                <BarRow
                  key={row.reason}
                  label={TOOL_REASON_SHORT[row.reason] ?? row.reason}
                  value={`${row.turns}`}
                  fraction={turns.tool.turns > 0 ? row.turns / turns.tool.turns : 0}
                />
              ))}
            </Tile>
          )}
        </div>
      </div>
    </div>
  );
};

function changeText(value: number | null | undefined): string {
  if (value === null || value === undefined) return '–';
  const points = value * 100;
  if (Math.abs(points) < 0.05) return 'no change';
  return `${points > 0 ? '▲' : '▼'} ${Math.abs(points).toFixed(1)} pts`;
}

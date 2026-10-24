import React from 'react';
import type { FeedbackSummary, GraphSummary } from '../api';
import { BarRow, StatTile } from './sonicHealthParts';
import { KB_LABELS, pct } from './sonicHealthFormat';

const TAG_LABELS: Record<string, string> = {
  hallucination: 'Made something up',
  incorrect_filter: 'Wrong filter or scope',
  formatting: 'Formatting',
  incomplete: 'Incomplete',
  code_wrong: 'Code was wrong or did not work',
  tool_failure: 'Tool or integration failed',
  forgot_context: 'Forgot earlier context',
  ignored_request: 'Did not do what I asked',
  tone_off: 'Tone felt off',
  too_long: 'Too long or wordy',
  other: 'Other',
};

export const FeedbackTab: React.FC<{ feedback: FeedbackSummary | null | undefined }> = ({ feedback }) => {
  if (!feedback) return <div className="sh-hint">Feedback could not be read just now.</div>;
  const { period, stored, applied, by_tag: byTag, daily } = feedback;
  const given = period.positive + period.negative;
  const biggestTag = byTag.length ? byTag[0].count : 0;
  const activeDays = daily.filter(d => d.positive + d.negative > 0).slice(-14).reverse();

  return (
    <div className="sh-stack">
      <section className="service-card sh-card">
        <h2>Feedback</h2>
        <div className="sh-hint">
          Feedback is not just a report card. When a later question from the same person is close in meaning to one that got feedback, the model is shown
          a thumbs-down as a guardrail ("do not answer like this") and a thumbs-up as a preferred example. So it can change what Sonic says.
        </div>
        <div className="sh-stat-grid">
          <StatTile value={period.user_positive} label="thumbs-up from people" />
          <StatTile value={period.user_negative} label="thumbs-down from people" warn={period.user_negative > 0} />
          <StatTile value={period.evaluator_negative} label="answers the checker made Sonic redo" warn={period.evaluator_negative > 0} />
          <StatTile value={pct(period.negative_share)} label={`of ${given} pieces of feedback were negative`} />
        </div>
        <div className="sh-hint">
          The answer checker judges grounded answers and has Sonic rewrite one that fails; each redo is saved as feedback too, so most of the negative
          count can be the checker and not people. {period.people} {period.people === 1 ? 'person' : 'people'} gave feedback in this period.
        </div>
      </section>

      <section className="service-card sh-card">
        <h2>What can reach the model</h2>
        <div className="sh-stat-grid">
          <StatTile value={stored.total} label={`pieces of feedback stored (${stored.positive} up, ${stored.negative} down)`} />
          <StatTile value={stored.matchable} label="can be matched to a new question by meaning" />
          <StatTile value={stored.unmatchable} label="can only match near-identical wording" warn={stored.unmatchable > stored.matchable} />
          <StatTile value={applied.turns} label={`turns it was shown on${applied.share !== null ? ` (${pct(applied.share)} of turns)` : ''}`} />
        </div>
        {stored.unmatchable > 0 && (
          <div className="sh-hint">
            {stored.unmatchable} older record(s) have no embedding, so they are only ever used when a question starts with nearly the same words.
          </div>
        )}
        {stored.positives_old_rule_misread > 0 && (
          <div className="sh-hint">
            {stored.positives_old_rule_misread} thumbs-up were stored in a form that an earlier rule read as negative, so they could have been shown to
            the model as things to avoid. They are read correctly now, and new thumbs-up are stored as positive.
          </div>
        )}
      </section>

      <section className="service-card sh-card sh-two">
        <div>
          <h3 className="sh-subhead">What the negative feedback was about</h3>
          {byTag.length === 0 ? <div className="sh-hint">No negative feedback in this period.</div> : byTag.map(row => (
            <BarRow key={row.tag} label={TAG_LABELS[row.tag] ?? row.tag} value={row.count} fraction={biggestTag > 0 ? row.count / biggestTag : 0} tone="notice" />
          ))}
        </div>
        <div>
          <h3 className="sh-subhead">Recent days</h3>
          {activeDays.length === 0 ? <div className="sh-hint">No feedback in this period.</div> : (
            <table className="sh-table sh-table-tight">
              <thead><tr><th>Day</th><th>Up</th><th>Down</th></tr></thead>
              <tbody>{activeDays.map(d => <tr key={d.day}><td>{d.day}</td><td>{d.positive}</td><td>{d.negative}</td></tr>)}</tbody>
            </table>
          )}
        </div>
      </section>
    </div>
  );
};

export const KnowledgeTab: React.FC<{ graph: GraphSummary | null | undefined }> = ({ graph }) => {
  if (!graph) return <div className="sh-hint">The knowledge graph could not be read just now.</div>;
  const empty = graph.edges_total === 0 && graph.documents_total > 0;
  return (
    <div className="sh-stack">
      <section className="service-card sh-card">
        <h2>Knowledge graph</h2>
        <div className="sh-hint">
          When someone asks a question, retrieval adds the relationships between the things it names (from the knowledge bases they may use) to the
          answer's context, next to the matching passages. The edges are built when a document is ingested. A graph with nothing in it looks exactly
          like a graph that found nothing, so this page counts them: every document that has passages but no edges is listed.
        </div>
        <div className="sh-stat-grid">
          <StatTile value={graph.edges_total} label="relationships stored" warn={empty} />
          <StatTile value={graph.documents_total} label="documents indexed" />
          <StatTile value={graph.documents_without_edges} label="documents with no relationships" warn={graph.documents_without_edges > 0} />
        </div>
        {empty && <div className="sh-hint sh-err">There are no relationships at all, so graph retrieval is adding nothing to any answer.</div>}
      </section>

      <section className="service-card sh-card">
        <h2>By knowledge base</h2>
        <table className="sh-table">
          <thead><tr><th>Knowledge base</th><th>Documents</th><th>Passages</th><th>Relationships</th><th>Without any</th></tr></thead>
          <tbody>
            {graph.by_affiliate.map(row => (
              <tr key={row.affiliate}>
                <td>{KB_LABELS[row.affiliate] ?? row.affiliate}</td>
                <td>{row.documents}</td>
                <td>{row.chunks}</td>
                <td>{row.edges}</td>
                <td className={row.documents_without_edges > 0 ? 'sh-err' : undefined}>{row.documents_without_edges}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {graph.by_affiliate.filter(row => row.missing.length > 0).map(row => (
          <div key={row.affiliate} className="sh-hint">
            {KB_LABELS[row.affiliate] ?? row.affiliate}, no relationships: {row.missing.join(', ')}{row.documents_without_edges > row.missing.length ? ' and more' : ''}.
          </div>
        ))}
        {graph.documents_without_edges > 0 && (
          <div className="sh-hint">
            To build them for existing documents: <code>python -m backend.utils.graph_backfill</code> shows what it would do, and adding <code>--apply</code> does it.
          </div>
        )}
      </section>
    </div>
  );
};

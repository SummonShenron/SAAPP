import React from 'react';
import type { UsersSummary } from '../api';
import { pct, tokens, usd } from './sonicHealthFormat';
/** A long account id, shortened in the middle so a table stays readable; the full id is the tooltip. */
function who(user: string): string {
  return user.length > 34 ? `${user.slice(0, 18)}…${user.slice(-12)}` : user;
}

/** What each account cost over the period. The one place the page names a person; counts and dollars only, never what anyone said. */
export const UsersCard: React.FC<{ users: UsersSummary | null | undefined }> = ({ users }) => {
  if (users === null || users === undefined) {
    return (
      <section className="service-card sh-card">
        <h2>Cost by person</h2>
        <div className="sh-hint">The per-person figures could not be read just now.</div>
      </section>
    );
  }
  const priced = users.priced;
  const average = users.people > 0 ? users.cost / users.people : null;
  return (
    <section className="service-card sh-card">
      <div className="card-badge">{users.people} {users.people === 1 ? 'PERSON' : 'PEOPLE'}</div>
      <h2>Cost by person</h2>
      <p className="card-description">
        What each account's chat turns cost in this period, from the priced model calls made during their turns, with the runs of the evals and tests listed
        beside them as their own entry. Counts and dollars only: no prompts, no replies. Admin-only. It counts from when per-person daily totals were first saved, so a longer period looks shorter than it is until then.
      </p>
      {users.rows.length === 0 ? (
        <div className="sh-hint">No chat turns have been counted for any account yet in this period.</div>
      ) : (
        <>
          <div className="sh-stat-grid">
            <div className="sh-stat"><div className="sh-stat-value">{users.people}</div><div className="sh-stat-label">active accounts</div></div>
            {priced && <div className="sh-stat"><div className="sh-stat-value">{usd(users.cost) || '$0.00'}</div><div className="sh-stat-label">total across accounts</div></div>}
            {priced && average !== null && <div className="sh-stat"><div className="sh-stat-value">{usd(average) || '$0.00'}</div><div className="sh-stat-label">average per account</div></div>}
            {priced && users.evals && (
              <div className="sh-stat"><div className="sh-stat-value">{usd(users.evals.cost) || '$0.00'}</div><div className="sh-stat-label">testing and evals</div></div>
            )}
            {priced && users.unattributed_cost !== null && users.unattributed_cost !== undefined && (
              <div className="sh-stat"><div className="sh-stat-value">{usd(users.unattributed_cost) || '$0.00'}</div><div className="sh-stat-label">not tied to anyone (background jobs)</div></div>
            )}
          </div>
          <div className="sh-scroll"><table className="sh-table">
            <thead>
              <tr>
                <th>Account</th>
                {priced && <th>Cost</th>}
                <th>{priced ? 'Share' : 'Share of tokens'}</th>
                <th>Turns</th>
                {priced && <th>Per turn</th>}
                {priced && <th>Per active day</th>}
                <th>Active days</th>
                <th>Tokens</th>
              </tr>
            </thead>
            <tbody>
              {users.rows.map(row => (
                <tr key={`${row.kind ?? 'person'}:${row.user}`}>
                  {row.kind === 'evals'
                    ? <td title="Runs of the evals and tests: not a person, and not chat turns"><em>{row.user}</em> <span className="sh-hint">(not a person)</span></td>
                    : <td title={row.user}>{who(row.user)}</td>}
                  {priced && <td>{usd(row.cost) || '$0.00'}</td>}
                  <td>{pct(row.share)}</td>
                  <td>{row.kind === 'evals' ? '—' : row.turns}</td>
                  {priced && <td>{row.cost_per_turn === null ? 'n/a' : usd(row.cost_per_turn)}</td>}
                  {priced && <td>{row.cost_per_active_day === null ? 'n/a' : usd(row.cost_per_active_day)}</td>}
                  <td>{row.active_days}</td>
                  <td>{tokens(row.tokens)}</td>
                </tr>
              ))}
              {users.others && (
                <tr>
                  <td>{users.others.people} more {users.others.people === 1 ? 'account' : 'accounts'}</td>
                  {priced && <td>{usd(users.others.cost) || '$0.00'}</td>}
                  <td></td><td></td>{priced && <td></td>}{priced && <td></td>}<td></td>
                  <td>{tokens(users.others.tokens)}</td>
                </tr>
              )}
            </tbody>
          </table></div>
          {!priced && (
            <div className="sh-hint">No dollar amounts yet: set LLM_PRICES (see the Usage tab) so each turn can be priced, and costs will start showing here.</div>
          )}
        </>
      )}
    </section>
  );
};

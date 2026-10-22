import React, { useCallback, useEffect, useState } from 'react';
import { api } from '../api';
import type { BudgetIncreaseRequest, BudgetRequests } from '../api';
import { budgetAmount } from './sonicHealthFormat';

const WINDOW_TEXT = { my_day: 'daily', my_five: '5-hour' } as const;

function amount(request: BudgetIncreaseRequest): string {
  if (request.spent == null || request.limit == null || !request.unit) return '';
  return `${budgetAmount(request.unit, request.spent)} of ${budgetAmount(request.unit, request.limit)}`;
}

/** People who reached 80% of their own limit and asked for +25% on it. An admin approves (their limit is 25% higher until that window or day ends) or declines. */
export const BudgetRequestsPanel: React.FC<{ onChanged?: () => void }> = ({ onChanged }) => {
  const [requests, setRequests] = useState<BudgetRequests | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState('');

  const load = useCallback(async () => {
    try {
      setRequests(await api.getBudgetRequests());
      setError('');
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load the requests.');
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const decide = async (id: string, decision: 'approve' | 'decline') => {
    setBusy(id);
    setError('');
    try {
      await api.decideBudgetRequest(id, decision);
      onChanged?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not save the decision.');
    } finally {
      setBusy('');
      await load();
    }
  };

  const pending = requests?.pending ?? [];
  const recent = requests?.recent ?? [];
  return (
    <section className="service-card sh-card">
      <div className="card-badge">{pending.length} WAITING</div>
      <h2>Requests for more usage</h2>
      <p className="card-description">
        Someone who has used 80% of their own limit can ask for 25% more on the window that is furthest along (the 5 hours or the day). Approving raises their
        limit by 25% until that window or day ends; it never changes anyone else's.
      </p>
      {error && <div className="sh-hint" role="alert">{error}</div>}
      {requests && pending.length === 0 && <div className="sh-hint">No requests are waiting.</div>}
      {pending.length > 0 && (
        <table className="sh-table">
          <thead><tr><th>Person</th><th>Window</th><th>Used</th><th>Asked</th><th /></tr></thead>
          <tbody>
            {pending.map(request => (
              <tr key={request.id}>
                <td title={request.user}>{request.user}</td>
                <td>{WINDOW_TEXT[request.scope] ?? request.scope}</td>
                <td>{amount(request)}{request.fraction != null ? ` (${Math.round(request.fraction * 100)}%)` : ''}</td>
                <td>{new Date(request.requested_at).toLocaleString()}</td>
                <td>
                  <div className="sh-actions">
                    <button type="button" className="sh-btn sh-btn-primary" disabled={busy === request.id} onClick={() => decide(request.id, 'approve')}>Approve +25%</button>
                    <button type="button" className="sh-btn" disabled={busy === request.id} onClick={() => decide(request.id, 'decline')}>Decline</button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {recent.length > 0 && (
        <>
          <h3>Recent</h3>
          <table className="sh-table">
            <tbody>
              {recent.map(request => (
                <tr key={request.id}>
                  <td title={request.user}>{request.user}</td>
                  <td>{WINDOW_TEXT[request.scope] ?? request.scope}</td>
                  <td>{request.status === 'pending' ? 'lapsed' : request.status}{request.decided_by ? ` by ${request.decided_by}` : ''}</td>
                  <td>{new Date(request.decided_at ?? request.requested_at).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </section>
  );
};

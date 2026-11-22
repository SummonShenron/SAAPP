import { useEffect, useRef, useState } from 'react';
import { api } from '../api';
import { outcomeLine, type ChangeCardData, type ChangeStatus } from './changeCardLogic';
import './__styles__/ChangeCard.css';

interface ChangeCardProps {
  card: ChangeCardData;
}

type State =
  | { kind: 'open' }
  | { kind: 'sending' }
  | { kind: 'done'; status: ChangeStatus; message: string };

/**
 * One change to the person's Google Calendar (Sonic's propose_calendar_change tool, backend/services/change_cards.py): what will change, to which event as Google has it now, who is emailed, and Apply
 * and Decline. Sonic's turn is waiting for the press; Apply makes the Google call on the server, once, with the person's own connection, and Sonic carries on with what really happened. If nobody is
 * waiting any more (the turn timed out or ended), the card says so and nothing is changed.
 */
export default function ChangeCard({ card }: ChangeCardProps) {
  const [state, setState] = useState<State>({ kind: 'open' });
  const [error, setError] = useState('');
  const alive = useRef(true);
  useEffect(() => () => { alive.current = false; }, []);

  const press = async (decision: 'apply' | 'decline') => {
    if (state.kind !== 'open') return;
    setError('');
    setState({ kind: 'sending' });
    try {
      const reply = await api.decideChange(card.proposal_id, decision);
      if (!alive.current) return;
      setState({ kind: 'done', status: reply.status, message: reply.message || '' });
    } catch (problem) {
      if (!alive.current) return;
      setError(problem instanceof Error ? problem.message : 'Could not send that.');
      setState({ kind: 'open' });
    }
  };

  const open = state.kind === 'open';
  const dataState = state.kind === 'done' ? state.status : state.kind;

  return (
    <div className="chg-card" role="group" aria-label="A proposed change" data-state={dataState} data-action={card.action} data-destructive={card.destructive ? 'true' : undefined}>
      <div className="chg-heading">{card.heading}</div>
      <div className="chg-title">{card.title}</div>
      {card.rows.length > 0 && (
        <dl className="chg-rows">
          {card.rows.map(row => (
            <div className="chg-row" key={row.label}>
              <dt>{row.label}</dt>
              <dd>{row.value}</dd>
            </div>
          ))}
        </dl>
      )}
      {card.notice && <div className="chg-notice">{card.notice}</div>}
      {(open || state.kind === 'sending') && (
        <div className="chg-actions">
          <button type="button" className="chg-btn chg-primary" data-destructive={card.destructive ? 'true' : undefined} disabled={!open} onClick={() => { void press('apply'); }}>
            {state.kind === 'sending' ? 'Working…' : card.applyLabel}
          </button>
          <button type="button" className="chg-btn" disabled={!open} onClick={() => { void press('decline'); }}>Decline</button>
        </div>
      )}
      {state.kind === 'done' && <div className="chg-status" data-status={state.status} role="status">{outcomeLine(state.status, state.message)}</div>}
      {error && <div className="chg-status chg-error" role="alert">{error}</div>}
    </div>
  );
}

import { useEffect, useRef, useState } from 'react';
import { api } from '../api';
import { MAX_OTHER_CHARS, answerText, cleanOther, type ChoiceAnswer, type ChoiceCardData } from './choiceCardLogic';
import './__styles__/ChoiceCard.css';

interface ChoiceCardProps {
  card: ChoiceCardData;
}

type State =
  | { kind: 'open' }
  | { kind: 'sending' }
  | { kind: 'answered'; answer: ChoiceAnswer }
  | { kind: 'expired' };

/**
 * One question from Sonic with a few options (its ask_choice tool, backend/services/choice_cards.py): a button for each option, its recommendation first, and a box for something else.
 * Sonic's turn is waiting for the answer, so pressing a button sends it straight back and Sonic carries on, with everything it had read still in hand. If nobody is waiting any more
 * (the turn timed out or ended), the card says so, and the answer can simply be typed as a message.
 */
export default function ChoiceCard({ card }: ChoiceCardProps) {
  const [state, setState] = useState<State>({ kind: 'open' });
  const [other, setOther] = useState('');
  const [error, setError] = useState('');
  const alive = useRef(true);
  useEffect(() => () => { alive.current = false; }, []);

  const open = state.kind === 'open';

  const send = async (answer: ChoiceAnswer) => {
    if (state.kind !== 'open') return;
    setError('');
    setState({ kind: 'sending' });
    try {
      const reply = await api.answerChoice(card.choice_id, 'index' in answer ? { index: answer.index } : { other: answer.other });
      if (!alive.current) return;
      setState(reply.status === 'recorded' ? { kind: 'answered', answer } : { kind: 'expired' });
    } catch (problem) {
      if (!alive.current) return;
      setError(problem instanceof Error ? problem.message : 'Could not send your answer.');
      setState({ kind: 'open' });
    }
  };

  const sendOther = () => {
    const words = cleanOther(other);
    if (words) void send({ other: words });
  };

  return (
    <div className="chc-card" role="group" aria-label="A question from Sonic" data-state={state.kind}>
      <div className="chc-question">{card.question}</div>
      <div className="chc-options">
        {card.options.map((option, index) => (
          <button
            key={option.label}
            type="button"
            className="chc-option"
            data-recommended={option.recommended ? 'true' : undefined}
            data-chosen={state.kind === 'answered' && 'index' in state.answer && state.answer.index === index ? 'true' : undefined}
            disabled={!open}
            onClick={() => { void send({ index }); }}
          >
            <span className="chc-label">
              {option.label}
              {option.recommended && <span className="chc-badge">Recommended</span>}
            </span>
            {option.description && <span className="chc-description">{option.description}</span>}
          </button>
        ))}
      </div>
      {open && (
        <div className="chc-other">
          <input
            type="text"
            className="chc-input"
            value={other}
            maxLength={MAX_OTHER_CHARS}
            placeholder="Something else…"
            aria-label="Something else"
            onChange={event => setOther(event.target.value)}
            onKeyDown={event => { if (event.key === 'Enter') { event.preventDefault(); sendOther(); } }}
          />
          <button type="button" className="chc-send" disabled={!cleanOther(other)} onClick={sendOther}>Send</button>
        </div>
      )}
      {state.kind === 'sending' && <div className="chc-status">Sending…</div>}
      {state.kind === 'answered' && <div className="chc-status chc-answered">{answerText(card, state.answer)}</div>}
      {state.kind === 'expired' && <div className="chc-status chc-expired">Sonic is no longer waiting for this. Tell it your answer in a message.</div>}
      {error && <div className="chc-status chc-error" role="alert">{error}</div>}
    </div>
  );
}

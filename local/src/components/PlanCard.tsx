import React, { useState } from 'react';
import { api, type CodePlan } from '../api';
import './__styles__/PlanCard.css';

interface Props {
  sessionId: string;
  plan: CodePlan;
  /** True while a reply is being made: approving sends a message, which waits for the turn in progress. */
  busy: boolean;
  onChange: (plan: CodePlan | null) => void;
  /** Called after the plan is approved, so the page can send the message that carries it out. */
  onApproved: () => void;
}

/**
 * The plan Sonic wrote before editing (plan mode, "Plan first" in the "..." menu). Nothing is edited until Approve. Steps can be reworded or removed first; after approval the steps
 * tick off as their edits are applied, and "Continue" picks up at the first step not done when a turn ran out of steps.
 */
export const PlanCard: React.FC<Props> = ({ sessionId, plan, busy, onChange, onApproved }) => {
  const [editing, setEditing] = useState<{ id: string; text: string } | null>(null);
  const [message, setMessage] = useState('');
  const [working, setWorking] = useState(false);

  const act = async (action: 'approve' | 'cancel' | 'edit_step' | 'remove_step', stepId?: string, text?: string): Promise<boolean> => {
    setMessage('');
    setWorking(true);
    try {
      const result = await api.actOnCodePlan(sessionId, action, stepId, text);
      onChange(result.plan);
      setEditing(null);
      return true;
    } catch (err) {
      setMessage(err instanceof Error ? err.message : 'Failed to change the plan.');
      return false;
    } finally {
      setWorking(false);
    }
  };

  const approve = async () => {
    if (await act('approve')) onApproved();
  };

  const remaining = plan.steps.filter(step => step.status !== 'done').length;
  const stateLabel = plan.state === 'proposed' ? 'Waiting for your OK' : plan.state === 'approved' ? `Approved, ${remaining} step${remaining === 1 ? '' : 's'} to go` : 'Finished';

  return (
    <section className={`plan-card plan-card-${plan.state}`} aria-label="Plan">
      <header className="plan-card-head">
        <span className="plan-card-title">Plan</span>
        <span className="plan-card-state">{stateLabel}</span>
      </header>
      <p className="plan-card-goal">{plan.goal}</p>

      <ol className="plan-card-steps">
        {plan.steps.map(step => (
          <li key={step.id} className={step.status === 'done' ? 'plan-step plan-step-done' : 'plan-step'}>
            {editing?.id === step.id ? (
              <form className="plan-step-edit" onSubmit={event => { event.preventDefault(); void act('edit_step', step.id, editing.text); }}>
                <input autoFocus value={editing.text} maxLength={320} onChange={event => setEditing({ id: step.id, text: event.target.value })} aria-label="Edit this step" />
                <button type="submit" className="plan-link" disabled={working}>Save</button>
                <button type="button" className="plan-link" onClick={() => setEditing(null)}>Cancel</button>
              </form>
            ) : (
              <>
                <span className="plan-step-mark" aria-hidden="true">{step.status === 'done' ? '✓' : '○'}</span>
                <span className="plan-step-text">
                  {step.text}
                  {step.files.length > 0 && <span className="plan-step-files">{step.files.join(', ')}</span>}
                </span>
                {step.status !== 'done' && plan.state !== 'done' && (
                  <span className="plan-step-actions">
                    <button type="button" className="plan-link" onClick={() => setEditing({ id: step.id, text: step.text })}>Edit</button>
                    {plan.steps.length > 1 && <button type="button" className="plan-link" disabled={working} onClick={() => void act('remove_step', step.id)}>Remove</button>}
                  </span>
                )}
              </>
            )}
          </li>
        ))}
      </ol>

      <details className="plan-card-more">
        <summary>Files{plan.tests.length ? ', tests' : ''}{plan.unknowns.length ? ' and what is unknown' : ''}</summary>
        <ul className="plan-card-list">
          {plan.files.map(file => <li key={file.path}><code>{file.path}</code>{file.new ? ' (new)' : ''}{file.why ? `: ${file.why}` : ''}</li>)}
        </ul>
        {plan.tests.length > 0 && <><div className="plan-card-sub">Tests worth running</div><ul className="plan-card-list">{plan.tests.map(t => <li key={t}>{t}</li>)}</ul></>}
        {plan.unknowns.length > 0 && <><div className="plan-card-sub">Not sure about</div><ul className="plan-card-list">{plan.unknowns.map(u => <li key={u}>{u}</li>)}</ul></>}
      </details>

      {plan.state === 'proposed' && (
        <div className="plan-card-actions">
          <button type="button" className="plan-approve" disabled={working || busy} onClick={() => void approve()}>Approve and start</button>
          <button type="button" className="plan-link" disabled={working} onClick={() => void act('cancel')}>Cancel</button>
          <span className="plan-card-hint">Or tell Sonic what to change and it will revise the plan. Nothing is edited until you approve.</span>
        </div>
      )}
      {plan.state === 'approved' && (
        <div className="plan-card-actions">
          <button type="button" className="plan-approve" disabled={working || busy} onClick={onApproved}>Continue</button>
          <button type="button" className="plan-link" disabled={working} onClick={() => void act('cancel')}>Drop the plan</button>
        </div>
      )}
      {plan.state === 'done' && (
        <div className="plan-card-actions">
          <button type="button" className="plan-link" onClick={() => onChange(null)}>Dismiss</button>
        </div>
      )}
      {message && <div className="plan-card-error" role="alert">{message}</div>}
    </section>
  );
};

export default PlanCard;

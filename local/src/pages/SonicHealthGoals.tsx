import React from 'react';
import type { GoalView } from '../api';
import { StatTile } from './sonicHealthParts';

const STATE_LABEL: Record<GoalView['state'], string> = {
  on_track: 'On track',
  drifting: 'Drifting',
  unproven: 'Unproven: looks fine, but the numbers cannot back it up',
  not_enough_data: 'Not enough data yet',
};

function percent(value: number | null | undefined): string {
  return value == null ? 'n/a' : `${Math.round(value * 100)}%`;
}

function approvedLine(goal: GoalView): string {
  const { approval } = goal;
  if (approval.approved) {
    const at = approval.at ? new Date(approval.at).toLocaleDateString() : '';
    return `Approved${approval.by ? ` by ${approval.by}` : ''}${at ? ` on ${at}` : ''}.`;
  }
  if (approval.stale) return 'The goal changed since it was approved, so the approval no longer counts.';
  return 'Not approved: Sonic only measures this goal, and its lever does nothing.';
}

const GoalCard: React.FC<{ goal: GoalView; busy: boolean; onApprove: (goalId: string, approved: boolean) => void }> = ({ goal, busy, onApprove }) => {
  const { numbers } = goal;
  return (
    <section className="service-card sh-card">
      <h2>{goal.title}</h2>
      <div className="sh-hint">{goal.statement}</div>
      <div className="sh-stats">
        <StatTile value={STATE_LABEL[goal.state]} label="This week" warn={goal.state === 'drifting' || goal.state === 'unproven'} />
        <StatTile value={percent(numbers.rate)} label={`${goal.rate_label} (target ${percent(goal.target_rate)})`} />
        <StatTile value={numbers.sample} label={`${goal.sample_label} (${goal.min_sample} needed)`} />
        <StatTile value={percent(numbers.previous_rate)} label="the week before" />
      </div>
      <table className="sh-table">
        <tbody>
          <tr><th>Signal</th><td>{goal.signal}</td></tr>
          {goal.measure_only ? (
            <tr><th>Lever</th><td>None. This goal is only measured, so there is nothing to approve and it changes nothing Sonic does.</td></tr>
          ) : (
            <>
              <tr><th>Lever</th><td>{goal.lever}</td></tr>
              <tr><th>Guardrail</th><td>{goal.guardrail}</td></tr>
              <tr>
                <th>Approval</th>
                <td>
                  {approvedLine(goal)}{' '}
                  <button type="button" className={`sh-btn${goal.approval.approved ? '' : ' sh-btn-primary'}`} disabled={busy} onClick={() => onApprove(goal.id, !goal.approval.approved)}>
                    {goal.approval.approved ? 'Withdraw approval' : 'Approve this goal'}
                  </button>
                </td>
              </tr>
              <tr>
                <th>Lever now</th>
                <td className={goal.guardrail_tripped ? 'sh-err' : undefined}>
                  {goal.guardrail_tripped
                    ? 'Switched itself off: it was holding back too many offers.'
                    : goal.lever_applies
                      ? `Acting. It held back ${numbers.held_back} offers this week, for ${numbers.people.stretched} of ${numbers.people.measured} people with enough evidence.`
                      : 'Not acting.'}
                </td>
              </tr>
            </>
          )}
          {(numbers.unproven ?? []).map((reason, index) => (
            <tr key={`unproven-${index}`}><th>{index === 0 ? 'Why unproven' : ''}</th><td className="sh-err">{reason}</td></tr>
          ))}
          {numbers.details.map((line, index) => (
            <tr key={index}><th>{index === 0 ? 'This week' : ''}</th><td>{line}</td></tr>
          ))}
        </tbody>
      </table>
    </section>
  );
};

export const GoalsTab: React.FC<{ goals: GoalView[] | null | undefined; busyId: string | null; onApprove: (goalId: string, approved: boolean) => void }> = ({ goals, busyId, onApprove }) => {
  if (!goals) return <div className="sh-hint">The goals could not be read just now.</div>;
  return (
    <div className="sh-stack">
      <section className="service-card sh-card">
        <h2>Goals</h2>
        <div className="sh-hint">
          A goal is a control loop, not a wish: a number Sonic already counts, one bounded thing it changes, a check on whether that helped, a guardrail, and your approval.
          Every goal is measured whether or not it is approved; only an approved goal changes what Sonic does, and changing a goal in the code withdraws its approval until you give it
          again. What is shown here is what every user can ask Sonic about, and Sonic answers from the same numbers in the first person. Nothing here holds a person's words:
          per person it keeps only the last ten outcomes of offers as picked up or passed on, which "forget everything" removes.
        </div>
      </section>
      {goals.map(goal => <GoalCard key={goal.id} goal={goal} busy={busyId === goal.id} onApprove={onApprove} />)}
    </div>
  );
};

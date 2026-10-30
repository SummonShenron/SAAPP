// What the plan card says when Continue has stopped getting anywhere (backend/utils/code_plan.py `stalled`: turns in a row that carried out the plan and finished no step).
// On 2026-10-30 about ten Continues in a row each cost a full turn after the work was already in the files, because the last step needed no edit and a step only ticks when an edit is applied.
import type { CodePlan } from '../api';

/** From this many empty turns in a row the card stops saying only "Continue" and offers "Mark done". The same figure as STALL_NOTICE_AT in code_plan.py. */
export const STALL_NOTICE_AT = 2;

export function isStalled(plan: Pick<CodePlan, 'state' | 'stalled'>): boolean {
  return plan.state === 'approved' && (plan.stalled ?? 0) >= STALL_NOTICE_AT;
}

/** The sentence above the buttons, or null when the plan is not stuck. */
export function stallNotice(plan: Pick<CodePlan, 'state' | 'stalled'>): string | null {
  if (!isStalled(plan)) return null;
  const tries = plan.stalled ?? 0;
  return `The last ${tries} tries made no progress. If the work is already in your files, mark the step done instead of continuing.`;
}

/** The step "Mark done" belongs to: the first one still open, and only while the plan is stuck. */
export function markDoneStepId(plan: Pick<CodePlan, 'state' | 'stalled' | 'steps'>): string | null {
  if (!isStalled(plan)) return null;
  return plan.steps.find(step => step.status !== 'done')?.id ?? null;
}

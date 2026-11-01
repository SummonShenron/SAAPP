/**
 * The decisions and words behind the steps card (components/CommandStepsCard.tsx), kept apart from React so they can be tested (stepsLogic.test.mjs).
 * Sonic proposes a short list of command lines on one card (backend/utils/command_steps.py); the person runs each with a click, on their own machine, through the same command jobs the Terminal uses.
 * Nothing here runs anything: it only says what a step is, what state it is in and what to tell Sonic afterwards.
 */
import type { RunnerJob } from './api';

export interface CommandStep { id: string; title: string; line: string; klass: 'read' | 'run' | 'change'; notes: string[]; }

/** What a step's job is doing. `error` is a step that could not even be started (the server said no); `idle` has not been run. */
export type StepState = 'idle' | 'running' | 'done' | 'failed' | 'cancelled' | 'error';
export interface StepRun { state: StepState; jobId?: string; exitCode?: number | null; tail?: string; message?: string; }

export const IDLE: StepRun = { state: 'idle' };

export function klassLabel(klass: CommandStep['klass']): string {
  return klass === 'read' ? 'looks only' : klass === 'run' ? 'runs a program' : 'changes things';
}

/** Whether pressing Run on this step needs the second, stronger confirmation first (the same rule as a single command card). */
export function needsConfirm(step: Pick<CommandStep, 'klass'>): boolean {
  return step.klass === 'change';
}

/** Whether any step in the list changes things (so "Run all" asks once, up front). */
export function anyChanges(steps: Pick<CommandStep, 'klass'>[]): boolean {
  return steps.some(needsConfirm);
}

/** A step's state from its job as the server reports it. A job still waiting for approval counts as running: pressing Run approves it at once. */
export function runFromJob(job: Pick<RunnerJob, 'id' | 'status' | 'exit_code' | 'timed_out' | 'output_tail' | 'error'>): StepRun {
  const base = { jobId: job.id, exitCode: job.exit_code, tail: job.output_tail || '' };
  switch (job.status) {
    case 'done': return { ...base, state: job.exit_code === 0 && !job.timed_out ? 'done' : 'failed', message: job.timed_out ? 'Timed out, so the result is unknown.' : undefined };
    case 'failed': return { ...base, state: 'error', message: job.error || 'The runner could not run it.' };
    case 'cancelled': return { ...base, state: 'cancelled' };
    case 'expired': return { ...base, state: 'error', message: job.error || 'It was not run in time.' };
    default: return { ...base, state: 'running' };
  }
}

export function isFinished(run: StepRun | undefined): boolean {
  return !!run && (run.state === 'done' || run.state === 'failed' || run.state === 'cancelled' || run.state === 'error');
}

/** The next step "Run all" should start: the first one not yet run, but only while every step before it went well. null when something failed, something is running, or all are done. */
export function nextToRun(steps: CommandStep[], runs: Record<string, StepRun | undefined>): CommandStep | null {
  for (const step of steps) {
    const run = runs[step.id] ?? IDLE;
    if (run.state === 'idle') return step;
    if (run.state !== 'done') return null;
  }
  return null;
}

/** Whether a step may be run by hand now: it has not run, and no other step is running (the runner does one job at a time). */
export function canRun(step: CommandStep, runs: Record<string, StepRun | undefined>): boolean {
  if (Object.values(runs).some(run => run?.state === 'running')) return false;
  return (runs[step.id] ?? IDLE).state === 'idle' || isFinished(runs[step.id]) && (runs[step.id]?.state !== 'done');
}

const TAIL_CHARS = 400;

function lastLines(text: string | undefined): string {
  const flat = (text ?? '').trim();
  return flat.length <= TAIL_CHARS ? flat : '…' + flat.slice(-TAIL_CHARS);
}

/** What to put in the person's message box so Sonic knows how the steps went: one line per step, and the end of the output for a step that failed. The person can edit it before sending. */
export function summaryText(steps: CommandStep[], runs: Record<string, StepRun | undefined>): string {
  const lines = ['How the steps went:'];
  steps.forEach((step, index) => {
    const run = runs[step.id] ?? IDLE;
    const label = `${index + 1}. ${step.title} (${step.line})`;
    switch (run.state) {
      case 'done': lines.push(`${label}: done`); break;
      case 'failed': lines.push(`${label}: failed (exit code ${run.exitCode ?? '?'})${run.message ? `, ${run.message}` : ''}${run.tail ? `\n${lastLines(run.tail)}` : ''}`); break;
      case 'cancelled': lines.push(`${label}: I stopped it`); break;
      case 'error': lines.push(`${label}: did not run (${run.message || 'the runner could not run it'})`); break;
      case 'running': lines.push(`${label}: still running`); break;
      default: lines.push(`${label}: not run`);
    }
  });
  return lines.join('\n');
}

/** Whether there is anything to tell Sonic yet: at least one step has an outcome. */
export function hasNews(steps: CommandStep[], runs: Record<string, StepRun | undefined>): boolean {
  return steps.some(step => isFinished(runs[step.id]));
}

/** The one sentence "Run all" asks before it starts: how many steps, and that those that change things get the stronger confirmation on the person's own computer too. */
export function runAllQuestion(steps: CommandStep[]): string {
  const changing = steps.filter(needsConfirm).length;
  return changing
    ? `Run all ${steps.length} steps in order? ${changing} of them change things (files, git or the network). Your computer asks again in its own window for each of those, and it stops at the first step that fails.`
    : `Run all ${steps.length} steps in order? It stops at the first step that fails.`;
}

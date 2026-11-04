import { useEffect, useRef, useState } from 'react';
import { api } from '../api';
import { isFinal } from '../runnerLogic';
import { IDLE, anyChanges, canRun, hasNews, isFinished, klassLabel, needsConfirm, runAllQuestion, runFromJob, summaryText, type CommandStep, type StepRun } from '../stepsLogic';
import './__styles__/CommandStepsCard.css';

interface Props {
  /** The steps Sonic proposed, already checked by the server with the runner's own rules. */
  steps: CommandStep[];
  /** The runner and project they will run in: the person's own computer. */
  runner: { id: string; name?: string | null; root_name?: string | null; project_id?: string | null };
  /** Puts text in the person's message box (never sends it): how the steps went, for Sonic. */
  onTell: (text: string) => void;
}

const POLL_MS = 1500;
const sleep = (ms: number) => new Promise<void>(resolve => window.setTimeout(resolve, ms));

/**
 * A short list of steps Sonic proposes, each with a Run button, the way a README's commands are run (backend/utils/command_steps.py). Pressing Run makes the same command job the Terminal makes and
 * approves it; a step that changes things asks for the stronger confirmation first, and the person's own computer asks again in its own window. Nothing runs without a click, "Run all" asks once and
 * stops at the first failure, and "Tell Sonic how it went" only fills the message box. Everything shown came from the person's own computer and is plain text.
 */
export default function CommandStepsCard({ steps, runner, onTell }: Props) {
  const [runs, setRuns] = useState<Record<string, StepRun>>({});
  const [confirming, setConfirming] = useState<string | null>(null);     // a step id, or 'all'
  const [allRunning, setAllRunning] = useState(false);
  const [message, setMessage] = useState('');
  const alive = useRef(true);
  const runsRef = useRef<Record<string, StepRun>>({});
  const stopRef = useRef(false);
  const current = useRef<string | null>(null);                           // the job that is running now, so Stop can cancel it
  useEffect(() => () => { alive.current = false; }, []);

  const setRun = (id: string, run: StepRun) => {
    runsRef.current = { ...runsRef.current, [id]: run };
    if (alive.current) setRuns(runsRef.current);
  };

  const projectId = runner.project_id || '';

  /** Runs one step to its end and returns how it went. Never throws: a problem becomes an `error` state with the reason. */
  const runStep = async (step: CommandStep, confirmed: boolean): Promise<StepRun> => {
    setMessage('');
    setConfirming(null);
    setRun(step.id, { state: 'running' });
    try {
      const created = await api.createRunnerCommand(runner.id, projectId, step.line);
      let job = created.status === 'awaiting_approval' ? await api.approveRunnerJob(created.id, confirmed) : created;
      current.current = job.id;
      setRun(step.id, runFromJob(job));
      while (!isFinal(job.status)) {
        await sleep(POLL_MS);
        if (!alive.current) return runFromJob(job);
        job = await api.getRunnerJob(job.id);
        setRun(step.id, runFromJob(job));
      }
      return runFromJob(job);
    } catch (problem) {
      const failed: StepRun = { state: 'error', message: problem instanceof Error ? problem.message : 'That did not work.' };
      setRun(step.id, failed);
      return failed;
    } finally {
      current.current = null;
    }
  };

  const runAll = async () => {
    stopRef.current = false;
    setConfirming(null);
    setAllRunning(true);
    for (const step of steps) {
      if ((runsRef.current[step.id] ?? IDLE).state === 'done') continue;
      if (stopRef.current) break;
      const result = await runStep(step, true);            // the person confirmed the whole list up front
      if (result.state !== 'done') break;
    }
    if (alive.current) setAllRunning(false);
  };

  const stop = async () => {
    stopRef.current = true;
    const job = current.current;
    if (!job) return;
    try {
      await api.cancelRunnerJob(job);
    } catch (problem) {
      setMessage(problem instanceof Error ? problem.message : 'Could not stop it.');
    }
  };

  const where = [runner.name, runner.root_name].filter(Boolean).join(' · ');
  const busy = Object.values(runs).some(run => run.state === 'running');
  const waiting = steps.filter(step => (runs[step.id] ?? IDLE).state === 'idle').length;
  return (
    <div className="csc-card" data-help="card.steps">
      <div className="csc-header">
        <span className="csc-title">{steps.length} step{steps.length === 1 ? '' : 's'} to run on your machine{where ? <span className="csc-where"> ({where})</span> : null}</span>
      </div>
      {!projectId && <div className="csc-error" role="alert">That computer has no project chosen, so these cannot run from here. Add the project in Settings, Run checks on your machine.</div>}
      <ol className="csc-steps">
        {steps.map((step, index) => {
          const run = runs[step.id] ?? IDLE;
          const asking = confirming === step.id;
          return (
            <li key={step.id} className="csc-step" data-state={run.state}>
              <div className="csc-step-head">
                <span className="csc-num" aria-hidden="true">{index + 1}</span>
                <span className="csc-step-title">{step.title}</span>
                <span className={`csc-class csc-class-${step.klass}`}>{klassLabel(step.klass)}</span>
              </div>
              <pre className="csc-line" aria-label={`The command for step ${index + 1}`}>{step.line}</pre>
              {step.notes.length > 0 && <div className="csc-notes">{step.notes.join(' · ')}</div>}
              {asking && (
                <div className="csc-strong" role="alert">
                  <strong>This step changes things.</strong> Read the line once more. After you confirm, your computer shows its own window asking again, and nothing runs unless you say yes there too.
                </div>
              )}
              <div className="csc-actions">
                {run.state === 'running' && <span className="csc-status" role="status">Running…</span>}
                {run.state === 'done' && <span className="csc-status csc-ok" role="status">Done</span>}
                {run.state === 'failed' && <span className="csc-status csc-bad" role="status">Failed (exit code {run.exitCode ?? '?'}){run.message ? `: ${run.message}` : ''}</span>}
                {run.state === 'cancelled' && <span className="csc-status" role="status">Stopped</span>}
                {run.state === 'error' && <span className="csc-status csc-bad" role="status">Did not run: {run.message}</span>}
                {canRun(step, runs) && projectId && !asking && (
                  <button type="button" className="csc-btn csc-btn-primary" disabled={busy || allRunning}
                    onClick={() => (needsConfirm(step) ? setConfirming(step.id) : void runStep(step, false))}>{isFinished(run) ? 'Run again' : 'Run'}</button>
                )}
                {asking && <button type="button" className="csc-btn csc-btn-danger" onClick={() => void runStep(step, true)}>Yes, run this step</button>}
                {asking && <button type="button" className="csc-btn" onClick={() => setConfirming(null)}>Cancel</button>}
              </div>
              {run.tail && (run.state === 'failed' || run.state === 'error') && <pre className="csc-output">{run.tail}</pre>}
              {run.tail && run.state === 'done' && <details className="csc-more"><summary>Output</summary><pre className="csc-output">{run.tail}</pre></details>}
            </li>
          );
        })}
      </ol>
      {confirming === 'all' && <div className="csc-strong" role="alert">{runAllQuestion(steps)}</div>}
      <div className="csc-footer">
        {projectId && waiting > 1 && !allRunning && confirming !== 'all' && <button type="button" className="csc-btn" disabled={busy} onClick={() => (anyChanges(steps) ? setConfirming('all') : void runAll())}>Run all in order</button>}
        {confirming === 'all' && <button type="button" className="csc-btn csc-btn-danger" onClick={() => void runAll()}>Yes, run them all</button>}
        {confirming === 'all' && <button type="button" className="csc-btn" onClick={() => setConfirming(null)}>Cancel</button>}
        {(busy || allRunning) && <button type="button" className="csc-btn" onClick={() => void stop()}>Stop</button>}
        {hasNews(steps, runs) && !busy && <button type="button" className="csc-btn" onClick={() => onTell(summaryText(steps, runs))}>Tell Sonic how it went</button>}
      </div>
      {message && <div className="csc-error" role="alert">{message}</div>}
    </div>
  );
}

import { useCallback, useEffect, useRef, useState } from 'react';
import { api, type RunnerJob } from '../api';
import { classSentence, filesLine, headline, isCommand, isFinal, needsSecondStep, verdict } from '../runnerLogic';
import './__styles__/RunnerCheckCard.css';

interface RunnerCheckCardProps {
  /** The check as the server first described it (the card for it is shown while Sonic waits). */
  job: RunnerJob;
  /** Which runner it will go to, so the person can see it is the one in the project they mean. */
  runner?: { name?: string | null; root_name?: string | null };
}

const POLL_MS = 1500;

/**
 * A check Sonic asks to run on the person's OWN machine (backend/utils/runner_jobs.py). It shows the exact command and the files, and nothing runs until Run is pressed. After that the card keeps
 * asking the server how it is going until the check has an outcome, then shows the result and the end of the output. The output is text the person's own command printed; it is shown as plain text.
 */
export default function RunnerCheckCard({ job: first, runner }: RunnerCheckCardProps) {
  const [job, setJob] = useState<RunnerJob>(first);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [confirming, setConfirming] = useState(false);          // a command that changes things: the first Run opens the stronger question
  const alive = useRef(true);
  useEffect(() => () => { alive.current = false; }, []);

  const refresh = useCallback(async () => {
    try {
      const latest = await api.getRunnerJob(first.id);
      if (alive.current) setJob(latest);
    } catch (problem) {
      if (alive.current) setError(problem instanceof Error ? problem.message : 'Could not check on it.');
    }
  }, [first.id]);

  // Until there is an outcome, ask the server how it is going. A card whose check was already finished is never asked about.
  useEffect(() => {
    if (isFinal(job.status)) return undefined;
    const timer = window.setInterval(() => { void refresh(); }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [job.status, refresh]);

  const act = async (action: 'run' | 'cancel', confirmed = false) => {
    setBusy(true);
    setError('');
    try {
      const updated = action === 'run' ? await api.approveRunnerJob(job.id, confirmed) : await api.cancelRunnerJob(job.id);
      if (alive.current) setJob(updated);
    } catch (problem) {
      if (alive.current) setError(problem instanceof Error ? problem.message : 'That did not work.');
    } finally {
      if (alive.current) setBusy(false);
    }
  };

  const kind = verdict(job);
  const command = isCommand(job);
  const second = needsSecondStep(job);
  const files = filesLine(job);
  const where = [runner?.name, runner?.root_name].filter(Boolean).join(' · ');
  return (
    <div className="rcc-card" data-verdict={kind}>
      <div className="rcc-header">
        {command
          ? <span className="rcc-title">Run a <strong>command</strong> on your machine{where ? <span className="rcc-where"> ({where})</span> : null}</span>
          : <span className="rcc-title">Run <strong>{job.check}</strong> on your machine{where ? <span className="rcc-where"> ({where})</span> : null}</span>}
        {command && job.command_class && <span className={`rcc-class rcc-class-${job.command_class}`}>{job.command_class === 'read' ? 'looks only' : job.command_class === 'run' ? 'runs a program' : 'changes things'}</span>}
      </div>
      <pre className="rcc-command" aria-label="The command that will run">{job.command_shown}</pre>
      {command && classSentence(job.command_class) && <div className="rcc-note">{classSentence(job.command_class)}</div>}
      {command && (job.command_notes ?? []).length > 0 && <ul className="rcc-notes">{(job.command_notes ?? []).map(note => <li key={note}>{note}</li>)}</ul>}
      {files && <div className="rcc-files"><span className="rcc-label">On these files:</span> {files}</div>}
      {kind === 'waiting' && <div className="rcc-note">This runs on your own computer, with your own privileges. Read the command before you press Run.</div>}
      {kind === 'waiting' && second && confirming && (
        <div className="rcc-strong" role="alert">
          <strong>This command changes things.</strong> Read the line above once more. After you confirm, your computer shows its own window asking again, and nothing runs unless you say yes there too.
        </div>
      )}
      <div className="rcc-status" role="status">{headline(job)}</div>
      {(kind === 'waiting' || kind === 'running') && (
        <div className="rcc-actions">
          {kind === 'waiting' && !second && <button type="button" className="rcc-btn rcc-btn-primary" disabled={busy} onClick={() => void act('run')}>Run</button>}
          {kind === 'waiting' && second && !confirming && <button type="button" className="rcc-btn rcc-btn-primary" disabled={busy} onClick={() => setConfirming(true)}>Review and run...</button>}
          {kind === 'waiting' && second && confirming && <button type="button" className="rcc-btn rcc-btn-danger" disabled={busy} onClick={() => void act('run', true)}>Yes, run this command</button>}
          <button type="button" className="rcc-btn" disabled={busy} onClick={() => void act('cancel')}>{kind === 'waiting' ? 'Cancel' : 'Stop'}</button>
        </div>
      )}
      {job.output_tail && (
        <details className="rcc-output" open={kind === 'failed' || kind === 'error'}>
          <summary>{job.truncated ? 'Output (the end of it)' : 'Output'}</summary>
          <pre>{job.output_tail}</pre>
        </details>
      )}
      {error && <div className="rcc-error" role="alert">{error}</div>}
    </div>
  );
}

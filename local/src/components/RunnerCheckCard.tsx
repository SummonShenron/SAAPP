import { useCallback, useEffect, useRef, useState } from 'react';
import { api, type RunnerJob } from '../api';
import { filesLine, headline, isFinal, verdict } from '../runnerLogic';
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

  const act = async (action: 'run' | 'cancel') => {
    setBusy(true);
    setError('');
    try {
      const updated = action === 'run' ? await api.approveRunnerJob(job.id) : await api.cancelRunnerJob(job.id);
      if (alive.current) setJob(updated);
    } catch (problem) {
      if (alive.current) setError(problem instanceof Error ? problem.message : 'That did not work.');
    } finally {
      if (alive.current) setBusy(false);
    }
  };

  const kind = verdict(job);
  const files = filesLine(job);
  const where = [runner?.name, runner?.root_name].filter(Boolean).join(' · ');
  return (
    <div className="rcc-card" data-verdict={kind}>
      <div className="rcc-header">
        <span className="rcc-title">Run <strong>{job.check}</strong> on your machine{where ? <span className="rcc-where"> ({where})</span> : null}</span>
      </div>
      <pre className="rcc-command" aria-label="The command that will run">{job.command_shown}</pre>
      {files && <div className="rcc-files"><span className="rcc-label">On these files:</span> {files}</div>}
      {kind === 'waiting' && <div className="rcc-note">This runs on your own computer, with your own privileges. Read the command before you press Run.</div>}
      <div className="rcc-status" role="status">{headline(job)}</div>
      {(kind === 'waiting' || kind === 'running') && (
        <div className="rcc-actions">
          {kind === 'waiting' && <button type="button" className="rcc-btn rcc-btn-primary" disabled={busy} onClick={() => void act('run')}>Run</button>}
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

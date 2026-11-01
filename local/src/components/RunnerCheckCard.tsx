import { useCallback, useEffect, useRef, useState } from 'react';
import { api, type RunnerJob } from '../api';
import { SESSION_IDLE_MINUTES, canStopServer, classSentence, deviceLabel, filesLine, headline, isCommand, isFinal, isLook, isServe, lookLines, lookNote, needsSecondStep, serveNote, serverExposed, verdict } from '../runnerLogic';
import './__styles__/RunnerCheckCard.css';

interface RunnerCheckCardProps {
  /** The check as the server first described it (the card for it is shown while Sonic waits). */
  job: RunnerJob;
  /** Which runner it will go to, so the person can see it is the one in the project they mean. */
  runner?: { name?: string | null; root_name?: string | null };
}

const POLL_MS = 1500;

/**
 * A check, a command, a server or a look that Sonic asks to run on the person's OWN machine (backend/utils/runner_jobs.py). It shows the exact command (or address) and nothing runs until Run is pressed
 * (a look inside a preview session the person already approved starts by itself, and says so). After that the card keeps asking the server how it is going until there is an outcome, then shows the result:
 * the end of the output, or for a look the screenshot and what the runner read. All of that text came from the person's own computer and is shown as plain text.
 */
export default function RunnerCheckCard({ job: first, runner }: RunnerCheckCardProps) {
  const [job, setJob] = useState<RunnerJob>(first);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [confirming, setConfirming] = useState(false);          // a command that changes things: the first Run opens the stronger question
  const [picture, setPicture] = useState('');                    // a look's screenshot, as a local address made from the picture the server holds
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

  // A look's picture: fetched once the look is done (the endpoint needs the login, so it comes as a blob), and let go of when the card goes.
  const wantsPicture = isLook(job) && job.status === 'done' && job.has_screenshot === true;
  useEffect(() => {
    if (!wantsPicture) return undefined;
    let made = '';
    let cancelled = false;
    api.getLookPicture(job.id).then(address => {
      if (cancelled) { URL.revokeObjectURL(address); return; }
      made = address;
      setPicture(address);
    }).catch(() => { /* the facts below still say what the page looked like */ });
    return () => { cancelled = true; if (made) URL.revokeObjectURL(made); };
  }, [wantsPicture, job.id]);

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

  const stopServer = async () => {
    if (!job.runner_id) return;
    setBusy(true);
    setError('');
    try {
      await api.stopRunnerServer(job.runner_id, job.project_id);
      window.setTimeout(() => { void refresh(); }, 1500);
    } catch (problem) {
      if (alive.current) setError(problem instanceof Error ? problem.message : 'The server could not be stopped.');
    } finally {
      if (alive.current) setBusy(false);
    }
  };

  const kind = verdict(job);
  const command = isCommand(job);
  const serve = isServe(job);
  const look = isLook(job);
  const second = needsSecondStep(job);
  const files = filesLine(job);
  const where = [runner?.name, runner?.root_name].filter(Boolean).join(' · ');
  const facts = look ? lookLines(job) : [];
  return (
    <div className="rcc-card" data-verdict={kind}>
      <div className="rcc-header">
        {serve
          ? <span className="rcc-title">Start the <strong>{job.check}</strong> server on your machine{where ? <span className="rcc-where"> ({where})</span> : null}</span>
          : look
            ? <span className="rcc-title">Look at a page on your machine <span className="rcc-where">— {deviceLabel(job.device)}</span>{where ? <span className="rcc-where"> ({where})</span> : null}</span>
            : command
              ? <span className="rcc-title">Run a <strong>command</strong> on your machine{where ? <span className="rcc-where"> ({where})</span> : null}</span>
              : <span className="rcc-title">Run <strong>{job.check}</strong> on your machine{where ? <span className="rcc-where"> ({where})</span> : null}</span>}
        {command && job.command_class && <span className={`rcc-class rcc-class-${job.command_class}`}>{job.command_class === 'read' ? 'looks only' : job.command_class === 'run' ? 'runs a program' : 'changes things'}</span>}
      </div>
      <pre className="rcc-command" aria-label={look ? 'The address that will be opened' : 'The command that will run'}>{job.command_shown}</pre>
      {command && classSentence(job.command_class) && <div className="rcc-note">{classSentence(job.command_class)}</div>}
      {command && (job.command_notes ?? []).length > 0 && <ul className="rcc-notes">{(job.command_notes ?? []).map(note => <li key={note}>{note}</li>)}</ul>}
      {files && <div className="rcc-files"><span className="rcc-label">On these files:</span> {files}</div>}
      {kind === 'waiting' && serve && <div className="rcc-note">{serveNote(job)}</div>}
      {(kind === 'waiting' || kind === 'running') && look && <div className="rcc-note">{lookNote(job)}</div>}
      {kind === 'waiting' && !serve && !look && <div className="rcc-note">This runs on your own computer, with your own privileges. Read the command before you press Run.</div>}
      {kind === 'waiting' && second && confirming && (
        <div className="rcc-strong" role="alert">
          <strong>This command changes things.</strong> Read the line above once more. After you confirm, your computer shows its own window asking again, and nothing runs unless you say yes there too.
        </div>
      )}
      <div className="rcc-status" role="status">{headline(job)}</div>
      {serve && kind === 'passed' && serverExposed(job) && (
        <div className="rcc-strong" role="alert">This server says it can be reached from other computers on your network, not only yours. That is how your project is set up, not something Sonic changed.</div>
      )}
      {serve && kind === 'passed' && !canStopServer(job) && job.session_stopped && <div className="rcc-note">Stopped.</div>}
      {serve && kind === 'passed' && canStopServer(job) && (
        <div className="rcc-note">Sonic may now look at its pages ({job.session_looks_left ?? 10} looks left without asking). It stops by itself after {SESSION_IDLE_MINUTES} minutes unused.</div>
      )}
      {(kind === 'waiting' || kind === 'running') && (
        <div className="rcc-actions">
          {kind === 'waiting' && !second && <button type="button" className="rcc-btn rcc-btn-primary" disabled={busy} onClick={() => void act('run')}>Run</button>}
          {kind === 'waiting' && second && !confirming && <button type="button" className="rcc-btn rcc-btn-primary" disabled={busy} onClick={() => setConfirming(true)}>Review and run...</button>}
          {kind === 'waiting' && second && confirming && <button type="button" className="rcc-btn rcc-btn-danger" disabled={busy} onClick={() => void act('run', true)}>Yes, run this command</button>}
          <button type="button" className="rcc-btn" disabled={busy} onClick={() => void act('cancel')}>{kind === 'waiting' ? 'Cancel' : 'Stop'}</button>
        </div>
      )}
      {canStopServer(job) && (
        <div className="rcc-actions"><button type="button" className="rcc-btn" disabled={busy} onClick={() => void stopServer()}>Stop the server</button></div>
      )}
      {look && picture && (
        <figure className={`rcc-shot rcc-shot-${job.device || 'desktop'}`}>
          <img src={picture} alt={`A screenshot of ${job.url ?? 'the page'} at ${deviceLabel(job.device)}`} />
        </figure>
      )}
      {facts.length > 0 && <ul className="rcc-facts">{facts.map(line => <li key={line}>{line}</li>)}</ul>}
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

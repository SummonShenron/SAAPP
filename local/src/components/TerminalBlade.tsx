import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import type { FormEvent, KeyboardEvent } from 'react';
import { api, type CommandJobRow } from '../api';
import { useTerminalJobs } from '../hooks/useTerminalJobs';
import Icon from './icons';
import {
  DEFAULT_TAIL_LINES,
  canRun,
  canStop,
  commandHistory,
  formatDuration,
  rowOutcome,
  scrollbackOrder,
  sendToSonicBlock,
  stepHistory,
  stoppableJob,
  readStoredPick,
  trimOutputTail,
  writeStoredPick,
  type TerminalChoice,
} from './terminalLogic';
import './__styles__/TerminalBlade.css';

interface Props {
  runnerId: string;
  projectId: string;
  /** The project's name, for the header. */
  projectName: string;
  onClose: () => void;
  /** Puts text into the chat message box (focused, not sent). Optional: without it no Send to Sonic button is shown. */
  onSendToChat?: (text: string) => void;
  /** Projects to pick from, passed only when the open folder matched no project. Optional: without it the header shows the project name. */
  choices?: TerminalChoice[];
  /** Called when the person picks a project (and once on open, with the remembered one). The parent switches runnerId and projectId. */
  onPick?: (choice: TerminalChoice) => void;
}

/**
 * The terminal: a blade that slides over the right of the page (opened from the terminal button in the top bar) showing the commands run on the person's own computer for this project, oldest at
 * the top, each with its output, and a prompt at the bottom to type one. A typed command is the same job Sonic's commands are (backend/utils/runner_jobs.py): the server classifies it with the
 * runner's own rules, a refused line says why, and one that changes things still asks twice (here, then in the runner's own window). It runs one command at a time and shows the output when the
 * command finishes; it is not a shell, so there is no `cd` or running program to type into. Up and down walk the earlier commands; Ctrl+C stops the one running; Escape closes the blade.
 */
export const TerminalBlade: React.FC<Props> = ({ runnerId, projectId, projectName, onClose, onSendToChat, choices, onPick }) => {
  const { jobs, outputs, loading, error, setError, refresh } = useTerminalJobs(runnerId, projectId, true);
  const [line, setLine] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [historyAt, setHistoryAt] = useState(-1);
  const draft = useRef('');
  const inputRef = useRef<HTMLInputElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const stuckToEnd = useRef(true);

  const rows = useMemo(() => scrollbackOrder(jobs), [jobs]);
  const history = useMemo(() => commandHistory(jobs), [jobs]);
  const running = stoppableJob(jobs);

  useEffect(() => { inputRef.current?.focus(); }, []);

  // Project picker. Storage may be missing or throw (privacy settings); then the choice is simply not remembered.
  const storage = (() => { try { return window.localStorage; } catch { return null; } })();
  const pickKey = (choice: TerminalChoice) => `${choice.runnerId}|${choice.projectId}`;
  const currentKey = `${runnerId}|${projectId}`;
  useEffect(() => {
    if (!choices || !onPick) return;
    const remembered = choices.find((choice) => pickKey(choice) === readStoredPick(storage));
    if (remembered && pickKey(remembered) !== currentKey) onPick(remembered);
    // Only on open: the remembered choice is applied once, not on every change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const onPickChange = (value: string) => {
    const choice = choices?.find((c) => pickKey(c) === value);
    if (!choice || !onPick) return;
    writeStoredPick(storage, value);
    onPick(choice);
  };

  // Escape closes the blade from anywhere on the page while it is open.
  useEffect(() => {
    const onKey = (event: globalThis.KeyboardEvent) => { if (event.key === 'Escape') onClose(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  // Stay at the bottom while new lines arrive, unless the person scrolled up to read.
  useLayoutEffect(() => {
    const box = scrollRef.current;
    if (box && stuckToEnd.current) box.scrollTop = box.scrollHeight;
  }, [rows, outputs]);
  const onScroll = () => {
    const box = scrollRef.current;
    if (box) stuckToEnd.current = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
  };

  const stop = async (id: string) => {
    try {
      await api.cancelRunnerJob(id);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to stop that command.');
    }
  };

  const run = async (job: CommandJobRow) => {
    if (job.needs_confirm && !window.confirm(`This command changes things on your computer:\n\n${job.command_shown}\n\nRun it? Your computer will ask once more.`)) return;
    try {
      await api.approveRunnerJob(job.id, Boolean(job.needs_confirm));
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to run that command.');
    }
  };

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const text = line.trim();
    if (!text || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      // The person typed it, so a command that only looks or runs a program starts at once; one that changes things waits for its Run button and the stronger question.
      const job = await api.createRunnerCommand(runnerId, projectId, text);
      setLine('');
      setHistoryAt(-1);
      stuckToEnd.current = true;
      if (job.status === 'awaiting_approval' && !job.needs_confirm) await api.approveRunnerJob(job.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to run that command.');
    } finally {
      setSubmitting(false);
      await refresh();
      inputRef.current?.focus();
    }
  };

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'ArrowUp' || event.key === 'ArrowDown') {
      event.preventDefault();
      if (historyAt === -1) draft.current = line;
      const next = stepHistory(history.length, historyAt, event.key === 'ArrowUp' ? 'up' : 'down');
      setHistoryAt(next);
      setLine(next === -1 ? draft.current : history[next]);
    } else if (event.ctrlKey && event.key.toLowerCase() === 'c' && running && !(inputRef.current && inputRef.current.selectionStart !== inputRef.current.selectionEnd)) {
      event.preventDefault();
      void stop(running.id);
    } else if (event.ctrlKey && event.key.toLowerCase() === 'l') {
      event.preventDefault();
      stuckToEnd.current = true;
    }
  };

  return (
    <aside className="terminal-blade" role="dialog" aria-label="Terminal">
      <header className="tb-head">
        <span className="tb-title"><Icon name="terminal" size={15} /> Terminal</span>
        {choices && choices.length > 0 && onPick ? (
          <select className="tb-where tb-pick" value={currentKey} onChange={(e) => onPickChange(e.target.value)} aria-label="Project the commands run in" title="The project on your computer the commands run in">
            {choices.map((choice) => <option key={pickKey(choice)} value={pickKey(choice)}>{choice.label}</option>)}
          </select>
        ) : (
          <span className="tb-where" title="The project on your computer the commands run in">{projectName}</span>
        )}
        <button type="button" className="tb-close" onClick={onClose} aria-label="Close the terminal" title="Close (Esc)"><Icon name="close" size={16} /></button>
      </header>

      <div className="tb-scroll" ref={scrollRef} onScroll={onScroll} onClick={() => { if (!window.getSelection()?.toString()) inputRef.current?.focus(); }}>
        {loading && <p className="tb-note">Loading...</p>}
        {!loading && rows.length === 0 && (
          <p className="tb-note">Nothing has run here in the last 7 days. Type a command below, such as <code>git status</code> or <code>pytest -q</code>; it runs on your computer, in {projectName}.</p>
        )}
        {rows.map((job) => {
          const outcome = rowOutcome(job);
          const stored = outputs[job.id];
          const output = trimOutputTail(stored?.output_tail ?? '', DEFAULT_TAIL_LINES);
          return (
            <section key={job.id} className={`tb-entry tb-${outcome.dot}`}>
              <div className="tb-cmd">
                <span className="tb-prompt" aria-hidden="true">$</span>
                <code>{job.command_shown}</code>
                <span className="tb-status">{canStop(job) && <span className="tb-spin" aria-hidden="true" />}{outcome.label}{job.duration != null ? ` · ${formatDuration(job.duration)}` : ''}</span>
              </div>
              {canRun(job) && (
                <div className="tb-ask">
                  <span>{job.needs_confirm ? 'This changes things. Run it?' : 'Run it?'}</span>
                  <button type="button" onClick={() => void run(job)}>Run</button>
                  <button type="button" onClick={() => void stop(job.id)}>Cancel</button>
                </div>
              )}
              {canStop(job) && !canRun(job) && <div className="tb-ask"><button type="button" onClick={() => void stop(job.id)}>Stop (Ctrl+C)</button></div>}
              {output ? <pre className="tb-out">{output}</pre> : stored && !canStop(job) && !canRun(job) && job.status === 'done' ? <p className="tb-quiet">No output.</p> : null}
              {job.truncated && <p className="tb-quiet">Only the end of the output is kept.</p>}
              {onSendToChat && !canStop(job) && (
                <div className="tb-ask">
                  <button type="button" className="tb-send" onClick={() => onSendToChat(sendToSonicBlock(job.command_shown, job.exit_code, stored?.output_tail ?? ''))}>Send to Sonic</button>
                </div>
              )}
            </section>
          );
        })}
      </div>

      {error && <p className="tb-error" role="alert">{error}</p>}
      <form className="tb-prompt-row" onSubmit={submit}>
        <span className="tb-prompt" aria-hidden="true">$</span>
        <input
          ref={inputRef}
          value={line}
          onChange={(e) => { setLine(e.target.value); setHistoryAt(-1); }}
          onKeyDown={onKeyDown}
          placeholder={running ? 'Running... Ctrl+C stops it' : 'Type a command and press Enter'}
          aria-label="Command to run on your computer"
          maxLength={2000}
          spellCheck={false}
          autoComplete="off"
          autoCapitalize="off"
          disabled={submitting}
        />
      </form>
      <footer className="tb-foot">One command at a time, on your computer. Output shows when it finishes. Not a shell: no cd, no programs that wait for typing.</footer>
    </aside>
  );
};

export default TerminalBlade;

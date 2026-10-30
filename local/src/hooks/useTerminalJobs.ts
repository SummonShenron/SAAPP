import { useCallback, useEffect, useRef, useState } from 'react';
import { api, type CommandJobRow, type RunnerJob } from '../api';
import { isActiveStatus, pollDelay } from '../components/terminalLogic';

/** How many of the newest commands get their output fetched for the scrollback (one request each). */
const OUTPUT_FOR_NEWEST = 12;

export interface TerminalJobs {
  /** The person's recent commands on this runner and project, newest first. */
  jobs: CommandJobRow[];
  /** The stored output of each job that has been opened, by job id. */
  outputs: Record<string, RunnerJob>;
  /** True until the first answer. */
  loading: boolean;
  error: string | null;
  setError: (message: string | null) => void;
  /** Asks the server now. */
  refresh: () => Promise<void>;
}

/**
 * What the terminal blade shows (components/TerminalBlade.tsx): the recent command jobs for one runner and project, and the output of the newest ones. Asks every few seconds while a command
 * is waiting or running and every ten otherwise, and stops when `open` is false (a closed blade makes no requests). Output is never kept anywhere but this page's memory.
 */
export function useTerminalJobs(runnerId: string, projectId: string, open: boolean): TerminalJobs {
  const [jobs, setJobs] = useState<CommandJobRow[]>([]);
  const [outputs, setOutputs] = useState<Record<string, RunnerJob>>({});
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);        // why a typed command was refused, say: kept until the next command, not wiped by the next refresh
  const alive = useRef(true);
  const outputsRef = useRef(outputs);
  outputsRef.current = outputs;

  const refresh = useCallback(async () => {
    try {
      const res = await api.listCommandJobs(runnerId, projectId);
      const rows = Array.isArray(res.jobs) ? res.jobs : [];
      if (!alive.current) return;
      setJobs(rows);
      setLoadError(null);
      // The output of the newest few: the ones not fetched yet, and the ones whose stored copy is from the middle of a run (or whose row is still active).
      const wanted = rows.slice(0, OUTPUT_FOR_NEWEST).filter((row) => {
        const stored = outputsRef.current[row.id];
        return !stored || isActiveStatus(row.status) || isActiveStatus(stored.status);
      });
      const fetched = await Promise.all(wanted.map(async (row) => {
        try { return await api.getRunnerJob(row.id); } catch { return null; }
      }));
      if (!alive.current) return;
      const fresh = fetched.filter((job): job is RunnerJob => job !== null);
      if (fresh.length) setOutputs((prev) => ({ ...prev, ...Object.fromEntries(fresh.map((job) => [job.id, job])) }));
    } catch (err) {
      if (alive.current) setLoadError(err instanceof Error ? err.message : 'Failed to load the command history.');
    } finally {
      if (alive.current) setLoading(false);
    }
  }, [runnerId, projectId]);

  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; };
  }, []);

  useEffect(() => {
    if (!open) return undefined;
    void refresh();
    return undefined;
  }, [open, refresh]);

  useEffect(() => {
    if (!open) return undefined;
    const timer = window.setTimeout(() => { void refresh(); }, pollDelay(jobs));
    return () => window.clearTimeout(timer);
  }, [open, jobs, outputs, refresh]);

  return { jobs, outputs, loading, error: actionError ?? loadError, setError: setActionError, refresh };
}

import { useEffect, useState } from 'react';
import { listRunners, type RunnerInfo } from '../api';

const POLL_MS = 30_000;

/**
 * The person's runners, asked about now and every half minute while the tab is showing, so the session column can say whether the runner on their computer is answering (it is the same list
 * Settings shows). `null` until the first answer, and always null when `enabled` is false (a guest, or the column hidden). A failed request keeps the last answer: a blip is not "disconnected".
 */
export function useRunnerPing(enabled: boolean): RunnerInfo[] | null {
  const [runners, setRunners] = useState<RunnerInfo[] | null>(null);
  useEffect(() => {
    if (!enabled) return undefined;
    let alive = true;
    const ask = async () => {
      if (document.hidden) return;
      try {
        const answer = await listRunners();
        if (alive) setRunners(answer.runners);
      } catch {
        /* keep what we had */
      }
    };
    void ask();
    const timer = window.setInterval(() => { void ask(); }, POLL_MS);
    return () => { alive = false; window.clearInterval(timer); };
  }, [enabled]);
  return enabled ? runners : null;
}

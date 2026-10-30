import { useCallback, useEffect, useRef, useState } from 'react';
import { listRunners, type RunnerInfo } from '../api';

const POLL_MS = 30_000;
const QUICK_MS = 3_000;
const QUICK_FOR_MS = 45_000;

/**
 * The person's runners, asked about now and every half minute while the tab is showing, so the session column can say whether the runner on their computer is answering (it is the same list
 * Settings shows). `runners` is null until the first answer, and always null when `enabled` is false (a guest, or the column hidden). A failed request keeps the last answer: a blip is not
 * "disconnected". `askSoon` asks every few seconds for the next 45 seconds, for after the person pressed "Start runner" and is waiting to see it connect.
 */
export function useRunnerPing(enabled: boolean): { runners: RunnerInfo[] | null; askSoon: () => void } {
  const [runners, setRunners] = useState<RunnerInfo[] | null>(null);
  const quick = useRef<number | null>(null);
  const alive = useRef(true);
  const ask = useCallback(async () => {
    if (document.hidden) return;
    try {
      const answer = await listRunners();
      if (alive.current) setRunners(answer.runners);
    } catch {
      /* keep what we had */
    }
  }, []);
  useEffect(() => {
    alive.current = true;
    if (!enabled) return undefined;
    const first = window.setTimeout(() => { void ask(); }, 0);          // the first answer, as soon as the effect has run
    const timer = window.setInterval(() => { void ask(); }, POLL_MS);
    return () => {
      alive.current = false;
      window.clearTimeout(first);
      window.clearInterval(timer);
      if (quick.current !== null) window.clearInterval(quick.current);
    };
  }, [enabled, ask]);
  const askSoon = useCallback(() => {
    if (!alive.current) return;
    if (quick.current !== null) window.clearInterval(quick.current);
    const started = Date.now();
    quick.current = window.setInterval(() => {
      void ask();
      if (Date.now() - started > QUICK_FOR_MS && quick.current !== null) { window.clearInterval(quick.current); quick.current = null; }
    }, QUICK_MS);
  }, [ask]);
  return { runners: enabled ? runners : null, askSoon };
}

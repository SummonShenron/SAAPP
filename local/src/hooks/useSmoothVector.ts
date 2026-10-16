import { useEffect, useRef } from 'react';

/** The time constant of the ease, in seconds. A value reaches about 90% of the way in 2.3 of these, so 0.09 feels like `transition: 0.2s ease`. */
export const SMOOTH_TAU = 0.09;

interface Options {
  /** The wanted numbers at time t (seconds since `key` last changed): constant for a still shape, varying for a bob or a wave. */
  target: (t: number) => readonly number[];
  /** True when `target` varies with t, so the loop keeps running; otherwise it stops once the numbers have settled. */
  animated: boolean;
  /** Restarts t when it changes, so a new motion begins from its start. Include everything the target depends on. */
  key: string;
  /** Writes the current numbers to the DOM (an SVG attribute, say). Called every frame while moving. */
  apply: (v: number[]) => void;
  /** Reduced motion: jump straight to the target instead of easing. */
  instant?: boolean;
}

/**
 * Eases a set of numbers toward a target, frame by frame, and hands them to `apply`. It exists for SVG shapes (an arm's path, a mouth's curve):
 * CSS can't transition a path's `d` in every browser, but numbers can always be eased. When the target changes (a new look), the numbers glide from
 * wherever they are to the new ones instead of popping, and a motion that is already running (a bob, a wave) carries on from the eased value.
 * It never touches React state, so there is no re-render per frame.
 */
export function useSmoothVector({ target, animated, key, apply, instant = false }: Options) {
  const current = useRef<number[] | null>(null);
  const targetRef = useRef(target);
  const applyRef = useRef(apply);
  useEffect(() => {
    targetRef.current = target;
    applyRef.current = apply;
  });

  useEffect(() => {
    let frame = 0;
    const start = performance.now();
    let last = start;
    const tick = (now: number) => {
      const t = (now - start) / 1000;
      const dt = Math.min(0.05, (now - last) / 1000);
      last = now;
      const want = targetRef.current(t);
      if (!current.current || instant) {
        current.current = [...want];
      } else {
        const k = 1 - Math.exp(-dt / SMOOTH_TAU);
        for (let i = 0; i < want.length; i++) current.current[i] += (want[i] - current.current[i]) * k;
      }
      applyRef.current(current.current);
      const settled = current.current.every((v, i) => Math.abs(v - want[i]) < 0.01);
      if ((animated && !instant) || !settled) frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [key, animated, instant]);
}

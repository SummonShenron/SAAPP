import { useEffect, useRef } from 'react';
import { GRIP_MS, RIG_REST, blendRig, rigPose, type RigPose, type RigState } from '../components/patchyRig';

/** What the rig should be doing and how long it has to do it. */
export interface RigTarget extends RigState { ms: number }

/**
 * Eases the laptop and both arms to a target, frame by frame, and hands each frame's pose to `apply` (which writes it to the drawing). When the target changes it carries on from wherever the rig is, so a stage cut
 * short (a turn starts) glides back instead of popping. It never touches React state, so there is no re-render per frame.
 */
export function useStretchRig(target: RigTarget, apply: (pose: RigPose) => void) {
  const current = useRef<RigState>(RIG_REST);
  const applyRef = useRef(apply);
  useEffect(() => { applyRef.current = apply; });

  const { lap, grip, mode, ms } = target;
  useEffect(() => {
    const from = { ...current.current };
    const to: RigState = { lap, grip, mode };
    const duration = Math.max(ms, 1);
    const gripDuration = Math.min(duration, GRIP_MS);
    const start = performance.now();
    let frame = 0;
    const tick = (now: number) => {
      const elapsed = Math.max(0, now - start);
      const state = blendRig(from, to, Math.min(elapsed / duration, 1), Math.min(elapsed / gripDuration, 1));
      current.current = state;
      applyRef.current(rigPose(state));
      if (elapsed < duration) frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [lap, grip, mode, ms]);
}

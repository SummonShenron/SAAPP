import { useEffect, useState } from 'react';
import { motionAllowed } from '../components/patchyLook';
import { YAWN_EVERY_MS, YAWN_FIRST_MS, YAWN_MS } from '../components/patchyYawn';

/**
 * True while Patchy is mid-yawn. He only yawns while `drowsy` is true: the first one a moment after he goes drowsy, then one every few seconds, until he is no longer drowsy (a poke, a message, anything that wakes him).
 * A person who asks for less motion never sees one.
 */
export function useYawn(drowsy: boolean): boolean {
  const [yawning, setYawning] = useState(false);
  useEffect(() => {
    if (!drowsy || !motionAllowed()) return undefined;
    const timers: number[] = [];
    const yawn = () => {
      setYawning(true);
      timers.push(window.setTimeout(() => setYawning(false), YAWN_MS));
      timers.push(window.setTimeout(yawn, YAWN_EVERY_MS));
    };
    timers.push(window.setTimeout(yawn, YAWN_FIRST_MS));
    return () => { timers.forEach(timer => window.clearTimeout(timer)); setYawning(false); };
  }, [drowsy]);
  return drowsy && yawning;
}

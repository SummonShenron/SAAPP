import { useEffect, useState } from 'react';
import {
  CELEBRATE_MS, DROWSY_AFTER_MS, FAULT_MS, LAUGH_MS, NUDGE_MS, PRAISE_MS, SAD_MS, STEER_MS,
  resolveMood,
  type PatchyBudget, type PatchyEnergy, type PatchyLook, type PatchyMood,
} from '../components/patchyLook';

export interface PatchySignals {
  /** What the running turn is doing; 'idle' when none is running. */
  look?: PatchyLook;
  /** Bump to celebrate (a turn just finished well). */
  cheer?: number;
  /** Bump for a fist pump (praise). */
  praise?: number;
  /** Bump for the sad face (he stays still a while). */
  sad?: number;
  /** Bump to show the failure face (a turn just failed). */
  fault?: number;
  /** Bump to nod "got it" (a message was steered into the running turn). */
  steer?: number;
  /** The input has focus (the person clicked into it): he turns to watch it. */
  engaged?: boolean;
  /** The length of what the person has typed so far. Text in the box makes him read it; any change is activity that wakes him. */
  typed?: number;
  energy?: PatchyEnergy;
  budget?: PatchyBudget;
  /** Quiet needed before he gets drowsy; the default is minutes, a gallery or test can shorten it. */
  drowsyAfterMs?: number;
}

/**
 * Patchy's moods, shared by the small one on the input and the full-size one on the empty chat. The signals come in as props (counters that
 * are bumped, or plain values); the short-lived states they start (a celebration, a failure face, typing, a laugh) end on their own timers.
 */
export function usePatchyMood({
  look = 'idle', cheer = 0, praise = 0, sad = 0, fault = 0, steer = 0, engaged = false, typed = 0, energy = 'open', budget = 'ok', drowsyAfterMs = DROWSY_AFTER_MS,
}: PatchySignals) {
  const [laughing, setLaughing] = useState(false);
  const [nudged, setNudged] = useState(false);
  const [celebrating, setCelebrating] = useState(false);
  const [faulted, setFaulted] = useState(false);
  const [steered, setSteered] = useState(false);
  const [drowsy, setDrowsy] = useState(false);
  const [pokes, setPokes] = useState(0);
  const [praised, setPraised] = useState(false);
  const [saddened, setSaddened] = useState(false);

  // Reacting to a changed prop during render (not in an effect) is React's own pattern for "derive state when a prop changes".
  const [seen, setSeen] = useState({ cheer, praise, sad, fault, steer });
  if (seen.cheer !== cheer || seen.praise !== praise || seen.sad !== sad || seen.fault !== fault || seen.steer !== steer) {
    setSeen({ cheer, praise, sad, fault, steer });
    if (seen.cheer !== cheer) setCelebrating(true);
    if (seen.praise !== praise) setPraised(true);
    if (seen.sad !== sad) setSaddened(true);
    if (seen.fault !== fault) setFaulted(true);
    if (seen.steer !== steer) setSteered(true);
  }

  // Anything he can notice wakes him and starts the quiet over.
  const activity = `${typed}|${engaged}|${look}|${cheer}|${praise}|${sad}|${fault}|${steer}|${pokes}|${budget}`;
  const [seenActivity, setSeenActivity] = useState(activity);
  if (activity !== seenActivity) {
    setSeenActivity(activity);
    setDrowsy(false);
  }

  useEffect(() => {
    if (!celebrating) return;
    const timer = setTimeout(() => setCelebrating(false), CELEBRATE_MS);
    return () => clearTimeout(timer);
  }, [celebrating, cheer]);

  useEffect(() => {
    if (!faulted) return;
    const timer = setTimeout(() => setFaulted(false), FAULT_MS);
    return () => clearTimeout(timer);
  }, [faulted, fault]);

  useEffect(() => {
    if (!steered) return;
    const timer = setTimeout(() => setSteered(false), STEER_MS);
    return () => clearTimeout(timer);
  }, [steered, steer]);

  useEffect(() => {
    if (!laughing) return;
    const timer = setTimeout(() => setLaughing(false), LAUGH_MS);
    return () => clearTimeout(timer);
  }, [laughing]);

  useEffect(() => {
    if (!nudged) return;
    const timer = setTimeout(() => setNudged(false), NUDGE_MS);
    return () => clearTimeout(timer);
  }, [nudged]);

  useEffect(() => {
    if (!praised) return;
    const timer = setTimeout(() => setPraised(false), PRAISE_MS);
    return () => clearTimeout(timer);
  }, [praised, praise]);

  useEffect(() => {
    if (!saddened) return;
    const timer = setTimeout(() => setSaddened(false), SAD_MS);
    return () => clearTimeout(timer);
  }, [saddened, sad]);

  useEffect(() => {
    if (drowsy) return;
    const timer = setTimeout(() => setDrowsy(true), drowsyAfterMs);
    return () => clearTimeout(timer);
  }, [drowsy, activity, drowsyAfterMs]);

  const mood: PatchyMood = resolveMood({ laughing, nudged, faulted, celebrating, praised, sad: saddened, watching: engaged, drowsy, look, budget });

  // A low or frustrated user doesn't get giggled at: poking him just earns a soft nod.
  const poke = () => {
    setPokes(p => p + 1);
    if (energy === 'subdued') setNudged(true);
    else setLaughing(true);
  };

  // Watching the box with text in it: he turns further, to read it.
  const reading = mood === 'watching' && typed > 0;

  return { mood, look, steered, reading, laughing, poke };
}

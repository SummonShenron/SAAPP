// Mini Patchy's stretch (MiniPatchy.tsx, the small one on the chat input): when a conversation has started (or the page is refreshed on one) and he has nothing to do, he sets his laptop down, stands up, stretches with his
// arms over his head, sits back down and picks the laptop up again. The sequence is a list of stages; what is drawn at each stage (MiniPatchy.css) is eased between them, so the list is the whole script.
// No React in here, so it can be tested (patchyStretch.test.mjs).

/** How long he has to be idle before he starts: long enough to be seen sitting at the laptop first. */
export const STRETCH_START_DELAY_MS = 1800;

export interface StretchStage {
  name: string;
  /** How long this stage lasts before the next one begins. */
  ms: number;
  /** The laptop is on the edge beside him (his hands are off the keys). */
  down: boolean;
  /** He is standing on the input's edge instead of sitting on it. */
  stand: boolean;
  /** Both arms are up over his head, and his eyes are squeezed shut. */
  reach: boolean;
  /** How far he leans back from upright, in degrees (negative: forward a little). */
  lean: number;
}

export const STRETCH_STAGES: readonly StretchStage[] = [
  { name: 'lift', ms: 700, down: true, stand: false, reach: false, lean: 0 },
  { name: 'rise', ms: 800, down: true, stand: true, reach: false, lean: 0 },
  { name: 'reach', ms: 600, down: true, stand: true, reach: true, lean: 3 },
  { name: 'lean back', ms: 800, down: true, stand: true, reach: true, lean: 11 },
  { name: 'sway', ms: 700, down: true, stand: true, reach: true, lean: -3 },
  { name: 'lean back again', ms: 600, down: true, stand: true, reach: true, lean: 8 },
  { name: 'relax', ms: 700, down: true, stand: true, reach: false, lean: 0 },
  { name: 'sit', ms: 800, down: true, stand: false, reach: false, lean: 0 },
  { name: 'pick up', ms: 700, down: false, stand: false, reach: false, lean: 0 },
];

/** From the first stage to the moment he is typing again. */
export const STRETCH_TOTAL_MS = STRETCH_STAGES.reduce((sum, stage) => sum + stage.ms, 0);

export interface StretchFlags { stretching: boolean; down: boolean; stand: boolean; reach: boolean; lean: number }

/** What is drawn at a stage. -1 (or any index outside the list) is not stretching: he is sitting at his laptop. Pure. */
export function stretchFlags(stage: number): StretchFlags {
  const s = Number.isInteger(stage) && stage >= 0 && stage < STRETCH_STAGES.length ? STRETCH_STAGES[stage] : null;
  return s ? { stretching: true, down: s.down, stand: s.stand, reach: s.reach, lean: s.lean } : { stretching: false, down: false, stand: false, reach: false, lean: 0 };
}

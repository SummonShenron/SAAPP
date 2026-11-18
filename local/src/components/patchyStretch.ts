// Mini Patchy's stretch (MiniPatchy.tsx, the small one on the chat input): when a conversation has started (or the page is refreshed on one) and he has nothing to do, he takes his laptop in both hands, sets it down beside
// him, stands up, stretches with his arms over his head, sits back down, picks the laptop up again and puts it on his lap. The sequence is a list of stages; what is drawn at each stage is eased between them (the laptop and
// both arms by patchyRig.ts, the rest by MiniPatchy.css), so the list is the whole script. No React in here, so it can be tested (patchyStretch.test.mjs).

/** How long he has to be idle before he starts: long enough to be seen sitting at the laptop first. */
export const STRETCH_START_DELAY_MS = 1800;

/** What his arms are doing: 'free' is the ordinary arms (hanging, or up over his head); the others are the carrying rig (patchyRig.ts): holding the laptop, hanging after letting go, or back on the keys. */
export type StretchArms = 'free' | 'grip' | 'hang' | 'type';

export interface StretchStage {
  name: string;
  /** How long this stage lasts before the next one begins. */
  ms: number;
  /** Where the laptop is when the stage ends: 0 on his lap, 1 on the edge beside him. */
  lap: 0 | 1;
  arms: StretchArms;
  /** Where the left arm rests when it is not holding the laptop: 0 on the keys, 1 hanging (patchyRig.ts RigState.mode). */
  mode: 0 | 1;
  /** He is standing on the input's edge instead of sitting on it. */
  stand: boolean;
  /** Both arms are up over his head, and his eyes are squeezed shut. */
  reach: boolean;
  /** How far he leans back from upright, in degrees (negative: forward a little). */
  lean: number;
}

export const STRETCH_STAGES: readonly StretchStage[] = [
  { name: 'take hold', ms: 450, lap: 0, arms: 'grip', mode: 0, stand: false, reach: false, lean: 0 },
  { name: 'set it down', ms: 1200, lap: 1, arms: 'grip', mode: 0, stand: false, reach: false, lean: 0 },
  { name: 'let go', ms: 500, lap: 1, arms: 'hang', mode: 1, stand: false, reach: false, lean: 0 },
  { name: 'rise', ms: 800, lap: 1, arms: 'free', mode: 1, stand: true, reach: false, lean: 0 },
  { name: 'reach', ms: 600, lap: 1, arms: 'free', mode: 1, stand: true, reach: true, lean: 3 },
  { name: 'lean back', ms: 800, lap: 1, arms: 'free', mode: 1, stand: true, reach: true, lean: 11 },
  { name: 'sway', ms: 700, lap: 1, arms: 'free', mode: 1, stand: true, reach: true, lean: -3 },
  { name: 'lean back again', ms: 600, lap: 1, arms: 'free', mode: 1, stand: true, reach: true, lean: 8 },
  { name: 'relax', ms: 700, lap: 1, arms: 'free', mode: 1, stand: true, reach: false, lean: 0 },
  { name: 'sit', ms: 800, lap: 1, arms: 'free', mode: 1, stand: false, reach: false, lean: 0 },
  { name: 'take hold again', ms: 500, lap: 1, arms: 'grip', mode: 1, stand: false, reach: false, lean: 0 },
  { name: 'bring it back', ms: 1200, lap: 0, arms: 'grip', mode: 1, stand: false, reach: false, lean: 0 },
  { name: 'hands to the keys', ms: 500, lap: 0, arms: 'type', mode: 0, stand: false, reach: false, lean: 0 },
];

/** From the first stage to the moment he is typing again. */
export const STRETCH_TOTAL_MS = STRETCH_STAGES.reduce((sum, stage) => sum + stage.ms, 0);

/** What the carrying rig is asked to do (hooks/useStretchRig.ts): where the laptop should be, whether the hands hold it, where the left arm rests, and how long it has. */
export interface StretchRigTarget { lap: number; grip: number; mode: number; ms: number }

export interface StretchFlags {
  stretching: boolean;
  /** The carrying rig's arms are drawn (and the ordinary ones are not). */
  rigging: boolean;
  /** His hands are off the laptop and it is on the floor: the ordinary hanging arm is drawn. */
  down: boolean;
  stand: boolean;
  reach: boolean;
  lean: number;
  rig: StretchRigTarget;
}

/** How long the rig takes to settle back when he is not stretching (a turn cut it short, or it has finished). */
export const RIG_SETTLE_MS = 400;
const AT_REST: StretchFlags = { stretching: false, rigging: false, down: false, stand: false, reach: false, lean: 0, rig: { lap: 0, grip: 0, mode: 0, ms: RIG_SETTLE_MS } };

/** What is drawn at a stage. -1 (or any index outside the list) is not stretching: he is sitting at his laptop. Pure. */
export function stretchFlags(stage: number): StretchFlags {
  const s = Number.isInteger(stage) && stage >= 0 && stage < STRETCH_STAGES.length ? STRETCH_STAGES[stage] : null;
  if (!s) return AT_REST;
  return {
    stretching: true,
    rigging: s.arms !== 'free',
    down: s.arms === 'free' && s.lap === 1,
    stand: s.stand,
    reach: s.reach,
    lean: s.lean,
    rig: { lap: s.lap, grip: s.arms === 'grip' ? 1 : 0, mode: s.mode, ms: s.ms },
  };
}

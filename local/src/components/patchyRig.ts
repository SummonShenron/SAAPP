// How Mini Patchy carries his laptop in the stretch (MiniPatchy.tsx, hooks/useStretchRig.ts): both hands take hold of its two sides, it is lifted in an arc and put down beside him (or brought back the same way), and the hands
// let go. The laptop and the arms are worked out from ONE small state, so the hands are always where the laptop's corners are. No React in here, so it can be tested (patchyRig.test.mjs).

/** The whole rig. lap: where the laptop is, 0 on his lap and 1 on the edge beside him. grip: how much both hands are on it, 0 off and 1 holding. mode: where the left arm rests when it is not holding it, 0 on the keys and 1 hanging. */
export interface RigState { lap: number; grip: number; mode: number }
export const RIG_REST: RigState = { lap: 0, grip: 0, mode: 0 };

type Vec6 = readonly [number, number, number, number, number, number];
type Point = readonly [number, number];

/** Where his shoulders are (his 100-unit drawing). */
export const SHOULDER_LEFT: Point = [24, 62];
export const SHOULDER_RIGHT: Point = [76, 62];
/** An arm is a cubic curve from its shoulder: [first control, second control, hand]. These are the shapes the arms already have when he is not carrying anything (the typing hand on the laptop, the arms hanging). */
export const LEFT_TYPE: Vec6 = [16, 70, 22, 82, 32.5, 78.5];
export const LEFT_HANG: Vec6 = [16, 66, 16, 76, 22, 82];
export const RIGHT_HANG: Vec6 = [84, 68, 84, 76, 78, 82];

/** The two corners of the laptop's base he takes hold of, with the laptop on his lap. */
const GRIP_LEFT: Point = [34, 77];
const GRIP_RIGHT: Point = [74, 76];

/** How long taking hold of the laptop, or letting go of it, takes: quicker than the carrying itself (hooks/useStretchRig.ts). */
export const GRIP_MS = 400;

/** Where the laptop ends up (how far to his left, and a hair lower: it is on the edge, not on his lap), and how high it is lifted on the way. */
export const LAP_TRAVEL: Point = [-36, 3];
export const LAP_LIFT = 9;

const clamp01 = (n: number) => Math.min(1, Math.max(0, Number.isFinite(n) ? n : 0));
const round = (n: number) => Math.round(n * 100) / 100 + 0;          // (+ 0 turns a negative zero into a plain one)

/** How far the laptop has moved from where it sits on his lap: along a lifted arc, so it is picked up, carried and put down, not slid. Pure. */
export function lapOffset(lap: number): [number, number] {
  const p = clamp01(lap);
  return [round(LAP_TRAVEL[0] * p), round(LAP_TRAVEL[1] * p - LAP_LIFT * Math.sin(Math.PI * p))];
}

/** A smooth start and a smooth stop. Pure. */
export function easeInOut(t: number): number {
  const p = clamp01(t);
  return p < 0.5 ? 2 * p * p : 1 - ((-2 * p + 2) ** 2) / 2;
}

/** The rig part-way from one state to another. The laptop and the resting arm take the whole of `t`; the grip of the hands (taking hold, letting go) is quicker, so it takes `gripT`. Pure. */
export function blendRig(from: RigState, to: RigState, t: number, gripT: number = t): RigState {
  const e = easeInOut(t);
  const g = easeInOut(gripT);
  return { lap: from.lap + (to.lap - from.lap) * e, mode: from.mode + (to.mode - from.mode) * e, grip: from.grip + (to.grip - from.grip) * g };
}

const lerpVec = (a: Vec6, b: Vec6, f: number): Vec6 => a.map((v, i) => v + (b[i] - v) * f) as unknown as Vec6;

/** An arm that reaches from a shoulder out to a hand holding the laptop: a loose, slightly sagging curve (arms are rubber). */
function gripVec(shoulder: Point, hand: Point): Vec6 {
  return [shoulder[0] + (hand[0] - shoulder[0]) * 0.2, shoulder[1] + 14, shoulder[0] + (hand[0] - shoulder[0]) * 0.8, hand[1] + 4, hand[0], hand[1]];
}

export interface RigPose { lap: [number, number]; left: Vec6; right: Vec6 }

/** Where the laptop is and what shape each arm has at a state. At rest it is exactly his usual pose: the laptop on his lap, the left hand on it, the right arm hanging. Pure. */
export function rigPose(state: RigState): RigPose {
  const [ox, oy] = lapOffset(state.lap);
  const g = clamp01(state.grip);
  const m = clamp01(state.mode);
  const left = lerpVec(lerpVec(LEFT_TYPE, LEFT_HANG, m), gripVec(SHOULDER_LEFT, [GRIP_LEFT[0] + ox, GRIP_LEFT[1] + oy]), g);
  const right = lerpVec(RIGHT_HANG, gripVec(SHOULDER_RIGHT, [GRIP_RIGHT[0] + ox, GRIP_RIGHT[1] + oy]), g);
  return { lap: [ox, oy], left, right };
}

/** The path of an arm from its shoulder. */
export function armPath(shoulder: Point, v: readonly number[]): string {
  return `M ${shoulder[0]} ${shoulder[1]} C ${round(v[0])} ${round(v[1])}, ${round(v[2])} ${round(v[3])}, ${round(v[4])} ${round(v[5])}`;
}

// Mini Patchy's small flourishes (MiniPatchy.tsx, MiniPatchy.css): a HELLO wave when you open an older conversation, and a TA-DA when a long plan finishes. The timings and the rules for them are here, with no React, so they can be
// tested (patchyFlourish.test.mjs).

/** How long his hello lasts: one trip through the waving arm's cycle (PatchyParts.tsx WAVE_TIMES), at this many seconds a cycle. His stretch starts only after it. */
export const HELLO_WAVE_SECONDS = 3.6;
export const HELLO_MS = HELLO_WAVE_SECONDS * 1000;

/** A plan with at least this many steps is a long one: finishing it is worth a ta-da. */
export const PLAN_LONG_STEPS = 3;
/** After the plan's last step is done, how long before the ta-da: the celebration of the turn that finished it comes first. */
export const TADA_AFTER_MS = 1800;
/** How long the ta-da lasts (the stylesheet's pose and its sparkles end inside this). */
export const TADA_MS = 2400;

/** Whether a plan that is in this state with this many steps is a long plan that has just been finished. Pure. */
export function isLongPlanDone(state: string | null | undefined, steps: number): boolean {
  return state === 'done' && Number.isFinite(steps) && steps >= PLAN_LONG_STEPS;
}

export interface Sparkle { x: number; y: number; r: number; delay: number }

/** Where the sparkles burst around him in the ta-da (his 100-unit drawing), each a little later than the one before. */
export const SPARKLES: readonly Sparkle[] = [
  { x: 12, y: 22, r: 5.8, delay: 0 },
  { x: 88, y: 24, r: 6.4, delay: 0.15 },
  { x: 22, y: 7, r: 4.4, delay: 0.3 },
  { x: 80, y: 8, r: 4.8, delay: 0.45 },
  { x: 7, y: 46, r: 4.2, delay: 0.6 },
  { x: 93, y: 48, r: 4.4, delay: 0.75 },
];

const round = (n: number) => Math.round(n * 100) / 100;

/** A four-pointed star centred on (cx, cy), its points `r` away and its sides curving in to the centre. Pure. */
export function sparklePath(cx: number, cy: number, r: number): string {
  const c = (x: number, y: number) => `${round(x)} ${round(y)}`;
  return `M ${c(cx, cy - r)} Q ${c(cx, cy)} ${c(cx + r, cy)} Q ${c(cx, cy)} ${c(cx, cy + r)} Q ${c(cx, cy)} ${c(cx - r, cy)} Q ${c(cx, cy)} ${c(cx, cy - r)} Z`;
}

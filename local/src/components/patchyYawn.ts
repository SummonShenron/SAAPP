// Patchy's yawn (MiniPatchy.tsx, PatchyHero.tsx): once he has gone drowsy (a long quiet, patchyLook.ts) he yawns every so often: his mouth opens wide, his eyes squeeze shut, his head tips back and a hand comes up to cover his
// mouth, then he goes back to drooping. These are the timings; useYawn.ts plays them and MiniPatchy.css draws the rest. No React in here, so it can be tested (patchyYawn.test.mjs).

/** How long after he goes drowsy the first yawn comes: long enough to see the heavy lids first. */
export const YAWN_FIRST_MS = 1500;
/** From the start of one yawn to the start of the next. */
export const YAWN_EVERY_MS = 9000;
/** How long one yawn lasts (the arm, the mouth and the tipped head all ease in and out inside this). */
export const YAWN_MS = 2800;

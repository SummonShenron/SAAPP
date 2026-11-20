// The loader turning into the first step's icon (2026-11-19). The first step of a turn is nearly always the "think" step, whose icon is a little four-point sparkle (components/StepIcon.tsx). When it lands, the animated
// logo does not just fade: in the first part (GATHER_SHARE of the time) its six dots gather into the middle and shrink, and a sparkle grows where they were; in the rest the sparkle travels down to the step's own dot, getting
// smaller as it goes, and arrives just as that dot comes in (ARRIVE_DELAY_MS, graphLoader.ts), then fades under it. Pure maths, like thinkingLogoMotion.ts: what everything looks like `progress` of the way through, from the
// pose the loader was last drawn in. components/ThinkingLogo.tsx draws it. Only for a first step whose icon IS the sparkle (StepsDisclosure.tsx); any other first step keeps the plain fade.
import { MID_X, MID_Y, bodyTransform, clamp01, easeInOut, easeOutBack, lerp, type Body, type DotPose, type LogoPose, type TrailPose } from './thinkingLogoMotion.ts';

/** The first part of the time, in which the dots gather and the sparkle forms; the rest is the sparkle's journey. */
export const GATHER_SHARE = 0.45;
/** After the journey the sparkle stays a moment, fading as the step's own dot comes in over it; this is how long, as a share of the whole time. */
export const FADE_TAIL = 0.3;
/** The sparkle's drawing: the "think" icon's own path in its own 16 by 16 box (the same as StepIcon.tsx draws, which a test pins), stroked 1.5 wide with round ends. */
export const GLYPH_PATH = 'M8 2l1.3 4.7L14 8l-4.7 1.3L8 14l-1.3-4.7L2 8l4.7-1.3z';
export const GLYPH_BOX = 16;
export const GLYPH_STROKE = 1.5;
/** How big the sparkle is once it has formed, in the logo's drawing units (the mark is 176 across): about two thirds of the mark's height. */
export const GLYPH_BIG = 64;
/** Where the sparkle ends, and how big: from the step's dot, measured on the page and turned into the logo's drawing units (ThinkingLogo.tsx). */
export interface BecomeTarget { travelX: number; travelY: number; endSize: number }

export interface BecomePose {
  body: Body;
  transform: string;
  dots: DotPose[];
  /** How visible the six dots are: they are gone by the time the sparkle has formed. */
  dotsOpacity: number;
  edgeOpacity: number;
  curveOpacity: number;
  trail: TrailPose[];
  runnerOpacity: number;
  /** The sparkle: where its middle is, how big its 16-unit box is drawn (in drawing units), and how visible. */
  glyph: { x: number; y: number; size: number; opacity: number };
}

/** What everything looks like `progress` (0 to 1, and on to 1 + FADE_TAIL) of the way through the hand-over, starting from the pose `from` the loader was last drawn in. */
export function becomePose(from: LogoPose, progress: number, target: BecomeTarget): BecomePose {
  const p = Math.max(progress, 0);
  const g = clamp01(p / GATHER_SHARE);
  const gather = easeInOut(g);
  const away = 1 - clamp01(g / 0.6);                       // everything that is not a dot (the links, the line, the trail, the runner) is gone before the dots have all arrived
  const travel = easeInOut(clamp01((p - GATHER_SHARE) / (1 - GATHER_SHARE)));
  const formed = GLYPH_BIG * easeOutBack(clamp01((g - 0.3) / 0.7));
  const body: Body = { lift: lerp(from.body.lift, 0, gather), scaleX: lerp(from.body.scaleX, 1, gather), scaleY: lerp(from.body.scaleY, 1, gather) };
  return {
    body,
    transform: bodyTransform(body),
    dots: from.dots.map(dot => ({
      x: lerp(dot.x, MID_X, gather),
      y: lerp(dot.y, MID_Y, gather),
      scaleX: lerp(dot.scaleX, 0.3, gather),
      scaleY: lerp(dot.scaleY, 0.3, gather),
      ringScale: dot.ringScale,
      ringOpacity: dot.ringOpacity * (1 - g),
    })),
    dotsOpacity: 1 - clamp01((g - 0.7) / 0.3),
    edgeOpacity: from.edgeOpacity * away,
    curveOpacity: from.curve.opacity * away,
    trail: from.trail.map(piece => ({ points: piece.points, opacity: piece.opacity * away })),
    runnerOpacity: from.runner.opacity * away,
    glyph: {
      x: MID_X + target.travelX * travel,
      y: MID_Y + target.travelY * travel,
      size: p < GATHER_SHARE ? formed : lerp(GLYPH_BIG, target.endSize, travel),
      opacity: p <= 1 ? clamp01((g - 0.3) / 0.35) : 1 - clamp01((p - 1) / FADE_TAIL),
    },
  };
}

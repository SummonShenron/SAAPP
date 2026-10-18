import { useRef, useState } from 'react';
import { useSmoothVector } from '../hooks/useSmoothVector';
import { motionAllowed, type ArmPose, type PatchyBudget, type PatchyEnergy, type PatchyLook, type PatchyMood } from './patchyLook';
import './__styles__/MiniPatchy.css';

// The pieces the small Patchy (MiniPatchy, on the chat input) and the full-size one (PatchyHero, on the empty chat) share: the head with every
// expression and the right arm with its poses. Both draw in the original Patchy's 100-unit coordinate space (errAgent's PatchyEmptyState.tsx), so
// the same head and arm drop onto either body.
//
// Nothing here pops when his look changes. Colors, the expression sets (eyes, brows, lids, the "?", the z's, the sweat drop) crossfade through
// CSS; the shapes that CSS can't ease everywhere (the arm's path, the mouth's curve) are eased by useSmoothVector; the head moves between poses
// with CSS transitions (MiniPatchy.css).

const fade = (on: boolean) => `mp-fade${on ? ' mp-on' : ''}`;

// ---------------------------------------------------------------------------------------------------------------------------------------
// The right arm. Its shoulder is at (76, 62); a pose is the cubic curve to where the ball hand ends up: [c1x, c1y, c2x, c2y, x, y].

type Vec6 = readonly [number, number, number, number, number, number];

interface Pose { v: Vec6; alt?: Vec6; dur?: number }

const POSES: Record<ArmPose, Pose> = {
  hang: { v: [84, 68, 84, 76, 78, 82] },
  type: { v: [70, 70, 62, 80, 52, 78.5] },
  // Hovering just over the keys, as if about to type: he is watching you write and getting ready to answer.
  ready: { v: [70, 68, 62, 77, 53, 75.5] },
  // A hand to the head, bobbing: thinking it over.
  think: { v: [88, 50, 84, 32, 74, 28], alt: [88, 46, 83, 27, 73, 24], dur: 1.6 },
  // The hand holds a magnifying glass over the right eye.
  search: { v: [82, 60, 74, 52, 66, 45] },
  // An elbow-out shrug.
  puzzle: { v: [90, 66, 92, 56, 86, 50], alt: [90, 64, 93, 53, 87, 46], dur: 1.8 },
  cheer: { v: [84, 50, 86, 36, 82, 24] },
  hug: { v: [72, 75, 64, 75, 57, 70] },
};

// The original's idle wave for the free arm: hang, then wave a few times, then hang again (keyframe times as fractions of the cycle).
const WAVE_TIMES = [0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 1];
const WAVE_UP_A: Vec6 = [88, 56, 94, 42, 92, 30];
const WAVE_UP_B: Vec6 = [82, 50, 86, 36, 84, 26];
const WAVE_FRAMES: Vec6[] = [POSES.hang.v, WAVE_UP_A, WAVE_UP_B, WAVE_UP_A, WAVE_UP_B, WAVE_UP_A, POSES.hang.v, POSES.hang.v];

const lerp = (a: readonly number[], b: readonly number[], f: number) => a.map((v, i) => v + (b[i] - v) * f);

/** Where the arm wants to be t seconds into its motion. */
function armAt(pose: ArmPose, waving: boolean, waveDur: number, t: number): readonly number[] {
  if (waving) {
    const u = (t % waveDur) / waveDur;
    let i = WAVE_TIMES.length - 2;
    while (i > 0 && u < WAVE_TIMES[i]) i--;
    const f = (u - WAVE_TIMES[i]) / (WAVE_TIMES[i + 1] - WAVE_TIMES[i]);
    return lerp(WAVE_FRAMES[i], WAVE_FRAMES[i + 1], 1 - (1 - f) ** 3); // each leg eases out, like the original's spline
  }
  const p = POSES[pose];
  if (!p.alt || !p.dur) return p.v;
  return lerp(p.v, p.alt, (1 - Math.cos((2 * Math.PI * t) / p.dur)) / 2);
}

const armPath = (v: readonly number[]) => `M 76 62 C ${v[0]} ${v[1]}, ${v[2]} ${v[3]}, ${v[4]} ${v[5]}`;

export function PatchyRightArm({ pose, wave = false, waveDur = 8, handClass }: { pose: ArmPose; wave?: boolean; waveDur?: number; handClass?: string }) {
  const moving = motionAllowed();
  const waving = wave && pose === 'hang';
  const pathRef = useRef<SVGPathElement>(null);
  const handRef = useRef<SVGCircleElement>(null);
  const [first] = useState(() => POSES[pose].v);

  useSmoothVector({
    target: t => armAt(pose, waving, waveDur, moving ? t : 0),
    animated: moving && (waving || POSES[pose].alt !== undefined),
    key: `${pose}|${waving}|${waveDur}`,
    instant: !moving,
    apply: v => {
      pathRef.current?.setAttribute('d', armPath(v));
      handRef.current?.setAttribute('cx', String(v[4]));
      handRef.current?.setAttribute('cy', String(v[5]));
    },
  });

  return (
    <g className="mp-scan">
      <path ref={pathRef} d={armPath(first)} stroke="#8E95A2" strokeWidth="4" strokeLinecap="round" fill="none" />
      <circle ref={handRef} className={`mp-accent${handClass ? ` ${handClass}` : ''}`} cx={first[4]} cy={first[5]} r="3.5" />
      {/* The magnifying glass: its handle runs from the hand to a lens framing the right eye. It fades in and out with the search pose. */}
      <g className={fade(pose === 'search')}>
        <line x1="66" y1="45" x2="61" y2="39" stroke="#8E95A2" strokeWidth="3" strokeLinecap="round" />
        <circle className="mp-stroke-accent" cx="58" cy="35" r="6.5" strokeWidth="2" fill="rgba(168, 85, 247, 0.3)" />
        <path d="M55 33 Q57 31 59 32" stroke="#FFFFFF" strokeWidth="1.2" strokeLinecap="round" opacity="0.85" fill="none" />
      </g>
    </g>
  );
}

// ---------------------------------------------------------------------------------------------------------------------------------------
// The mouth: every shape is the same two-piece curve (M x0 y0 Q c1 m Q c2 x2 y2), so any two of them can be eased into each other.

type Vec10 = readonly number[];

/** A single quadratic curve, split in half so it fits the shared two-piece form. */
function quad(x0: number, y0: number, cx: number, cy: number, x2: number, y2: number): Vec10 {
  return [x0, y0, (x0 + cx) / 2, (y0 + cy) / 2, (x0 + 2 * cx + x2) / 4, (y0 + 2 * cy + y2) / 4, (cx + x2) / 2, (cy + y2) / 2, x2, y2];
}
const flat = (x0: number, y: number, x2: number) => quad(x0, y, (x0 + x2) / 2, y, x2, y);

const mouthPath = (v: readonly number[]) => `M${v[0]} ${v[1]} Q${v[2]} ${v[3]} ${v[4]} ${v[5]} Q${v[6]} ${v[7]} ${v[8]} ${v[9]}`;

// ---------------------------------------------------------------------------------------------------------------------------------------
// The head, with every expression.

interface HeadProps {
  mood: PatchyMood;
  look: PatchyLook;
  energy: PatchyEnergy;
  budget: PatchyBudget;
  /** Locked in on what he is doing and too busy to look up: no round eyes: flat dashes ("-_-") that now and then turn into two little dots that sweep right to left three times, like reading three lines of code (the landing page's Patchy; the timing is in PatchyAtLaptop.css). */
  focused?: boolean;
  /** Drawn turned toward the screen (near ear on the side of the head). Defaults to `focused`; the landing page keeps it on while he looks up so the turn can ease out. */
  turned?: boolean;
  /** Eyes squeezed into ">  <" and a wobbly mouth, for a head shake. */
  scrunched?: boolean;
  /** No mouth at all. */
  mouthless?: boolean;
}

export function PatchyHead({ mood, look, energy, budget, focused = false, turned = focused, scrunched = false, mouthless = false }: HeadProps) {
  const doing: PatchyLook = mood === 'active' ? look : 'idle';
  const laughing = mood === 'laugh';
  const closedEyes = mood === 'soft' || mood === 'asleep';
  const eyesOpen = !closedEyes && !laughing && !scrunched;
  const calm = mood === 'idle' || mood === 'watching' || mood === 'drowsy';
  // Heavy lids: a long quiet, or a chat that has been slowed down.
  const lids = mood === 'drowsy' || (budget === 'throttled' && (mood === 'idle' || mood === 'watching'));
  const sweat = (doing === 'puzzled' && energy !== 'subdued') || (budget === 'notice' && calm);
  // Worried brows, inner ends raised: concerned for you, or sorry it went wrong.
  const worried = mood === 'error' || (energy === 'subdued' && doing !== 'puzzled');

  const mouth: Vec10 =
    mood === 'error' ? quad(43, 44, 50, 39, 57, 44)
    : mood === 'asleep' ? flat(46, 43, 54)
    : doing === 'puzzled' ? [42, 43, 46, 40, 50, 43, 54, 46, 58, 43]
    : doing === 'thinking' ? flat(46, 42.5, 54)
    : doing === 'searching' ? flat(45, 42.5, 55)
    : lids ? flat(46, 43, 54)
    : energy === 'subdued' ? quad(45, 43.5, 50, 40.5, 55, 43.5)
    : energy === 'easing' ? quad(44, 41.5, 50, 44.5, 56, 41.5)
    : mood === 'done' ? quad(41, 40, 50, 48, 59, 40)
    : focused ? flat(46.5, 43.6, 53.5)
    : doing === 'writing' ? flat(43, 43, 57)
    : quad(43, 41, 50, 46, 57, 41);
  const mouthShown = !laughing && mood !== 'soft' && !scrunched && !mouthless;

  const mouthRef = useRef<SVGPathElement>(null);
  const [firstMouth] = useState(() => mouthPath(mouth));
  useSmoothVector({
    target: () => mouth,
    animated: false,
    key: mouth.join(','),
    instant: !motionAllowed(),
    apply: v => mouthRef.current?.setAttribute('d', mouthPath(v)),
  });

  return (
    <g className="mp-head">
      <g className="mp-turn">
        <line x1="50" y1="20" x2="50" y2="12" stroke="#8E95A2" strokeWidth="2.5" strokeLinecap="round" />
        <circle className="mp-accent mp-antenna" cx="50" cy="10" r="4" />

        <rect className="mp-ear mp-ear-l" x="23" y="32" width="5" height="10" rx="2" fill="#8E95A2" />
        <rect className="mp-ear mp-ear-r" x="72" y="32" width="5" height="10" rx="2" fill="#8E95A2" />

        <rect className="mp-shell" x="28" y="20" width="44" height="34" rx="10" fill="#121316" stroke="#3A3F4C" strokeWidth="2" />

        {/* The face (visor and every feature on it) is one group, so when he turns it slides across the shell: that is what reads as a turn.
            Every expression is always drawn and faded in or out, so changing look is a crossfade and not a swap. */}
        <g className="mp-face">
          <rect x="33" y="25" width="34" height="24" rx="6" fill="#0C1016" stroke="#1F242D" />

          {/* Eyes open */}
          <g className={fade(eyesOpen && !focused)}>
            <g className="mp-eyes">
              <circle className="mp-accent" cx="42" cy="35" r="3.5" />
              <circle className="mp-accent" cx="58" cy="35" r="3.5" />
            </g>
            <g className={fade(lids)}>
              <rect x="36" y="30" width="28" height="5.2" fill="#0C1016" />
            </g>
            {/* One brow raised, one lowered: puzzled */}
            <g className={`mp-stroke-accent ${fade(doing === 'puzzled')}`} strokeWidth="1.8" strokeLinecap="round" fill="none">
              <path d="M37 29 Q41 25.5 45 28.5" />
              <path d="M55 31.5 L63 30" />
            </g>
            <g className={`mp-stroke-accent ${fade(worried && doing !== 'puzzled')}`} strokeWidth="1.8" strokeLinecap="round">
              <path d="M37.5 31.5 L45.5 28.5" />
              <path d="M62.5 31.5 L54.5 28.5" />
            </g>
          </g>

          {/* Focused: dashes for eyes, swapped now and then for two dots that sweep from right to left and snap back, three times */}
          {focused && eyesOpen && (
            <g>
              <g className="ps-eyes-dash mp-stroke-accent" strokeWidth="2.4" strokeLinecap="round" fill="none">
                <path d="M38.5 38 H45.5" />
                <path d="M54.5 38 H61.5" />
              </g>
              <g className="ps-eyes-dots">
                <g className="ps-dots-run mp-accent">
                  <circle cx="42" cy="38" r="1.9" />
                  <circle cx="58" cy="38" r="1.9" />
                </g>
              </g>
              {/* and his mouth falls open a little, "oooh", while he reads (the flat mouth below is faded out for that stretch) */}
              <circle className="ps-eyes-dots mp-accent" cx="50" cy="43.4" r="2.3" />
            </g>
          )}

          {/* Scrunched: eyes squeezed shut, no mouth */}
          <g className={`mp-stroke-accent ${fade(scrunched)}`} strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" fill="none">
            <path d="M38 32.5 L45 36 L38 39.5" />
            <path d="M62 32.5 L55 36 L62 39.5" />
          </g>

          {/* Eyes closed: asleep, or a soft nod */}
          <g className={`mp-stroke-accent ${fade(closedEyes)}`} strokeWidth="2.2" strokeLinecap="round" fill="none">
            <path d="M 38 36 Q 42 39 46 36" />
            <path d="M 54 36 Q 58 39 62 36" />
          </g>

          {/* Laughing: happy eyes and an open mouth */}
          <g className={fade(laughing)}>
            <g className="mp-stroke-accent" strokeWidth="2.5" strokeLinecap="round" fill="none">
              <path d="M 38 36 Q 42 30 46 36" />
              <path d="M 54 36 Q 58 30 62 36" />
            </g>
            <path className="mp-accent" d="M41 39 Q50 50 59 39 Z" opacity="0.9" />
          </g>

          <g className={`${fade(mouthShown)}${focused ? ' ps-mouth-flat' : ''}`}>
            <path ref={mouthRef} className="mp-stroke-accent" d={firstMouth} strokeWidth="2" strokeLinecap="round" fill="none" />
          </g>
        </g>

        {/* Focused (turned toward the screen): the near ear is seen on the side of the head, in front of the shell, and the far one is hidden */}
        {turned && (
          <g className="ps-ear-near" transform="translate(67.6 39.5) scale(0.7 1) translate(-67.4 -39.5)">
            {/* the ear-cup, a disc with a recessed ring, a bright rim on its upper edge and a small light in the middle; seen at about 45 degrees, so it is squeezed
                sideways into an oval */}
            <circle cx="67.4" cy="39.5" r="4.5" fill="#8E95A2" stroke="#3A3F4C" strokeWidth="0.9" />
            <circle cx="67.4" cy="39.5" r="2.8" fill="#5F6674" />
            <circle className="mp-accent" cx="67.4" cy="39.5" r="1" opacity="0.9" />
            <path d="M64.2 36.9 A4.1 4.1 0 0 1 69.6 36.1" stroke="#fff" strokeWidth="0.8" strokeLinecap="round" fill="none" opacity="0.4" />
          </g>
        )}

        <g className={fade(sweat)}>
          <path className="mp-sweat" d="M74 22 C72 20, 70 23, 71 25 C72 27, 74 27, 75 25 C76 23, 76 22, 74 22 Z" fill="#38C2DE" />
        </g>

        {/* A thought forming, beside his head */}
        <g className={fade(doing === 'thinking')}>
          <g className="mp-thought">
            <circle className="mp-accent mp-dot mp-dot-1" cx="79" cy="23" r="1.4" />
            <circle className="mp-accent mp-dot mp-dot-2" cx="83.5" cy="17.5" r="1.9" />
            <circle className="mp-accent mp-dot mp-dot-3" cx="88.5" cy="11" r="2.5" />
          </g>
        </g>
        <g className={fade(doing === 'puzzled')}>
          <text className="mp-accent mp-q" x="79" y="22" fontSize="14" fontWeight="700" fontFamily="ui-monospace, Menlo, Consolas, monospace">?</text>
        </g>
        {/* Asleep: z's drift up */}
        <g className={fade(mood === 'asleep')}>
          <g className="mp-zzz" fontWeight="700" fontFamily="ui-monospace, Menlo, Consolas, monospace">
            <text className="mp-accent mp-z mp-z-1" x="76" y="26" fontSize="8">z</text>
            <text className="mp-accent mp-z mp-z-2" x="82" y="19" fontSize="10">z</text>
            <text className="mp-accent mp-z mp-z-3" x="89" y="11" fontSize="12">Z</text>
          </g>
        </g>
      </g>
    </g>
  );
}

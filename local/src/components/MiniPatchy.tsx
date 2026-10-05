import { useEffect, useState } from 'react';
import './__styles__/MiniPatchy.css';

// A small Patchy drawn straight from the full-size one in errAgent (PatchyEmptyState.tsx): same 100x100
// coordinates, same rounded dark head with a visor, glowing eyes, gray ears, accent antenna, smile, round
// jointed arms with ball hands and blocky legs. He sits on the top-right edge of the chat input with his
// blocky legs dangling over the field, tapping up and down, a laptop open in front of him.
//
// Idle: one hand rests on the laptop and the free arm hangs, and every few seconds it waves (the
// original's own idle wave animation). Working (Sonic responds): head tipped down, both hands on the
// keyboard tapping, code building line by line on the screen.
//
// Moods: idle (cyan), working (yellow like Patchy's "analyzing"), done (green, a check pops off the
// laptop and he hops), laugh (pink, when poked).
//
// Energy follows the user's emotional state (the same ceiling the backend puts on Sonic's own energy, see
// backend/utils/emotion_utils.py energy_tier): "open" is all of the above; "easing" (the user has shown a
// small lift) is gentler: slower legs, a rarer wave, a softer smile; "subdued" (they are low or
// frustrated and haven't lifted) is quiet: legs still, no wave, worried brows over wide eyes, a small frown,
// head tilted attentively, a muted color, and no giggling when poked (a soft nod instead).
//
// The SVG keeps the original's 100-unit coordinate space; SCALE turns a unit into CSS pixels.

const SCALE = 0.58;
const WIDTH = 100 * SCALE;
const HEIGHT = 102 * SCALE;

// The original's idle wave for the free (right) arm, as SMIL: hang, then wave a few times, then hang again.
const WAVE_TIMES = '0; 0.1; 0.2; 0.3; 0.4; 0.5; 0.6; 1';
const WAVE_SPLINES = '0.25 1 0.5 1; 0.25 1 0.5 1; 0.25 1 0.5 1; 0.25 1 0.5 1; 0.25 1 0.5 1; 0.25 1 0.5 1; 0.25 1 0.5 1';
const WAVE_ARM = `M 76 62 C 84 68, 84 76, 78 82;
  M 76 62 C 88 56, 94 42, 92 30;
  M 76 62 C 82 50, 86 36, 84 26;
  M 76 62 C 88 56, 94 42, 92 30;
  M 76 62 C 82 50, 86 36, 84 26;
  M 76 62 C 88 56, 94 42, 92 30;
  M 76 62 C 84 68, 84 76, 78 82;
  M 76 62 C 84 68, 84 76, 78 82`;

export type PatchyEnergy = 'open' | 'easing' | 'subdued';

interface MiniPatchyProps {
  /** True while Sonic is busy: Patchy codes. */
  working?: boolean;
  /** Bump this number to make Patchy celebrate (a turn just finished). */
  cheer?: number;
  /** How much energy Patchy shows, following the user's emotional state. Defaults to fully open. */
  energy?: PatchyEnergy;
}

export function MiniPatchy({ working = false, cheer = 0, energy = 'open' }: MiniPatchyProps) {
  const [laughing, setLaughing] = useState(false);
  const [nudged, setNudged] = useState(false);
  const [celebrating, setCelebrating] = useState(false);
  const [seenCheer, setSeenCheer] = useState(cheer);

  // Reacting to a prop change during render (instead of in an effect) is React's own recommended
  // pattern for "derive state when a prop changes".
  if (cheer !== seenCheer) {
    setSeenCheer(cheer);
    setCelebrating(true);
  }

  useEffect(() => {
    if (!celebrating) return;
    const timer = setTimeout(() => setCelebrating(false), 1600);
    return () => clearTimeout(timer);
  }, [celebrating, cheer]);

  useEffect(() => {
    if (!laughing) return;
    const timer = setTimeout(() => setLaughing(false), 1400);
    return () => clearTimeout(timer);
  }, [laughing]);

  useEffect(() => {
    if (!nudged) return;
    const timer = setTimeout(() => setNudged(false), 1200);
    return () => clearTimeout(timer);
  }, [nudged]);

  const mood = laughing ? 'laugh' : nudged ? 'soft' : celebrating && !working ? 'done' : working ? 'working' : 'idle';
  // Only idle has a free hand; otherwise both hands are on the keyboard.
  const bothHandsOnLaptop = mood !== 'idle';
  // SMIL ignores prefers-reduced-motion, so the wave is only drawn when motion is welcome.
  const wave = mood === 'idle' && energy !== 'subdued' && !(typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches);

  // A low or frustrated user doesn't get giggled at: poking him just earns a soft nod.
  const poke = () => (energy === 'subdued' ? setNudged(true) : setLaughing(true));
  const waveDur = energy === 'easing' ? '14s' : '8s';
  const mouth =
    energy === 'subdued' ? 'M45 43.5 Q50 40.5 55 43.5'
    : energy === 'easing' ? 'M44 41.5 Q50 44.5 56 41.5'
    : mood === 'done' ? 'M41 40 Q50 48 59 40'
    : 'M43 41 Q50 46 57 41';

  return (
    <button
      type="button"
      className={`mp-root mp-${mood} mp-e-${energy}`}
      onClick={poke}
      title={laughing ? 'Hehe!' : energy === 'subdued' ? 'SAAPP' : 'Poke SAAPP!'}
      aria-label={energy === 'subdued' ? 'SAAPP, the mascot.' : 'SAAPP, the mascot. Poke him.'}
    >
      <svg viewBox="0 0 100 102" width={WIDTH} height={HEIGHT} fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
        {/* A check pops off the laptop when he finishes */}
        {mood === 'done' && (
          <g className="mp-sent">
            <circle className="mp-accent" cx="72" cy="52" r="5" />
            <path d="M69.4 52.2 L71.2 54 L74.8 50" stroke="#0C1016" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" fill="none" />
          </g>
        )}

        <g className="mp-bob">
          {/* Blocky legs dangle over the input's edge and tap up and down; the torso covers the hips */}
          <g className="mp-leg mp-leg-l">
            <rect x="37" y="80" width="9" height="13" rx="2" fill="#8E95A2" />
            <rect x="35" y="91" width="12" height="7" rx="2" fill="#B7BECB" />
          </g>
          <g className="mp-leg mp-leg-r">
            <rect x="54" y="80" width="9" height="13" rx="2" fill="#8E95A2" />
            <rect x="53" y="91" width="12" height="7" rx="2" fill="#B7BECB" />
          </g>

          {/* Torso */}
          <rect x="34" y="56" width="32" height="30" rx="7" fill="#121316" stroke="#3A3F4C" strokeWidth="2" />

          {/* The laptop, open with its screen toward us so the coding is visible */}
          <rect x="36" y="58" width="28" height="19" rx="3" fill="#2C303B" stroke="#B7BECB" strokeWidth="1.2" />
          <rect x="38.4" y="60.4" width="23.2" height="14.2" rx="1.6" fill="#0C1016" />
          <rect className="mp-line mp-line-1" x="40.4" y="62.2" width="10" height="2.2" rx="1.1" fill="#4B5163" />
          <rect className="mp-accent mp-line mp-line-2" x="40.4" y="65.8" width="16" height="2.2" rx="1.1" />
          <rect className="mp-line mp-line-3" x="40.4" y="69.4" width="8" height="2.2" rx="1.1" fill="#4B5163" />
          <rect className="mp-accent mp-cursor" x="50" y="69.4" width="2.2" height="2.2" rx="0.5" />
          <rect x="31" y="76.5" width="38" height="5.5" rx="2.5" fill="#3A3F4C" stroke="#B7BECB" strokeWidth="1.2" />
          <rect x="46" y="77.2" width="8" height="1.4" rx="0.7" fill="#1F242D" />

          {/* Left arm: shoulder to the laptop's side, ball hand on it */}
          <path d="M24 62 C 16 70, 22 82, 32.5 78.5" stroke="#8E95A2" strokeWidth="4" strokeLinecap="round" fill="none" />
          <circle className="mp-hand mp-hand-l" cx="32.5" cy="78.5" r="3.5" fill="#8E95A2" />

          {/* Right arm: idle, it hangs and waves now and then; otherwise it joins in on the keyboard */}
          {bothHandsOnLaptop ? (
            <>
              <path d="M76 62 C 84 70, 78 82, 67.5 78.5" stroke="#8E95A2" strokeWidth="4" strokeLinecap="round" fill="none" />
              <circle className="mp-accent mp-hand mp-hand-r" cx="67.5" cy="78.5" r="3.5" />
            </>
          ) : (
            <>
              <path d="M 76 62 C 84 68, 84 76, 78 82" stroke="#8E95A2" strokeWidth="4" strokeLinecap="round" fill="none">
                {wave && (
                  <animate attributeName="d" dur={waveDur} repeatCount="indefinite" calcMode="spline" keyTimes={WAVE_TIMES} keySplines={WAVE_SPLINES} values={WAVE_ARM} />
                )}
              </path>
              <circle className="mp-accent" cx="78" cy="82" r="3.5">
                {wave && (
                  <>
                    <animate attributeName="cx" dur={waveDur} repeatCount="indefinite" calcMode="spline" keyTimes={WAVE_TIMES} keySplines={WAVE_SPLINES} values="78; 92; 84; 92; 84; 92; 78; 78" />
                    <animate attributeName="cy" dur={waveDur} repeatCount="indefinite" calcMode="spline" keyTimes={WAVE_TIMES} keySplines={WAVE_SPLINES} values="82; 30; 26; 30; 26; 30; 82; 82" />
                  </>
                )}
              </circle>
            </>
          )}

          {/* Head, tipped forward while he looks at the laptop */}
          <g className="mp-head">
            <line x1="50" y1="20" x2="50" y2="12" stroke="#8E95A2" strokeWidth="2.5" strokeLinecap="round" />
            <circle className="mp-accent mp-antenna" cx="50" cy="10" r="4" />

            <rect x="23" y="32" width="5" height="10" rx="2" fill="#8E95A2" />
            <rect x="72" y="32" width="5" height="10" rx="2" fill="#8E95A2" />

            <rect x="28" y="20" width="44" height="34" rx="10" fill="#121316" stroke="#3A3F4C" strokeWidth="2" />
            <rect x="33" y="25" width="34" height="24" rx="6" fill="#0C1016" stroke="#1F242D" />

            {nudged ? (
              <g className="mp-stroke-accent" strokeWidth="2.2" strokeLinecap="round" fill="none">
                <path d="M 38 36 Q 42 39 46 36" />
                <path d="M 54 36 Q 58 39 62 36" />
              </g>
            ) : laughing ? (
              <>
                <g className="mp-stroke-accent" strokeWidth="2.5" strokeLinecap="round" fill="none">
                  <path d="M 38 36 Q 42 30 46 36" />
                  <path d="M 54 36 Q 58 30 62 36" />
                </g>
                <path className="mp-accent" d="M41 39 Q50 50 59 39 Z" opacity="0.9" />
              </>
            ) : (
              <>
                <g className="mp-eyes">
                  <circle className="mp-accent" cx="42" cy="35" r="3.5" />
                  <circle className="mp-accent" cx="58" cy="35" r="3.5" />
                </g>
                {/* Worried brows, inner ends raised: he is concerned, not bored */}
                {energy === 'subdued' && (
                  <g className="mp-stroke-accent mp-brows" strokeWidth="1.8" strokeLinecap="round">
                    <path d="M37.5 31.5 L45.5 28.5" />
                    <path d="M62.5 31.5 L54.5 28.5" />
                  </g>
                )}
                <path className="mp-stroke-accent" d={mouth} strokeWidth="2" strokeLinecap="round" fill="none" />
              </>
            )}
          </g>
        </g>
      </svg>
    </button>
  );
}

export default MiniPatchy;

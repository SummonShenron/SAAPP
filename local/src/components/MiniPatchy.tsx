import { usePatchyMood, type PatchySignals } from '../hooks/usePatchyMood';
import { PatchyHead, PatchyRightArm } from './PatchyParts';
import { patchyRootClass, patchyTitle, type ArmPose } from './patchyLook';
import './__styles__/MiniPatchy.css';

// A small Patchy drawn straight from the full-size one in errAgent (PatchyEmptyState.tsx): same 100x100
// coordinates, same rounded dark head with a visor, glowing eyes, gray ears, accent antenna, smile, round
// jointed arms with ball hands and blocky legs. He sits on the top-right edge of the chat input with his
// blocky legs dangling over the field, tapping up and down, a laptop open in front of him.
//
// What he does follows what Sonic does (see patchyLook.ts for the rules):
//  - while a turn runs, his look follows the step it is on: THINKING (a hand to his head, a thought forming), SEARCHING (a magnifying glass
//    over his eye), PUZZLED (a shrug, one brow up, a sweat drop, a question mark when a query is rewritten or an answer reconsidered) and
//    WRITING (both hands on the keyboard, code building line by line);
//  - when a turn ends he celebrates (a check pops off the laptop and he hops), or, if it failed, his face turns sorry and red instead;
//  - he watches the box while you type, nods "got it" when you steer a running turn, and gets drowsy after a long quiet;
//  - the usage budget shows too: a sweat drop at the notice level, heavy lids when chat is throttled, asleep when it is paused;
//  - poke him and he giggles (pink), idle he waves now and then.
//
// Energy follows the user's emotional state (the same ceiling the backend puts on Sonic's own energy, see
// backend/utils/emotion_utils.py energy_tier): "open" is all of the above; "easing" (the user has shown a
// small lift) is gentler: slower legs, a rarer wave, a softer smile; "subdued" (they are low or
// frustrated and haven't lifted) is quiet: legs still, no wave, worried brows over wide eyes, a small frown,
// head tilted attentively, a muted color, no sweat drop, no alarm when something fails, and no giggling when poked (a soft nod instead).
//
// The SVG keeps the original's 100-unit coordinate space; SCALE turns a unit into CSS pixels.

export type { PatchyEnergy } from './patchyLook';

const SCALE = 0.58;
const WIDTH = 100 * SCALE;
const HEIGHT = 102 * SCALE;

export function MiniPatchy(props: PatchySignals) {
  const { energy = 'open', budget = 'ok' } = props;
  const { mood, look, steered, reading, poke } = usePatchyMood(props);

  const pose: ArmPose =
    mood === 'active' ? (look === 'thinking' ? 'think' : look === 'searching' ? 'search' : look === 'puzzled' ? 'puzzle' : 'type')
    : mood === 'idle' ? 'hang'
    : mood === 'watching' ? 'ready' // you are writing: his free hand comes down to hover over the keys, getting ready to answer
    : 'type'; // celebrating, laughing, nodding, sorry, drowsy, asleep: hands on the laptop
  const wave = mood === 'idle' && energy !== 'subdued' && budget !== 'throttled';
  const waveDur = energy === 'easing' ? 14 : 8;

  return (
    <button
      type="button"
      className={patchyRootClass({ mood, look, energy, budget, steered, reading })}
      onClick={poke}
      title={patchyTitle({ mood, look, energy })}
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

          {/* 3/4 Angled Laptop (Reversed Isometric Direction) */}
          <polygon points="34,77 64,70 74,76 44,83" fill="#2C303B" stroke="#B7BECB" strokeWidth="1.2" />
          <polygon points="42,76 58,72 68,76 48,80" fill="#1F242D" />

          <polygon points="48,51 72,58 69,78 45,71" fill="#2C303B" stroke="#B7BECB" strokeWidth="1.2" />
          <polygon points="50,54 70,60 67,75 47,69" fill="#0C1016" />

          {/* Code Lines on Screen (Reversed Isometric Slope) */}
          <line className="mp-line mp-line-1" x1="50" y1="60" x2="60" y2="63" stroke="#4B5163" strokeWidth="2" strokeLinecap="round" />
          <line className="mp-accent mp-line mp-line-2" x1="50" y1="64" x2="64" y2="68" strokeWidth="2" strokeLinecap="round" />
          <line className="mp-line mp-line-3" x1="50" y1="68.5" x2="58" y2="71" stroke="#4B5163" strokeWidth="2" strokeLinecap="round" />
          <rect className="mp-accent mp-cursor" x="59" y="68.5" width="2" height="2" fill="var(--mp-accent)" />

          {/* Left arm: shoulder to the laptop's side, ball hand on it */}
          <path d="M24 62 C 16 70, 22 82, 32.5 78.5" stroke="#8E95A2" strokeWidth="4" strokeLinecap="round" fill="none" />
          <circle className="mp-hand mp-hand-l" cx="32.5" cy="78.5" r="3.5" fill="#8E95A2" />


          <PatchyHead mood={mood} look={look} energy={energy} budget={budget} />

          {/* Right arm, in front of the head (a hand to it, a magnifying glass over the eye): idle, it hangs and waves now and then; typing, it joins in on the keyboard; otherwise it does what the look calls for */}
          <PatchyRightArm pose={pose} wave={wave} waveDur={waveDur} handClass={pose === 'type' ? 'mp-hand mp-hand-r' : undefined} />
        </g>
      </svg>
    </button>
  );
}

export default MiniPatchy;

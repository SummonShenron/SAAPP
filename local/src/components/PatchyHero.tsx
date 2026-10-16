import { usePatchyMood, type PatchySignals } from '../hooks/usePatchyMood';
import { PatchyHead, PatchyRightArm } from './PatchyParts';
import { patchyRootClass, patchyTitle, type ArmPose } from './patchyLook';
import './__styles__/PatchyHero.css';

// The full-size Patchy, standing: errAgent's PatchyEmptyState body (round torso, chest mark, blocky feet dangling a little, a backdrop glow)
// with the same head, expressions and moods as the small one on the chat input. He greets you on the empty chat, then hands over to the
// small one once a conversation starts. Idle he floats and waves now and then; he watches the box while you type, goes drowsy after a long
// quiet, sleeps when chat is paused, and giggles (or, for a low person, nods) when poked.

const LEFT_ARM: Record<'hang' | 'cheer' | 'hug', { d: string; x: number; y: number }> = {
  hang: { d: 'M24 62 C16 66, 16 76, 22 82', x: 22, y: 82 },
  cheer: { d: 'M 24 62 C 16 50, 14 36, 18 24', x: 18, y: 24 },
  hug: { d: 'M 24 62 C 28 75, 36 75, 43 70', x: 43, y: 70 },
};

export function PatchyHero({ size = 132, ...signals }: PatchySignals & { size?: number }) {
  const { energy = 'open', budget = 'ok' } = signals;
  const { mood, look, steered, reading, poke } = usePatchyMood(signals);

  const pose: ArmPose =
    mood === 'laugh' ? 'hug'
    : mood === 'done' ? 'cheer'
    : mood === 'active' ? (look === 'thinking' ? 'think' : look === 'searching' ? 'search' : look === 'puzzled' ? 'puzzle' : 'hang')
    : 'hang';
  const left = LEFT_ARM[mood === 'laugh' ? 'hug' : mood === 'done' ? 'cheer' : 'hang'];
  const wave = mood === 'idle' && energy !== 'subdued' && budget !== 'throttled';

  return (
    <button
      type="button"
      className={`${patchyRootClass({ mood, look, energy, budget, steered, reading })} mp-hero`}
      onClick={poke}
      title={patchyTitle({ mood, look, energy })}
      aria-label={energy === 'subdued' ? 'SAAPP, the mascot.' : 'SAAPP, the mascot. Poke him.'}
    >
      <svg viewBox="0 0 100 100" width={size} height={size} fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
        <g className="mp-bob">
          <circle className="mph-glow" cx="50" cy="52" r="40" />

          {/* A calm signal off the antenna, when he is just standing by */}
          <circle className="mph-radar mph-radar-1 mp-stroke-accent" cx="50" cy="10" r="4" fill="none" />
          <circle className="mph-radar mph-radar-2 mp-stroke-accent" cx="50" cy="10" r="4" fill="none" />

          {/* Feet */}
          <rect className="mph-foot mph-foot-l" x="35" y="85" width="12" height="6" rx="2" fill="#8E95A2" />
          <rect className="mph-foot mph-foot-r" x="53" y="85" width="12" height="6" rx="2" fill="#8E95A2" />

          {/* Torso and chest mark */}
          <rect x="34" y="56" width="32" height="30" rx="7" fill="#121316" stroke="#2A2A32" strokeWidth="2" />
          <rect x="43" y="63" width="14" height="14" rx="3" fill="#0C1016" stroke="#1F242D" />
          <path className="mp-stroke-accent" d="M50 66 V74 M46 70 H54" strokeWidth="2" strokeLinecap="round" opacity="0.6" />

          {/* Left arm */}
          <path d={left.d} stroke="#8E95A2" strokeWidth="4" strokeLinecap="round" fill="none" />
          <circle className={mood === 'laugh' || mood === 'done' ? 'mp-accent' : undefined} cx={left.x} cy={left.y} r="3.5" fill={mood === 'laugh' || mood === 'done' ? undefined : '#8E95A2'} />


          <PatchyHead mood={mood} look={look} energy={energy} budget={budget} />

          {/* Right arm, in front of the head: waves when idle, otherwise does what the look calls for */}
          <PatchyRightArm pose={pose} wave={wave} waveDur={energy === 'easing' ? 14 : 8} />
        </g>
      </svg>
    </button>
  );
}

export default PatchyHero;

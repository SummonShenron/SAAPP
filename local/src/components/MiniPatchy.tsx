import { useEffect, useRef, useState, type CSSProperties } from 'react';
import { usePatchyMood, type PatchySignals } from '../hooks/usePatchyMood';
import { useStretchRig } from '../hooks/useStretchRig';
import { useYawn } from '../hooks/useYawn';
import { PatchyHead, PatchyRightArm } from './PatchyParts';
import { loopVictory, motionAllowed, patchyRootClass, patchyTitle, type ArmPose } from './patchyLook';
import { STRETCH_STAGES, STRETCH_START_DELAY_MS, stretchFlags } from './patchyStretch';
import { gearPath } from './patchyGear';
import { LEFT_TYPE, RIGHT_HANG, SHOULDER_LEFT, SHOULDER_RIGHT, armPath } from './patchyRig';
import { HELLO_MS, HELLO_WAVE_SECONDS, SPARKLES, TADA_AFTER_MS, TADA_MS, sparklePath } from './patchyFlourish';
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
// Once a conversation has started (the first time he appears) and every time the page is refreshed on one, the first time he has nothing to do he stretches: he sets the laptop down on the input's edge, stands up,
// stretches with both arms over his head, sits back down and picks the laptop up again (patchyStretch.ts is the script, MiniPatchy.css draws it). Any turn, or you clicking into the box, cuts it short.
//
// The SVG keeps the original's 100-unit coordinate space; SCALE turns a unit into CSS pixels.

export type { PatchyEnergy } from './patchyLook';

const SCALE = 0.58;
const WIDTH = 100 * SCALE;
const HEIGHT = 102 * SCALE;
// The two gears that turn beside his head while the tool agent goes round its loop: a big one under a small one, on the left where nothing else is.
const GEAR_BIG = gearPath(12, 52, 9, 7, 9);
const GEAR_SMALL = gearPath(12, 34.5, 7, 5.4, 7);

export function MiniPatchy(props: PatchySignals) {
  const { energy = 'open', budget = 'ok' } = props;
  const { mood, look, steered, reading, poke } = usePatchyMood(props);

  // The stretch: a stage number while it plays (patchyStretch.ts), -1 when he is just sitting there. It starts the first time he is idle for a moment, once for as long as he is on the page, and stops the moment he has
  // anything else to do. The stage list is only looked at while he is idle, so a stale number is never drawn.
  const [stage, setStage] = useState(-1);
  const [stretchedOnce, setStretchedOnce] = useState(false);
  const idle = mood === 'idle';

  // Opening an older conversation: he waves hello first (the waving arm starts a fresh trip through its cycle, MiniPatchy.css gives him a little hop), then the stretch is armed again and plays after it. Worked out while
  // rendering, the way usePatchyMood notices a bump, so there is no frame in between.
  const [seenGreet, setSeenGreet] = useState(props.greet ?? 0);
  const [hello, setHello] = useState(false);
  if ((props.greet ?? 0) !== seenGreet) {
    setSeenGreet(props.greet ?? 0);
    if (motionAllowed()) { setHello(true); setStretchedOnce(false); setStage(-1); }
  }
  useEffect(() => {
    if (!hello) return undefined;
    const timer = window.setTimeout(() => setHello(false), HELLO_MS);
    return () => window.clearTimeout(timer);
  }, [hello]);

  useEffect(() => {
    if (!idle || hello || stretchedOnce || !motionAllowed()) return undefined;
    let timer: number | undefined;
    const step = (i: number) => {
      setStage(i);
      timer = window.setTimeout(() => {
        if (i + 1 < STRETCH_STAGES.length) step(i + 1);
        else { setStretchedOnce(true); setStage(-1); }
      }, STRETCH_STAGES[i].ms);
    };
    timer = window.setTimeout(() => step(0), STRETCH_START_DELAY_MS);
    return () => { window.clearTimeout(timer); setStage(-1); };
  }, [idle, hello, stretchedOnce]);

  // A long plan has just been finished (the page says so with `planDone`): once the celebration of the turn that finished it is over, a ta-da. 'wait' is the pause, 'on' the ta-da.
  const [seenPlan, setSeenPlan] = useState(props.planDone === true);
  const [tadaStage, setTadaStage] = useState<'off' | 'wait' | 'on'>('off');
  if ((props.planDone === true) !== seenPlan) {
    setSeenPlan(props.planDone === true);
    if (props.planDone === true && motionAllowed()) setTadaStage('wait');
  }
  useEffect(() => {
    if (tadaStage === 'off') return undefined;
    const timer = window.setTimeout(() => setTadaStage(tadaStage === 'wait' ? 'on' : 'off'), tadaStage === 'wait' ? TADA_AFTER_MS : TADA_MS);
    return () => window.clearTimeout(timer);
  }, [tadaStage]);
  const tada = tadaStage === 'on' && idle && energy !== 'subdued';
  const greeting = hello && idle;

  const stretch = stretchFlags(idle && !tada ? stage : -1);

  // The laptop and the two arms that carry it (patchyRig.ts): one state eased frame by frame, written straight to the drawing, so his hands are always on the laptop's corners while he lifts it, sets it down beside him and
  // later brings it back. When he is not stretching the rig settles at rest: the laptop on his lap, the typing hand on it, the right arm hanging.
  const lapRef = useRef<SVGGElement>(null);
  const leftArmRef = useRef<SVGPathElement>(null);
  const leftHandRef = useRef<SVGCircleElement>(null);
  const rightArmRef = useRef<SVGPathElement>(null);
  const rightHandRef = useRef<SVGCircleElement>(null);
  useStretchRig(stretch.rig, rig => {
    lapRef.current?.setAttribute('transform', `translate(${rig.lap[0]} ${rig.lap[1]})`);
    leftArmRef.current?.setAttribute('d', armPath(SHOULDER_LEFT, rig.left));
    leftHandRef.current?.setAttribute('cx', String(rig.left[4]));
    leftHandRef.current?.setAttribute('cy', String(rig.left[5]));
    rightArmRef.current?.setAttribute('d', armPath(SHOULDER_RIGHT, rig.right));
    rightHandRef.current?.setAttribute('cx', String(rig.right[4]));
    rightHandRef.current?.setAttribute('cy', String(rig.right[5]));
  });
  // Drowsy, he yawns every so often (patchyYawn.ts); in the tool loop, the gears turn.
  const yawning = useYawn(mood === 'drowsy');
  const looping = props.loop === true && mood === 'active';
  const victory = loopVictory(mood, props.loopCheer, energy);          // a tool loop just finished well: the big hop, an arm up, the gears fly off

  const pose: ArmPose =
    tada ? 'cheer'
    : stretch.stretching ? (stretch.reach ? 'cheer' : 'hang')
    : yawning ? 'yawn'
    : victory ? 'cheer'
    : mood === 'active' ? (look === 'thinking' ? 'think' : look === 'searching' ? 'search' : look === 'puzzled' ? 'puzzle' : 'type')
    : mood === 'idle' ? 'hang'
    : mood === 'praise' ? 'fistpump' // praise: right arm snaps up into the fist pump
    : mood === 'sad' ? 'hang' // sad: the arm hangs still
    : mood === 'watching' ? 'ready' // you are writing: his free hand comes down to hover over the keys, getting ready to answer
    : 'type'; // celebrating, laughing, nodding, sorry, drowsy, asleep: hands on the laptop
  const wave = mood === 'idle' && !stretch.stretching && energy !== 'subdued' && budget !== 'throttled';
  const waveDur = greeting ? HELLO_WAVE_SECONDS : energy === 'easing' ? 14 : 8;          // a hello starts the wave over, at the quick pace

  return (
    <button
      type="button"
      className={`${patchyRootClass({ mood, look, energy, budget, steered, reading })}${stretch.stretching ? ' mp-stretching' : ''}${stretch.down ? ' mp-down' : ''}${stretch.rigging ? ' mp-rigging' : ''}${stretch.stand ? ' mp-stand' : ''}${stretch.reach ? ' mp-reach' : ''}${yawning ? ' mp-yawn' : ''}${looping ? ' mp-looping' : ''}${victory ? ' mp-victory' : ''}${greeting ? ' mp-hello' : ''}${tada ? ' mp-tada' : ''}`}
      style={stretch.stretching ? ({ '--mp-lean': stretch.lean } as CSSProperties) : undefined}
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

        {/* The ta-da: sparkles burst around him */}
        {tada && (
          <g className="mp-sparkles mp-accent" aria-hidden="true">
            {SPARKLES.map((s, i) => <path key={i} className="mp-sparkle" d={sparklePath(s.x, s.y, s.r)} style={{ '--d': `${s.delay}s` } as CSSProperties} />)}
          </g>
        )}

        <g className="mp-bob">
          {/* Everything of him that stands up in the stretch is drawn in two layers (the legs and torso, then, after the laptop, his arms and head) that rise and lean together, from his hips. */}
          <g className="mp-rise">
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

          {/* His chest badge, exactly the original Patchy's (the full-size one): a small dark inset with a plus in his accent colour, in the middle of his chest. The laptop in front of him covers most of it, as it covers most of him. */}
          <rect x="43" y="63" width="14" height="14" rx="3" fill="#0C1016" stroke="#1F242D" />
          <path className="mp-stroke-accent" d="M50 66 V74 M46 70 H54" strokeWidth="2" strokeLinecap="round" opacity="0.6" />

          {/* Every arm that is not typing on the laptop is drawn BEHIND it (before it), so no arm ever crosses the front of the laptop, wherever it is: the two arms that carry it (drawn by the rig, hidden otherwise; each takes hold
              of a corner and goes with it, the right one reaching across him), then, after letting go, the left arm hanging and the left arm up over his head (the right arm, in front of his head below, is PatchyRightArm's). */}
          <g className="mp-rig mp-rig-back">
            <path ref={leftArmRef} d={armPath(SHOULDER_LEFT, LEFT_TYPE)} stroke="#8E95A2" strokeWidth="4" strokeLinecap="round" fill="none" />
            <circle ref={leftHandRef} cx={LEFT_TYPE[4]} cy={LEFT_TYPE[5]} r="3.5" fill="#8E95A2" />
            <path ref={rightArmRef} d={armPath(SHOULDER_RIGHT, RIGHT_HANG)} stroke="#8E95A2" strokeWidth="4" strokeLinecap="round" fill="none" />
            <circle ref={rightHandRef} className="mp-accent" cx={RIGHT_HANG[4]} cy={RIGHT_HANG[5]} r="3.5" />
          </g>
          {/* In the stretch it lets go of the laptop: it hangs while he stands, and goes up over his head (the right arm, below, is PatchyRightArm's cheer pose) */}
          <g className="mp-arm-free mp-arm-hang">
            <path d="M24 62 C 16 66, 16 76, 22 82" stroke="#8E95A2" strokeWidth="4" strokeLinecap="round" fill="none" />
            <circle cx="22" cy="82" r="3.5" fill="#8E95A2" />
          </g>
          <g className="mp-arm-free mp-arm-up">
            <path d="M24 62 C 16 50, 14 36, 18 24" stroke="#8E95A2" strokeWidth="4" strokeLinecap="round" fill="none" />
            <circle cx="18" cy="24" r="3.5" fill="#8E95A2" />
          </g>

          </g>

          {/* 3/4 Angled Laptop (Reversed Isometric Direction): in the stretch he sets it down on the edge beside him */}
          <g className="mp-lap" ref={lapRef}>
          <polygon points="34,77 64,70 74,76 44,83" fill="#2C303B" stroke="#B7BECB" strokeWidth="1.2" />
          <polygon points="42,76 58,72 68,76 48,80" fill="#1F242D" />

          <polygon points="48,51 72,58 69,78 45,71" fill="#2C303B" stroke="#B7BECB" strokeWidth="1.2" />
          <polygon points="50,54 70,60 67,75 47,69" fill="#0C1016" />

          {/* Code Lines on Screen (Reversed Isometric Slope) */}
          <line className="mp-line mp-line-1" x1="50" y1="60" x2="60" y2="63" stroke="#4B5163" strokeWidth="2" strokeLinecap="round" />
          <line className="mp-accent mp-line mp-line-2" x1="50" y1="64" x2="64" y2="68" strokeWidth="2" strokeLinecap="round" />
          <line className="mp-line mp-line-3" x1="50" y1="68.5" x2="58" y2="71" stroke="#4B5163" strokeWidth="2" strokeLinecap="round" />
          <rect className="mp-accent mp-cursor" x="59" y="68.5" width="2" height="2" fill="var(--mp-accent)" />
          </g>

          {/* The tool loop: two gears turn beside his head (the big one drives the small one the other way) */}
          <g className={`mp-fade mp-gears${looping || victory ? ' mp-on' : ''}${victory ? ' mp-gears-pop' : ''}`}>
            <path className="mp-gear mp-gear-big mp-stroke-accent" d={GEAR_BIG} strokeWidth="1.3" strokeLinejoin="round" fill="none" />
            <circle className="mp-stroke-accent" cx="12" cy="52" r="2.3" strokeWidth="1.3" fill="none" />
            <path className="mp-gear mp-gear-small mp-stroke-accent" d={GEAR_SMALL} strokeWidth="1.3" strokeLinejoin="round" fill="none" />
            <circle className="mp-stroke-accent" cx="12" cy="34.5" r="1.9" strokeWidth="1.3" fill="none" />
          </g>

          <g className="mp-rise">
          {/* Left arm: shoulder to the laptop's side, ball hand on it */}
          <g className="mp-arm-typing">
            <path d="M24 62 C 16 70, 22 82, 32.5 78.5" stroke="#8E95A2" strokeWidth="4" strokeLinecap="round" fill="none" />
            <circle className="mp-hand mp-hand-l" cx="32.5" cy="78.5" r="3.5" fill="#8E95A2" />
          </g>

          <PatchyHead mood={mood} look={look} energy={energy} budget={budget} scrunched={stretch.reach} yawning={yawning} />

          {/* Right arm, in front of the head (a hand to it, a magnifying glass over the eye): idle, it hangs and waves now and then; typing, it joins in on the keyboard; otherwise it does what the look calls for */}
          <g className="mp-rarm">
            <PatchyRightArm pose={pose} wave={wave} waveDur={waveDur} handClass={pose === 'type' ? 'mp-hand mp-hand-r' : undefined} />
          </g>

          </g>
        </g>
      </svg>
    </button>
  );
}

export default MiniPatchy;

import { useRef, useState } from 'react';
import { PatchyHead, PatchyRightArm } from './PatchyParts';
import './__styles__/MiniPatchy.css';
import './__styles__/PatchyAtLaptop.css';

// The landing page's Patchy: the same character that sits at the chat input (MiniPatchy), seen from a new angle. He is turned about 45 degrees toward the
// viewer's left and sitting on the floor, hunched forward over a thin silver laptop that is turned toward him: we see the back of its open lid and, beside it,
// the keyboard he is typing on. His legs
// are what they always are, two little nub feet and nothing else, held up toes to the sky with the laptop resting across them. The head, the face and the typing animation are the real ones (PatchyHead, and the
// "working" look in MiniPatchy.css); only the body and the laptop are redrawn for the new angle, in the same 100-unit space the other Patchys use.
//
// The turn is drawn the way the original draws one (see PatchyParts.tsx): the face slides across the shell and the far ear disappears behind it, the
// torso narrows, and the whole upper body leans over from the hips. Everything that moves stops under "reduce motion" (PatchyAtLaptop.css).
//
// Coordinates: the floor is y=94 and he sits on it, his hips at x=58; he faces left.

export function PatchyAtLaptop() {
  // Clicking him startles him: he stops typing and hops, still turned to the screen; when he lands he shakes his head, eyes squeezed shut, without changing his pose, then carries on.
  const [phase, setPhase] = useState<'idle' | 'jump' | 'wave'>('idle');
  const calmTimer = useRef<number | undefined>(undefined);
  const startle = () => {
    window.clearTimeout(calmTimer.current);
    setPhase('idle');
    // A frame later, so a second click mid-hop restarts the animation.
    requestAnimationFrame(() => setPhase('jump'));
  };
  const landed = (e: React.AnimationEvent) => {
    if (e.target !== e.currentTarget) return;
    setPhase('wave');
    calmTimer.current = window.setTimeout(() => setPhase('idle'), 2800);
  };
  const idle = phase === 'idle';

  return (
    <div className={`mp-root ps-root${idle ? ' mp-working' : ''}${phase === 'wave' ? ' ps-front' : ''}`} role="img" aria-label="Patchy, the Sonic Assistant robot, sitting on the floor and typing on his laptop">
      <svg viewBox="-8 0 112 96" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
        <defs>
          <radialGradient id="ps-glow" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor="var(--mp-accent)" stopOpacity="0.38" />
            <stop offset="100%" stopColor="var(--mp-accent)" stopOpacity="0" />
          </radialGradient>
          <clipPath id="ps-behind-body">
            <polygon points="-4,61 41.8,61 47.9,91 -4,91" />
          </clipPath>
          <linearGradient id="ps-lid" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stopColor="#F1F3F8" />
            <stop offset="1" stopColor="#BFC6D2" />
          </linearGradient>
        </defs>

        {/* A mug of something hot, on the floor behind him */}
        <g className="ps-mug">
          <path className="ps-steam ps-steam-1 mp-stroke-accent" d="M89 82 q-1.6 -2.2 0 -4.2 q1.6 -2 0 -4" strokeWidth="1" strokeLinecap="round" fill="none" />
          <path className="ps-steam ps-steam-2 mp-stroke-accent" d="M92 83 q-1.6 -2.2 0 -4.2 q1.6 -2 0 -4" strokeWidth="1" strokeLinecap="round" fill="none" />
          <rect x="86.5" y="85" width="8.4" height="9" rx="1.8" fill="#2C303B" stroke="#B7BECB" strokeWidth="1" />
          <path d="M94.9 87.4 q3.2 0.4 3.2 3 t-3.2 3" stroke="#B7BECB" strokeWidth="1.2" fill="none" strokeLinecap="round" />
        </g>

        <g className={`ps-jump${phase === 'jump' ? ' ps-startled' : ''}`} onClick={startle} onAnimationEnd={landed}>
        <g className="mp-bob">
          {/* Patchy has no legs, only two little nub feet, like the other Patchys (they float below the torso, with nothing joining them). Here he is sitting
              on the floor with his feet up, toes to the sky, and the laptop rests across them (they are drawn before it). */}
          <g className="ps-foot ps-foot-far">
            <g transform="translate(6 0) rotate(-8 12.5 94)">
              <rect x="8" y="82" width="9" height="12" rx="3.2" fill="#8E95A2" />
              <rect x="10" y="84" width="1.8" height="7" rx="0.9" fill="#fff" opacity="0.24" />
            </g>
          </g>
          {/* From the hips up he leans forward, toward the laptop */}
          <g className="ps-upper">
            {/* Torso, narrowed by the turn, with the chest mark on its near side */}
            <rect x="44" y="64" width="29" height="30" rx="7" fill="#121316" stroke="#3A3F4C" strokeWidth="2" />
            <path d="M67 69 V89" stroke="#3A3F4C" strokeWidth="1.2" strokeLinecap="round" opacity="0.7" />
            <path className="mp-stroke-accent" d="M52 74 V82 M48 78 H56" strokeWidth="2" strokeLinecap="round" opacity="0.6" />

            {/* The light of the screen he is looking at, on his chest and face */}
            <ellipse className="ps-glow" cx="44" cy="62" rx="22" ry="17" fill="url(#ps-glow)" />


            {/* The real head, bowed over the screen (PatchyAtLaptop.css slides the face across the shell and hides the far ear) */}
            <g className="ps-head">
              <g transform="translate(8 8)">
                <PatchyHead mood={phase === 'wave' ? 'idle' : 'active'} look="writing" energy="open" budget="ok" focused={idle} turned mouthless={phase === 'jump'} />
              </g>
            </g>

            {/* The arm he waves with once he has turned to us: the same rubber arm the chat Patchy waves with (PatchyRightArm), moved from its shoulder at (76, 62) to his at (71, 70) */}
            <g className="ps-wave-arm" transform="translate(-5 8)">
              <PatchyRightArm pose="hang" wave={phase === 'wave'} waveDur={3.6} />
            </g>
          </g>

          {/* The laptop, open, a thin silver one, turned to face him (like the reference photo): the hinge runs down to the right, the lid stands on it with its
              back to us, and the keyboard deck runs away from it toward Patchy, who is typing on it. Drawn on a skewed plane: the hinge is H0 to H1, and the
              deck extends from it by D = (18, -6). */}
          <g className="ps-laptop" transform="translate(-6 -1)">
            {/* the deck, with a little thickness on its front edge */}
            <polygon points="34,87 52,81 52,82.7 34,88.7" fill="#8D95A3" />
            <polygon points="16,80 34,87 52,81 34,74" fill="#BCC2CD" stroke="#98A0AD" strokeWidth="0.8" strokeLinejoin="round" />
            <g transform="matrix(18 7 18 -6 16 80)">
              <rect x="0.07" y="0.14" width="0.86" height="0.62" rx="0.04" fill="#2B2F37" />
              {[0, 1, 2, 3].map(row => [0, 1, 2, 3, 4, 5, 6].map(col => (
                <rect key={`${row}-${col}`} x={0.1 + col * 0.115} y={0.19 + row * 0.14} width="0.095" height="0.1" rx="0.02" fill="#4A505C" />
              )))}
              <rect x="0.37" y="0.82" width="0.26" height="0.13" rx="0.03" fill="#D2D8E1" />
            </g>

            {/* his arms start at his shoulders, one on each side of the chest, and reach the keys without crossing: the far (left) shoulder, half hidden behind his body (drawn above), to the hand on
                the left (accent colour), and the near one sweeping across the front of him to the hand on the right. (This group is not leaned, so the shoulders
                are the leaning torso's shoulders worked out by hand.) */}
            {/* The far arm (to the accent-coloured hand) is in front of the laptop but behind his body: it is cut off along the left edge of his torso (the line
                below, worked out from the lean), so its shoulder is half hidden by him and the arm comes out from behind. */}
            <g clipPath="url(#ps-behind-body)">
              <path d="M46 75.6 C 43 75, 40 75.6, 38 77" stroke="#656C79" strokeWidth="3.4" strokeLinecap="round" fill="none" />
              <circle cx="45.4" cy="75.6" r="2.4" fill="#656C79" />
            </g>
            <g className="ps-near-arm">
              <path d="M71.2 69.6 C 75 82, 58 88.5, 47.5 82.2" stroke="#7B8290" strokeWidth="3.4" strokeLinecap="round" fill="none" />
              <circle cx="71.2" cy="69.6" r="2.2" fill="#7B8290" />
            </g>
            <circle className="mp-hand mp-hand-r" cx="37.2" cy="77.4" r="3.2" fill="#9DA4B1" stroke="#2B2F37" strokeWidth="0.8" />
            <circle className="mp-accent mp-hand mp-hand-l ps-near-hand" cx="45" cy="81.6" r="3.3" stroke="#6F7684" strokeWidth="0.7" />

            {/* the lid, standing on the hinge and leaning back, its back to us, a patch sticker on it */}
            <polygon points="16,80 34,87 31,65 13,58" fill="url(#ps-lid)" stroke="#AEB6C3" strokeWidth="1.4" strokeLinejoin="round" />
            <polygon points="16.6,78 19.2,79 16.6,60.6 14.7,59.9" fill="#fff" opacity="0.38" />
            <path d="M16 80 L34 87" stroke="#666D79" strokeWidth="1.5" strokeLinecap="round" />
            <g transform="rotate(21 23.5 72.5)">
              <rect x="17" y="69.8" width="13" height="5.4" rx="2.7" fill="var(--mp-accent)" />
              <rect x="21.5" y="70.5" width="4" height="4" rx="1" fill="#F8DCC0" />
            </g>
          </g>

          {/* His near foot (his left) is in front of the laptop, where it would be: the laptop rests across the far foot and is propped on this one */}
          <g className="ps-foot ps-foot-front">
            <g transform="translate(6 0) rotate(-8 31 94)">
              <rect x="26.5" y="82" width="9" height="12" rx="3.2" fill="#8E95A2" />
              <rect x="28.5" y="84" width="1.8" height="7" rx="0.9" fill="#fff" opacity="0.24" />
            </g>
          </g>

          {/* Bits of code lifting off as he types */}
          <g className="ps-bits mp-accent">
            <rect className="ps-bit ps-bit-1" x="15" y="62" width="2.2" height="2.2" rx="0.5" />
            <rect className="ps-bit ps-bit-2" x="21" y="64" width="1.8" height="1.8" rx="0.5" />
            <rect className="ps-bit ps-bit-3" x="26" y="66" width="2.4" height="2.4" rx="0.5" />
            <rect className="ps-bit ps-bit-4" x="18" y="61" width="1.6" height="1.6" rx="0.5" />
          </g>
        </g>
        </g>
      </svg>
    </div>
  );
}

export default PatchyAtLaptop;

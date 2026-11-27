import React, { useEffect, useRef } from 'react';
import { useReducedMotion } from './useReducedMotion';
import { LOGO_ORANGE } from './logoMarkData';

// A double helix loader: two strands that twist round each other, a row of dots on each strand, and two comets that run down the strands. Each dot pulses (swells, then settles) as a comet passes it, so the pulse travels down the helix the way the other loader animations do.
const VB_W = 60;
const VB_H = 120;
const AMP = 16;             // how far each strand swings either side of the middle
const TURNS = 1.5;          // how many full twists the helix makes top to bottom
const DOTS = 3;             // dots on each strand (3 per strand, 6 in all, like the logo)
const DOT_R = 3.2;
const COMET_PERIOD_MS = 1800; // one comet's trip from the top of the helix to the bottom
const TAIL = 6;             // circles in each comet's tail
const TAIL_STEP = 0.035;    // how far apart (0 to 1 of the helix) the tail circles sit
const PULSE_WIDTH = 26;     // how narrow the pulse is around the comet's head (bigger is narrower)
const PULSE_SIZE = 0.6;     // how much a dot swells when the comet reaches it
const STRANDS = [0, 1];
const MARGIN = 0.08;        // top and bottom margin, as a share of the height
const SAMPLES = 48;

/** Where strand `s` is across the drawing at height `f` (0 top to 1 bottom). The second strand is half a turn out of step with the first. */
export function strandX(s: number, f: number): number {
  return VB_W / 2 + AMP * Math.sin(2 * Math.PI * TURNS * f + s * Math.PI);
}

/** Height (0 to 1) of dot `i` on the helix. */
export function dotFrac(i: number): number {
  return MARGIN + (i / (DOTS - 1)) * (1 - 2 * MARGIN);
}

/** How far dot `i` is swollen at comet head position `head` (0 to 1): 1 when the comet is on it, settling to 0 as the comet moves on. */
export function pulseAt(head: number, i: number): number {
  const d = head - dotFrac(i);
  return Math.exp(-PULSE_WIDTH * d * d * 4);
}

/** The comet's head on strand `s` at time `t` (ms): it starts from the top, both comets start together, so they pulse in step. */
export function cometHead(t: number, _s: number): number {
  return (t / COMET_PERIOD_MS) % 1;
}

function strandPath(s: number): string {
  const points: string[] = [];
  for (let k = 0; k <= SAMPLES; k += 1) {
    const f = MARGIN + (k / SAMPLES) * (1 - 2 * MARGIN);
    points.push(`${k === 0 ? 'M' : 'L'}${strandX(s, f).toFixed(2)} ${(f * VB_H).toFixed(2)}`);
  }
  return points.join(' ');
}

interface Props {
  /** The loader's height in pixels; the width keeps its own proportions. */
  size?: number;
  /** Draw as if the person had asked for reduced motion (the dev preview and tests). */
  reducedMotion?: boolean;
  className?: string;
}

const HelixLogo: React.FC<Props> = ({ size = 120, reducedMotion, className }) => {
  const reduced = useReducedMotion(reducedMotion);
  const svgRef = useRef<SVGSVGElement>(null);
  const dotRefs = useRef<Array<SVGCircleElement | null>>([]);
  const tailRefs = useRef<Array<SVGCircleElement | null>>([]);

  useEffect(() => {
    const start = performance.now();
    let frame = 0;
    const draw = (now: number) => {
      STRANDS.forEach(s => {
        const head = cometHead(now, s);
        for (let j = 0; j < TAIL; j += 1) {
          const f = head - j * TAIL_STEP;
          const circle = tailRefs.current[s * TAIL + j];
          if (!circle) continue;
          const visible = f >= 0;
          circle.setAttribute('cx', String(strandX(s, Math.max(0, f))));
          circle.setAttribute('cy', String(Math.max(0, f) * VB_H));
          circle.setAttribute('opacity', visible ? String((1 - j / TAIL) * 0.9) : '0');
        }
        for (let i = 0; i < DOTS; i += 1) {
          const dot = dotRefs.current[s * DOTS + i];
          if (!dot) continue;
          const f = dotFrac(i);
          const r = DOT_R * (1 + PULSE_SIZE * pulseAt(head, i));
          dot.setAttribute('cx', String(strandX(s, f)));
          dot.setAttribute('cy', String(f * VB_H));
          dot.setAttribute('r', r.toFixed(2));
        }
      });
      if (!reduced) frame = requestAnimationFrame(draw);
    };
    if (reduced) {
      draw(0);
    } else {
      frame = requestAnimationFrame(draw);
    }
    return () => cancelAnimationFrame(frame);
  }, [reduced]);

  return (
    <svg
      ref={svgRef}
      className={`helix-logo${className ? ` ${className}` : ''}`}
      viewBox={`0 0 ${VB_W} ${VB_H}`}
      width={Math.round((size * VB_W) / VB_H)}
      height={size}
      aria-hidden="true"
      focusable="false"
      style={{ color: `var(--accent-primary, ${LOGO_ORANGE})` }}
    >
      {STRANDS.map(s => (
        <path key={`strand-${s}`} d={strandPath(s)} fill="none" stroke="currentColor" strokeWidth={1.5} opacity={0.35} />
      ))}
      {STRANDS.map(s => Array.from({ length: TAIL }, (_, j) => (
        <circle key={`tail-${s}-${j}`} ref={el => { tailRefs.current[s * TAIL + j] = el; }} r={2.2} fill="currentColor" opacity="0" />
      )))}
      {STRANDS.map(s => Array.from({ length: DOTS }, (_, i) => (
        <circle key={`dot-${s}-${i}`} ref={el => { dotRefs.current[s * DOTS + i] = el; }} r={DOT_R} fill="currentColor" />
      )))}
    </svg>
  );
};

export default HelixLogo;

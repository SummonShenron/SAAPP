import React, { useEffect, useRef } from 'react';
import { LOGO_DASH, LOGO_DASH_PERIOD, LOGO_EDGES, LOGO_NODES, LOGO_STROKE, LOGO_VIEWBOX } from './logoMarkData';
import { RESTING_POSE, TRAIL_SLOTS, poseAt, type LogoPose } from './thinkingLogoMotion';
import { useReducedMotion } from './useReducedMotion';
import './__styles__/TracePanel.css';

const [, , VB_W, VB_H] = LOGO_VIEWBOX.split(' ').map(Number);

// At the small size this is drawn at, the mark's own thin line would be a hair, so the line and the dots are a little heavier than the brand drawing (the same reason the favicon is its own drawing).
const LINE_BOOST = 1.5;
const DOT_BOOST = 1.2;
const RUNNER_CORE = 3.2;
const RUNNER_HALO = 7.5;
const TRAIL_CORE_WIDTH = 2;
// All seven of the mark's edges are drawn FULL and solid at rest, including the two that are dashed in the brand mark (up to the crest, and across the middle): the loader has no dashes of its own. The dashes
// exist only as the trail behind the runner.
const SLOTS = Array.from({ length: TRAIL_SLOTS }, (_, i) => i);

interface Props {
  /** The mark's height in pixels; the width keeps its own proportions. */
  size?: number;
  /** Draw as if the person had asked for reduced motion (the dev preview and tests; the app leaves it to the system setting). */
  reducedMotion?: boolean;
  /** The first step has arrived: the mark fades away (the steps take its place). */
  leaving?: boolean;
  className?: string;
}

/** A line through some points (the circle, the straight line, the infinity loop, or a piece of the trail along one of them) as an SVG path. */
const pathOf = (points: LogoPose['curve']['points']) => points.map(([x, y], i) => `${i === 0 ? 'M' : 'L'}${x} ${y}`).join(' ');

/**
 * The Sonic mark as the "thinking" loader (the maths and the timings are in thinkingLogoMotion.ts). It crouches, springs and lands; the dots break apart into a circle and go round it, the circle unrolls into a
 * line, the line becomes an infinity loop, then the dots become a star, a tree and a ladder (real graph shapes, joined by links), and fly back into the logo. All the way through, in every stage, a bright runner goes from dot to dot with a flowing dashed line behind it, along whatever the dots
 * are on, and a dot pulses (stretches, squashes and sends out a ring) only when the runner reaches it. It is drawn frame by frame straight into the drawing (no React state per frame). Decoration only (aria-hidden): "Thinking..." or the step's own words beside it say what is going
 * on. Under reduced motion it is the plain mark, still. The colour is the page's accent.
 */
const ThinkingLogo: React.FC<Props> = ({ size = 48, reducedMotion, leaving = false, className }) => {
  const reduced = useReducedMotion(reducedMotion);
  const markRef = useRef<SVGGElement>(null);
  const edgesRef = useRef<SVGGElement>(null);
  const edgeRefs = useRef<Array<SVGLineElement | null>>([]);
  const curveRef = useRef<SVGPathElement>(null);
  const dotRefs = useRef<Array<SVGEllipseElement | null>>([]);
  const ringRefs = useRef<Array<SVGCircleElement | null>>([]);
  const trailRefs = useRef<Array<SVGGElement | null>>([]);
  const runnerRef = useRef<SVGGElement>(null);

  useEffect(() => {
    const apply = (pose: LogoPose) => {
      markRef.current?.setAttribute('transform', pose.transform);
      // the links join the dots wherever the dots are (the logo's own lines, or a star's, a tree's, a ladder's), and fade while the dots move from one shape to the next
      edgesRef.current?.setAttribute('opacity', String(pose.edgeOpacity));
      if (pose.edgeOpacity > 0) {
        pose.links.forEach((link, index) => {
          const line = edgeRefs.current[index];
          if (!line) return;
          line.setAttribute('visibility', link ? 'visible' : 'hidden');
          if (!link) return;
          line.setAttribute('x1', String(pose.dots[link[0]].x));
          line.setAttribute('y1', String(pose.dots[link[0]].y));
          line.setAttribute('x2', String(pose.dots[link[1]].x));
          line.setAttribute('y2', String(pose.dots[link[1]].y));
        });
      }
      curveRef.current?.setAttribute('opacity', String(pose.curve.opacity));
      if (pose.curve.opacity > 0) curveRef.current?.setAttribute('d', pathOf(pose.curve.points));
      LOGO_NODES.forEach(([, , radius], index) => {
        const dot = pose.dots[index];
        const body = dotRefs.current[index];
        body?.setAttribute('cx', String(dot.x));
        body?.setAttribute('cy', String(dot.y));
        body?.setAttribute('rx', String(radius * DOT_BOOST * dot.scaleX));
        body?.setAttribute('ry', String(radius * DOT_BOOST * dot.scaleY));
        const ring = ringRefs.current[index];
        ring?.setAttribute('cx', String(dot.x));
        ring?.setAttribute('cy', String(dot.y));
        ring?.setAttribute('r', String(radius * DOT_BOOST * dot.ringScale));
        ring?.setAttribute('opacity', String(dot.ringOpacity));
      });
      pose.trail.forEach((piece, slot) => {
        const group = trailRefs.current[slot];
        if (!group) return;
        group.setAttribute('opacity', String(piece.opacity));
        if (piece.opacity > 0) {
          const d = pathOf(piece.points);
          group.querySelectorAll('path').forEach(path => path.setAttribute('d', d));
        }
      });
      runnerRef.current?.setAttribute('transform', `translate(${pose.runner.x} ${pose.runner.y})`);
      runnerRef.current?.setAttribute('opacity', String(pose.runner.opacity));
    };
    if (reduced) {
      apply(RESTING_POSE);
      return undefined;
    }
    const start = performance.now();
    let frame = 0;
    const tick = (now: number) => {
      apply(poseAt(now - start));
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [reduced]);

  // each piece of trail is drawn from the dot the runner left to the one it is heading for, so its dashes flow the way the runner goes (the dashes move toward a line's end as the offset goes down)
  const flowing = reduced ? undefined : 'logo-flow';
  const flow = { '--logo-flow-to': `${-LOGO_DASH_PERIOD}px` } as React.CSSProperties;

  return (
    <svg
      className={`thinking-logo${leaving ? ' thinking-logo-out' : ''}${className ? ` ${className}` : ''}`}
      viewBox={LOGO_VIEWBOX}
      width={Math.round((size * VB_W) / VB_H)}
      height={size}
      aria-hidden="true"
      focusable="false"
    >
      <g ref={markRef} transform={RESTING_POSE.transform}>
        <g ref={edgesRef}>
          {LOGO_EDGES.map(([from, to], index) => (
            <line key={index} ref={element => { edgeRefs.current[index] = element; }} x1={LOGO_NODES[from][0]} y1={LOGO_NODES[from][1]} x2={LOGO_NODES[to][0]} y2={LOGO_NODES[to][1]} stroke="currentColor" strokeWidth={LOGO_STROKE * LINE_BOOST} />
          ))}
        </g>
        {/* the line through the dots once the logo has broken apart: a circle, a straight line, an infinity loop */}
        <path ref={curveRef} d="" fill="none" stroke="currentColor" strokeWidth={LOGO_STROKE * LINE_BOOST} strokeLinejoin="round" strokeLinecap="round" opacity="0" />
        {/* the trail: dashes in the accent, with a bright thin core, so they show over a solid line as well as over the empty page */}
        {SLOTS.map(slot => (
          <g key={`trail-${slot}`} ref={element => { trailRefs.current[slot] = element; }} opacity="0">
            <path d="" fill="none" stroke="currentColor" strokeWidth={LOGO_STROKE * LINE_BOOST} strokeDasharray={LOGO_DASH} strokeLinejoin="round" className={flowing} style={flow} />
            <path d="" fill="none" stroke="#fff" strokeWidth={TRAIL_CORE_WIDTH} strokeDasharray={LOGO_DASH} strokeLinejoin="round" className={flowing} style={flow} />
          </g>
        ))}
        {LOGO_NODES.map(([x, y, radius], index) => (
          <circle key={`ring-${index}`} ref={element => { ringRefs.current[index] = element; }} cx={x} cy={y} r={radius * DOT_BOOST} fill="none" stroke="currentColor" strokeWidth="2" opacity="0" />
        ))}
        {LOGO_NODES.map(([x, y, radius], index) => (
          <ellipse key={index} ref={element => { dotRefs.current[index] = element; }} cx={x} cy={y} rx={radius * DOT_BOOST} ry={radius * DOT_BOOST} fill="currentColor" />
        ))}
        {/* the runner: a soft halo round a bright core, drawn over the dots */}
        <g ref={runnerRef} opacity="0" transform={`translate(${LOGO_NODES[0][0]} ${LOGO_NODES[0][1]})`}>
          <circle r={RUNNER_HALO} fill="currentColor" opacity="0.35" />
          <circle r={RUNNER_CORE} fill="#fff" />
        </g>
      </g>
    </svg>
  );
};

export default ThinkingLogo;

import { useEffect, useRef } from 'react';
import { glowTimeline } from './inputGlowPath';

// The comet that circles the message box while it is hovered or focused (styles in __styles__/AppShell.css, ".input-glow").
//
// A rounded rectangle outline drawn as a stack of dashes that all end at the same point: the short ones are the bright head, the longer ones the
// dimmer tail, so together they read as one tail that fades behind a head. They share one animation (the dash offset). `pathLength` makes every length
// a share of the whole outline (100 = once around), so the same dashes fit any width of box.
//
// The pace changes along the way (inputGlowPath.ts): quick along the long straight edges, slow through the rounded ends, easing between. That depends
// on the box's width and height, so the animation is built here from the measured size and rebuilt when it changes (the box grows with what is typed),
// and it runs only while the box is hovered or focused.
const SEGMENTS = 8;
const TAIL = 22; // the longest dash, as a share of the way round
const RADIUS = 25; // the box's corner radius in CSS (index.css .chat-textarea)

export default function InputGlow() {
  const svg = useRef<SVGSVGElement>(null);

  useEffect(() => {
    const el = svg.current;
    const host = el?.parentElement;
    if (!el || !host || typeof el.animate !== 'function') return;
    const rects = Array.from(el.querySelectorAll('rect'));
    let animations: Animation[] = [];
    let pauseTimer: ReturnType<typeof setTimeout> | undefined;
    let builtFor = '';

    const active = () => host.matches(':hover, :focus-within');
    const build = () => {
      const width = el.clientWidth - 2; // the outline is inset by half its stroke on each side
      const height = el.clientHeight - 2;
      if (width <= 0 || height <= 0) return;
      // Rebuilding restarts the animation from where it was read, so a rebuild for a size that has not changed (the observer also fires once as it starts, and
      // on sub-pixel noise) could hold the comet at its start. Only a real change of size needs one.
      const size = `${width}x${height}`;
      if (size === builtFor && animations.length) return;
      builtFor = size;
      const previous = animations.map(a => a.currentTime);
      animations.forEach(a => a.cancel());
      const { durationMs, keyframes } = glowTimeline(width, height, RADIUS);
      animations = rects.map((rect, i) => {
        const len = TAIL * (1 - i / SEGMENTS);
        const animation = rect.animate(
          keyframes.map(k => ({ strokeDashoffset: `${len - k.p}px`, offset: k.t, easing: 'linear' })),
          { duration: durationMs, iterations: Infinity },
        );
        // keep the place in the lap across a resize (typing makes the box taller), as far as the lap's length allows
        const carried = Number(previous[i]);
        if (Number.isFinite(carried)) animation.currentTime = carried % durationMs;
        if (!active()) animation.pause();
        return animation;
      });
    };

    const run = () => {
      clearTimeout(pauseTimer);
      animations.forEach(a => a.play());
    };
    // After the fade-out (0.6s), nothing is visible, so stop spending frames on it.
    const rest = () => {
      clearTimeout(pauseTimer);
      pauseTimer = setTimeout(() => { if (!active()) animations.forEach(a => a.pause()); }, 700);
    };

    build();
    const observer = new ResizeObserver(build);
    observer.observe(el);
    host.addEventListener('mouseenter', run);
    host.addEventListener('focusin', run);
    host.addEventListener('mouseleave', rest);
    host.addEventListener('focusout', rest);
    return () => {
      clearTimeout(pauseTimer);
      observer.disconnect();
      host.removeEventListener('mouseenter', run);
      host.removeEventListener('focusin', run);
      host.removeEventListener('mouseleave', rest);
      host.removeEventListener('focusout', rest);
      animations.forEach(a => a.cancel());
    };
  }, []);

  return (
    <svg ref={svg} className="input-glow" aria-hidden="true" focusable="false">
      {Array.from({ length: SEGMENTS }, (_, i) => {
        const len = TAIL * (1 - i / SEGMENTS);
        // i = 0 is the longest and faintest, i = SEGMENTS - 1 the shortest and brightest: the colour runs tail to head with it.
        const t = i / (SEGMENTS - 1);
        return (
          <rect
            key={i}
            pathLength={100}
            strokeDasharray={`${len} ${100 - len}`}
            style={{ ['--len' as string]: len, ['--t' as string]: t } as React.CSSProperties}
          />
        );
      })}
    </svg>
  );
}

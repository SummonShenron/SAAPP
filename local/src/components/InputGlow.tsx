import { useEffect, useRef, useState } from 'react';
import { glowTimeline, headPosition, sweepTimeline, type GlowTimeline } from './inputGlowPath';

// The comet that circles the message box while it is hovered or focused (styles in __styles__/AppShell.css, ".input-glow").
//
// A rounded rectangle outline drawn as a stack of dashes that all end at the same point: the short ones are the bright head, the longer ones the
// dimmer tail, so together they read as one tail that fades behind a head. They share one animation (the dash offset). `pathLength` makes every length
// a share of the whole outline (100 = once around), so the same dashes fit any width of box.
//
// The pace changes along the way (inputGlowPath.ts): quick along the long straight edges, slow through the rounded ends, easing between. That depends
// on the box's width and height, so the animation is built here from the measured size and rebuilt when it changes (the box grows with what is typed),
// and it runs only while Sonic is working (the running prop).
const SEGMENTS = 8;
const TAIL = 16; // the longest dash, as a share of the way round (a shorter tail puts less orange on screen at once)
const RADIUS = 25; // the box's corner radius in CSS (index.css .chat-textarea)
const SWEEP_TRAVEL = 90; // the focus sweep completes the ring when its head has gone this far round the outline from where it started (100 = once round)

export default function InputGlow({ running }: { running: boolean }) {
  const svg = useRef<SVGSVGElement>(null);
  // Read by the animation's pause/resume logic, which is set up once on mount.
  const runningRef = useRef(running);
  runningRef.current = running;
  const control = useRef<{ run: () => void; rest: () => void } | undefined>(undefined);
  const [focused, setFocused] = useState(false);
  const [settled, setSettled] = useState(false); // the sweep has closed into the ring

  useEffect(() => {
    const el = svg.current;
    const host = el?.parentElement;
    if (!el || !host || typeof el.animate !== 'function') return;
    const rects = Array.from(el.querySelectorAll('.glow-comet rect'));
    const sweepRects = Array.from(el.querySelectorAll('.glow-sweep rect'));
    let animations: Animation[] = [];
    let sweeps: Animation[] = [];
    let pauseTimer: ReturnType<typeof setTimeout> | undefined;
    let builtFor = '';
    let hasFocus = host.matches(':focus-within');
    // The box is focused on its own when the page loads, which is not the person choosing it, so the sweep and the ring wait for their first click or key press
    // anywhere on the page. After that, every focus counts, including the one the box takes back when a message is sent.
    let armed = false;
    let showing = false; // focused, and armed: the sweep or the ring is meant to be on screen
    let timeline: GlowTimeline | null = null;
    let sweepStart = 0;  // where on the outline (0 to 100) the sweep's head starts
    let wasRunning = false;

    const active = () => runningRef.current;
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
      timeline = glowTimeline(width, height, RADIUS);
      const { durationMs, keyframes } = timeline;
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

      buildSweeps(sweeps.length ? Number(sweeps[0].currentTime) : 0);
    };

    // The focus sweep: the same comet (a stack of dashes ending at the head, fading toward the tail, paced like the lap), but its tail lags behind the head and the
    // whole thing lengthens from TAIL to the full outline, so the head catches its own tail and the ring closes. The head keeps the comet's pace and the sweep ends when
    // it has gone SWEEP_TRAVEL of the way round; every dash grows from its comet length to the full outline over that stretch, the short orange ones the fastest, so the
    // orange catches up with the whole tail and the ring is complete, and orange, as the head arrives. It starts at `sweepStart` on the outline: the top when the box is
    // focused, and wherever the comet's head was when a request finished (so the ring is drawn from the comet, not from a different place).
    const buildSweeps = (elapsedMs: number) => {
      if (!timeline) return;
      const sweep = sweepTimeline(timeline, sweepStart, SWEEP_TRAVEL);
      sweeps.forEach(a => a.cancel());
      sweeps = sweepRects.map((rect, i) => {
        const start = TAIL * (1 - i / SEGMENTS);
        const animation = rect.animate(
          sweep.keyframes.map(k => {
            const len = start + (100 - start) * (k.progress / SWEEP_TRAVEL);
            return { strokeDasharray: `${len} ${100 - len}`, strokeDashoffset: `${len - k.p}px`, offset: k.t, easing: 'linear' };
          }),
          { duration: sweep.durationMs, fill: 'both' },
        );
        animation.pause();
        animation.currentTime = showing ? Math.min(Number.isFinite(elapsedMs) ? elapsedMs : 0, sweep.durationMs) : 0;
        if (i === 0) animation.onfinish = () => { if (showing) setSettled(true); };
        if (showing && Number(animation.currentTime) < sweep.durationMs) animation.play();
        return animation;
      });
    };

    const run = () => {
      clearTimeout(pauseTimer);
      wasRunning = true;
      animations.forEach(a => a.play());
    };
    // When a request finishes while the box has focus, the ring is drawn from where the comet's head is: the comet stops there and fades, and the sweep starts from the
    // same place, so the comet turns into the ring instead of vanishing and a ring appearing from the top.
    const handOff = () => {
      if (!timeline || animations.length === 0) return;
      const lap = Number(animations[0].currentTime);
      // A pause takes hold a frame later, so the time is set outright: the comet is left exactly where the sweep is about to start.
      animations.forEach(a => { a.pause(); if (Number.isFinite(lap)) a.currentTime = lap; });
      if (!showing || !Number.isFinite(lap)) return;
      sweepStart = headPosition(timeline.keyframes, lap / timeline.durationMs);
      setSettled(false);
      buildSweeps(0);
    };
    // After the fade-out (0.6s), nothing is visible, so stop spending frames on it.
    const rest = () => {
      clearTimeout(pauseTimer);
      if (wasRunning) { wasRunning = false; handOff(); }
      pauseTimer = setTimeout(() => { if (!active()) animations.forEach(a => a.pause()); }, 700);
    };

    const begin = () => {
      showing = true;
      sweepStart = 0;                 // a fresh focus draws the ring from the top, as it always has
      setFocused(true);
      setSettled(false);
      buildSweeps(0);
    };
    const onFocusIn = () => {
      if (hasFocus) return; // focus moving between the box and its buttons is not a new focus
      hasFocus = true;
      if (armed) begin();
    };
    const onFocusOut = (e: FocusEvent) => {
      if (e.relatedTarget instanceof Node && host.contains(e.relatedTarget)) return;
      hasFocus = false;
      showing = false;
      setFocused(false);
      setSettled(false);
    };
    // The first real input arms it (capture, so it is seen before the click that focuses the box). If the box already has focus from the page load, the sweep
    // runs now, because the person is typing in it.
    const arm = () => {
      if (armed) return;
      armed = true;
      document.removeEventListener('pointerdown', arm, true);
      document.removeEventListener('keydown', arm, true);
      if (hasFocus && !showing) begin();
    };
    document.addEventListener('pointerdown', arm, true);
    document.addEventListener('keydown', arm, true);
    host.addEventListener('focusin', onFocusIn);
    host.addEventListener('focusout', onFocusOut);

    build();
    const observer = new ResizeObserver(build);
    observer.observe(el);
    control.current = { run, rest };
    return () => {
      clearTimeout(pauseTimer);
      document.removeEventListener('pointerdown', arm, true);
      document.removeEventListener('keydown', arm, true);
      host.removeEventListener('focusin', onFocusIn);
      host.removeEventListener('focusout', onFocusOut);
      observer.disconnect();
      animations.forEach(a => a.cancel());
      sweeps.forEach(a => a.cancel());
    };
  }, []);

  // Play while Sonic is working, fade out and rest after it stops.
  useEffect(() => {
    if (running) control.current?.run();
    else control.current?.rest();
  }, [running]);

  const showRing = focused && !running;
  const segments = (sweep: boolean) => Array.from({ length: SEGMENTS }, (_, i) => {
    const len = TAIL * (1 - i / SEGMENTS);
    // i = 0 is the longest and faintest, i = SEGMENTS - 1 the shortest and brightest: the colour runs tail to head with it.
    const t = i / (SEGMENTS - 1);
    return (
      <rect
        key={i}
        pathLength={100}
        strokeDasharray={sweep ? '0 100' : `${len} ${100 - len}`}
        style={{ ['--len' as string]: sweep ? 0 : len, ['--t' as string]: t } as React.CSSProperties}
      />
    );
  });

  return (
    <svg ref={svg} className="input-glow" aria-hidden="true" focusable="false" style={{ opacity: 1 }}>
      <g className="glow-sweep" style={{ opacity: showRing && !settled ? 1 : 0, transition: 'opacity 0.6s ease' }}>{segments(true)}</g>
      <g className="glow-comet" style={{ opacity: running ? 1 : 0, transition: 'opacity 0.6s ease' }}>{segments(false)}</g>
      {/* What the sweep leaves behind: a quiet static ring, so focus stays visible once the animation is over (it dissolves into this, not into nothing). */}
      <rect className="glow-ring" style={{ opacity: showRing && settled ? 1 : 0, transition: 'opacity 0.6s ease' }} />
    </svg>
  );
}

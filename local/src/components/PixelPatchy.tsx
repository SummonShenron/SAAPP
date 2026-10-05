import { useEffect, useState } from 'react';
import './__styles__/PixelPatchy.css';

// A tiny 8-bit Patchy sitting on the top-right corner of the chat input with a little laptop, drawn in
// the spirit of the full-size Patchy in errAgent (dark visor face, slim ears, glowing eyes, an accent
// antenna) but deliberately thin: a one-pixel outline, one-pixel arms, no mouth. Every pixel is a rect
// in a 15x15 grid, so it stays crisp and animates with steps() for a real pixel-art feel.
//
// Moods: idle (cyan, slow bob, blinking cursor), working (yellow like Patchy's "analyzing", typing
// hands and scrolling code), celebrating (green hop when a turn finishes), laughing (pink, when poked).

const COLS = 15;
const ROWS = 15;
const PIXEL = 3; // on-screen size of one grid pixel, in CSS px

// Letters index PALETTE. "." is empty. Accent-colored parts (antenna ball, eyes, screen content) are
// separate layers below, so they can change color and animate independently of the static body.
const PALETTE: Record<string, string> = {
  O: '#4B5163', // head outline (light enough to show on dark themes, dark enough for light ones)
  K: '#0C1016', // visor / screen dark
  G: '#8E95A2', // ears, neck, arms
  T: '#6B7488', // laptop lid frame (darker than the arms so the two never merge into one slab)
  L: '#B7BECB', // laptop base
  D: '#8E95A2', // laptop base shadow
};

const BODY: string[] = [
  '...............', // 0  antenna ball (accent layer)
  '.......G.......', // 1  antenna stem
  '..OOOOOOOOOOO..', // 2  head top
  '..OKKKKKKKKKO..', // 3
  '.GOKKKKKKKKKOG.', // 4  ears + eyes (eyes are an accent layer)
  '.GOKKKKKKKKKOG.', // 5
  '..OKKKKKKKKKO..', // 6
  '..OOOOOOOOOOO..', // 7  head bottom
  '.GG....G....GG.', // 8  shoulders hang from the head's corners; a one-pixel neck
  '.G.TTTTTTTTT.G.', // 9  slim arms run down outside the lid; lid top
  '.G.TKKKKKKKT.G.', // 10 screen
  '.G.TKKKKKKKT.G.', // 11 screen
  '...TTTTTTTTT...', // 12 lid bottom (the hands, an animated layer, sit at the arms' ends)
  '.LLLLLLLLLLLLL.', // 13 laptop base, wide enough for the hands to land on
  '.DDDDDDDDDDDDD.', // 14 base shadow
];

type Rect = { x: number; y: number; w: number; h: number; fill: string };

// Merges runs of the same color along a row, so the DOM has a few dozen rects instead of ~150.
function rectsFromRows(rows: string[]): Rect[] {
  const rects: Rect[] = [];
  rows.forEach((row, y) => {
    let x = 0;
    while (x < row.length) {
      const ch = row[x];
      let end = x;
      while (end + 1 < row.length && row[end + 1] === ch) end++;
      if (ch !== '.' && PALETTE[ch]) rects.push({ x, y, w: end - x + 1, h: 1, fill: PALETTE[ch] });
      x = end + 1;
    }
  });
  return rects;
}

const BODY_RECTS = rectsFromRows(BODY);

// [x, y, w, h] in grid units, drawn in the accent color.
type Px = [number, number, number, number];

const EYES_TOP: Px[] = [[5, 4, 1, 1], [9, 4, 1, 1]];
const EYES_BOTTOM: Px[] = [[5, 5, 1, 1], [9, 5, 1, 1]]; // the blink hides this row
const EYES_LAUGH: Px[] = [[5, 4, 1, 1], [4, 5, 1, 1], [6, 5, 1, 1], [9, 4, 1, 1], [8, 5, 1, 1], [10, 5, 1, 1]];
const SCREEN_IDLE: Px[] = [[4, 10, 3, 1]];
const CURSOR: Px = [8, 10, 1, 1];

// Four frames of "code" scrolling past on the two-row screen: [x, y, width] lines, one pixel tall.
const CODE_FRAMES: Array<Array<[number, number, number]>> = [
  [[4, 10, 3], [8, 10, 2], [4, 11, 5]],
  [[4, 10, 4], [9, 10, 1], [4, 11, 2], [7, 11, 3]],
  [[5, 10, 2], [8, 10, 3], [4, 11, 3], [8, 11, 2]],
  [[4, 10, 2], [7, 10, 4], [5, 11, 4]],
];

const px = (list: Px[], keyPrefix: string) =>
  list.map(([x, y, w, h], i) => (
    <rect key={`${keyPrefix}${i}`} className="pp-accent" x={x} y={y} width={w} height={h} />
  ));

interface PixelPatchyProps {
  /** True while Sonic is busy: Patchy types and the laptop shows scrolling code. */
  working?: boolean;
  /** Bump this number to make Patchy hop in celebration (a turn just finished). */
  cheer?: number;
}

export function PixelPatchy({ working = false, cheer = 0 }: PixelPatchyProps) {
  const [laughing, setLaughing] = useState(false);
  const [celebrating, setCelebrating] = useState(false);
  const [seenCheer, setSeenCheer] = useState(cheer);

  // Reacting to a prop change during render (instead of in an effect) is React's own recommended
  // pattern for "reset/derive state when a prop changes".
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

  const mood = laughing ? 'laugh' : celebrating && !working ? 'done' : working ? 'working' : 'idle';

  return (
    <button
      type="button"
      className={`pp-root pp-${mood}`}
      onClick={() => setLaughing(true)}
      title={laughing ? 'Hehe!' : 'Poke Patchy!'}
      aria-label="Patchy, the mascot. Poke him."
    >
      <svg
        viewBox={`0 0 ${COLS} ${ROWS}`}
        width={COLS * PIXEL}
        height={ROWS * PIXEL}
        shapeRendering="crispEdges"
        xmlns="http://www.w3.org/2000/svg"
        aria-hidden="true"
      >
        <g className="pp-bob">
          {BODY_RECTS.map((r, i) => (
            <rect key={i} x={r.x} y={r.y} width={r.w} height={r.h} fill={r.fill} />
          ))}

          <rect className="pp-accent pp-antenna" x={7} y={0} width={1} height={1} />

          {laughing ? (
            px(EYES_LAUGH, 'el')
          ) : (
            <>
              {px(EYES_TOP, 'et')}
              <g className="pp-blink">{px(EYES_BOTTOM, 'eb')}</g>
            </>
          )}

          {working && !laughing ? (
            CODE_FRAMES.map((lines, frame) => (
              <g
                key={frame}
                className={`pp-code-frame${frame === 0 ? ' pp-frame-first' : ''}`}
                style={{ animationDelay: `${frame * 0.2}s` }}
              >
                {lines.map(([x, y, w], i) => (
                  <rect key={i} className="pp-accent" x={x} y={y} width={w} height={1} />
                ))}
              </g>
            ))
          ) : (
            <>
              {px(SCREEN_IDLE, 'si')}
              <rect className="pp-accent pp-cursor" x={CURSOR[0]} y={CURSOR[1]} width={CURSOR[2]} height={CURSOR[3]} />
            </>
          )}

          {/* The slim arms come down outside the lid and the hands rest on the ends of the laptop's base;
              they take turns lifting off it while Sonic works. */}
          <rect className="pp-hand pp-hand-l" x={1} y={12} width={1} height={1} fill={PALETTE.G} />
          <rect className="pp-hand pp-hand-r" x={13} y={12} width={1} height={1} fill={PALETTE.G} />
        </g>
      </svg>
    </button>
  );
}

export default PixelPatchy;

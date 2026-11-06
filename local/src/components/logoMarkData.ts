// The Sonic mark, drawn as a small agent graph: six nodes, solid edges for the forward steps, and dashed edges for the ones that are not a plain step forward. The line forks at the second node
// into a crest and a trough and joins again (a signal's peak and dip, and a branch and merge in a graph); the dashed edge up to the crest is a conditional step, and the dashed line straight
// across the middle from the fourth node back to the second is state handed back to an earlier node, out of order. The colour is not stored here: components/LogoMark.tsx paints it, and the
// three files in src/assets/ (sonic_mark_orange, _white, _black) and public/favicon2.svg are drawn from these same values (logoMark.test.mjs checks they still agree). To redraw it, change these values.
export const LOGO_ORANGE = '#c85a0c';             // the brand orange: the app's --accent-primary on a light page (src/index.css); on a dark page the mark follows that token's own value

/** The mark's box, with room for the line around every dot. */
export const LOGO_VIEWBOX = '0 0 176 111';

export const LOGO_STROKE = 3.5;

/** The six nodes, left to right, as [x, y, radius]. The third is the crest (the larger dot) and the fourth, below it, the trough; the fifth is where they join. */
export const LOGO_NODES: ReadonlyArray<readonly [number, number, number]> = [
  [14, 74, 6], [50, 55, 6], [88, 20, 9], [88, 84, 6], [126, 55, 6], [162, 66, 6],
];

/** The edges as [from, to, dashed], by node index. Eight of them: the forward steps, and the two dashed ones. */
export const LOGO_EDGES: ReadonlyArray<readonly [number, number, boolean]> = [
  [0, 1, false], [1, 3, false], [3, 4, false], [4, 5, false],
  [1, 2, true],         // the diamond's upper-left edge: the conditional step up to the crest (set `false` here and `true` on [3, 4] to put the dashes on the lower-right edge instead)
  [2, 4, false],
  [4, 1, true],         // across the middle of the diamond, back from the fourth node to the second: state passed back out of order
];

export const LOGO_DASH = '6 5';

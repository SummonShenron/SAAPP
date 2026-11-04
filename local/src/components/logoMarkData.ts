// The Sonic mark: a signal line through seven points, one stretch of it dashed. Drawn from the three files the brand supplied (assets/sonic_mark_orange.svg, _white.svg, _black.svg), which differ only in colour,
// so the colour is not stored here: components/LogoMark.tsx paints it. Coordinates are those files' own (a 260 by 260 canvas), cropped to the mark by LOGO_VIEWBOX. To redraw it, replace these values.
export const LOGO_ORANGE = '#f28c28';

/** The mark's box in the supplied canvas: the points and their dots, with half a stroke of room. */
export const LOGO_VIEWBOX = '42 67 176 111';

export const LOGO_STROKE = 4;

/** The seven points the line passes through, left to right, and the dot radius at each. The fourth is the peak and its dot is a pixel larger. */
export const LOGO_POINTS: ReadonlyArray<readonly [number, number, number]> = [
  [55, 140, 9], [80, 95, 9], [105, 165, 9], [130, 80, 10], [155, 150, 9], [180, 110, 9], [205, 135, 9],
];

/** The segment drawn dashed (from point 3 to point 4, the climb to the peak), as the index of its first point, and its dash pattern. */
export const LOGO_DASHED_FROM = 2;
export const LOGO_DASH = '6 5';

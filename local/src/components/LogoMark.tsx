import React from 'react';
import { LOGO_DASH, LOGO_DASHED_FROM, LOGO_ORANGE, LOGO_POINTS, LOGO_STROKE, LOGO_VIEWBOX } from './logoMarkData';

const [, , VB_W, VB_H] = LOGO_VIEWBOX.split(' ').map(Number);

/**
 * The Sonic mark as inline vector. `color` is the brand orange; `mono` paints with the current text colour, so it follows the theme (white on dark, black on light) with no second file. `size` is the height in
 * pixels; the width keeps the mark's own proportions. The stroke and the dots are the supplied files' own, so at a small size the line is thin, which is how the mark is drawn.
 */
const LogoMark: React.FC<{ size?: number; variant?: 'color' | 'mono'; className?: string; label?: string }> = ({ size = 28, variant = 'color', className, label }) => {
  const paint = variant === 'color' ? LOGO_ORANGE : 'currentColor';
  return (
    <svg
      className={className}
      viewBox={LOGO_VIEWBOX}
      width={Math.round((size * VB_W) / VB_H)}
      height={size}
      role={label ? 'img' : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      focusable="false"
    >
      {LOGO_POINTS.slice(0, -1).map(([x, y], index) => {
        const [nextX, nextY] = LOGO_POINTS[index + 1];
        return <line key={index} x1={x} y1={y} x2={nextX} y2={nextY} stroke={paint} strokeWidth={LOGO_STROKE} strokeDasharray={index === LOGO_DASHED_FROM ? LOGO_DASH : undefined} />;
      })}
      {LOGO_POINTS.map(([x, y, radius], index) => <circle key={index} cx={x} cy={y} r={radius} fill={paint} />)}
    </svg>
  );
};

export default LogoMark;

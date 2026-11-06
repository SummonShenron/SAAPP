import React from 'react';
import { LOGO_DASH, LOGO_EDGES, LOGO_NODES, LOGO_ORANGE, LOGO_STROKE, LOGO_VIEWBOX } from './logoMarkData';

const [, , VB_W, VB_H] = LOGO_VIEWBOX.split(' ').map(Number);

/**
 * The Sonic mark as inline vector. `color` is the brand orange: the page's own accent colour (`--accent-primary`, so it is the light orange on a light page and the softer one on a dark page), with the brand
 * orange as the fallback where that is not defined. `mono` paints with the current text colour, so it follows the theme (white on dark, black on light) with no second file. `size` is the height in pixels;
 * the width keeps the mark's own proportions. The line is thin by design, so at a small size it is the favicon's heavier drawing (public/favicon2.svg) that should be used.
 */
const LogoMark: React.FC<{ size?: number; variant?: 'color' | 'mono'; className?: string; label?: string }> = ({ size = 28, variant = 'color', className, label }) => (
  <svg
    className={className}
    viewBox={LOGO_VIEWBOX}
    width={Math.round((size * VB_W) / VB_H)}
    height={size}
    role={label ? 'img' : undefined}
    aria-label={label}
    aria-hidden={label ? undefined : true}
    focusable="false"
    style={variant === 'color' ? { color: `var(--accent-primary, ${LOGO_ORANGE})` } : undefined}
  >
    {LOGO_EDGES.map(([from, to, dashed], index) => (
      <line key={index} x1={LOGO_NODES[from][0]} y1={LOGO_NODES[from][1]} x2={LOGO_NODES[to][0]} y2={LOGO_NODES[to][1]} stroke="currentColor" strokeWidth={LOGO_STROKE} strokeDasharray={dashed ? LOGO_DASH : undefined} />
    ))}
    {LOGO_NODES.map(([x, y, radius], index) => <circle key={index} cx={x} cy={y} r={radius} fill="currentColor" />)}
  </svg>
);

export default LogoMark;

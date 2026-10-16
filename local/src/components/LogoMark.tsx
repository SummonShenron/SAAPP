import React, { useId } from 'react';
import { LOGO_GRADIENT_STOPS, LOGO_GRADIENT_X, LOGO_PATH, LOGO_VIEWBOX } from './logoMarkData';

const [, , VB_W, VB_H] = LOGO_VIEWBOX.split(' ').map(Number);

/**
 * The swirl mark as inline vector. `color` is the full-colour gradient; `mono` fills with the current text colour, so it follows the theme (white on
 * dark, the accent blue on light) with no second file. `size` is the height in pixels; the width keeps the mark's own proportions.
 */
const LogoMark: React.FC<{ size?: number; variant?: 'color' | 'mono'; className?: string; label?: string }> = ({ size = 28, variant = 'color', className, label }) => {
  const gradientId = `logo-gradient-${useId().replace(/[^a-zA-Z0-9_-]/g, '')}`;
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
      {variant === 'color' && (
        <defs>
          <linearGradient id={gradientId} gradientUnits="userSpaceOnUse" x1={LOGO_GRADIENT_X[0]} y1={0} x2={LOGO_GRADIENT_X[1]} y2={0}>
            {LOGO_GRADIENT_STOPS.map(([offset, color]) => <stop key={offset} offset={`${offset}%`} stopColor={color} />)}
          </linearGradient>
        </defs>
      )}
      <path fill={variant === 'color' ? `url(#${gradientId})` : 'currentColor'} fillRule="evenodd" d={LOGO_PATH} />
    </svg>
  );
};

export default LogoMark;

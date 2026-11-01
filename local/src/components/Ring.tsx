import React from 'react';
import { LEVEL_COLOR } from './sessionColumnLogic';
import './__styles__/Ring.css';

/**
 * The ring both of the session column's gauges are drawn with (the usage gauge and the memory compaction meter), at the size they always were: a track, an arc filled clockwise from the top to `fraction`
 * of the way round, in the colour of its `level`, and (at the full size) the percentage in the middle. One drawing means the two cannot drift apart. `animated` makes the arc move smoothly when the
 * reading changes (the usage ring has never done that and keeps not doing it); `returning` makes it trace back slower than it filled, as the memory ring does after a compaction; `overloaded` pulses it.
 * The track and the number keep the class names the column's and the strip's styles already use.
 */
export interface RingProps {
  fraction: number;
  /** Picks the colour: one of LEVEL_COLOR's keys (ok, notice, throttled, blocked). */
  level: string;
  size?: number;
  stroke?: number;
  /** The percentage in the middle (the full-size ring has it; the strip's small one does not). */
  percent?: boolean;
  label?: string;
  trackClass?: string;
  numberClass?: string;
  animated?: boolean;
  returning?: boolean;
  overloaded?: boolean;
}

const Ring: React.FC<RingProps> = ({ fraction, level, size = 44, stroke = 4, percent = true, label, trackClass, numberClass, animated = false, returning = false, overloaded = false }) => {
  const radius = (size - stroke) / 2;
  const length = 2 * Math.PI * radius;
  const filled = Math.min(1, Math.max(0, Number.isFinite(fraction) ? fraction : 0)) * length;
  const classes = `ring${animated ? ' ring-animated' : ''}${returning ? ' ring-returning' : ''}${overloaded ? ' ring-overloaded' : ''}`;
  return (
    <svg className={classes} width={size} height={size} viewBox={`0 0 ${size} ${size}`} role={label ? 'img' : undefined} aria-label={label} aria-hidden={label ? undefined : true}>
      <circle cx={size / 2} cy={size / 2} r={radius} fill="none" stroke="var(--border-color)" strokeWidth={stroke} className={trackClass} />
      <circle
        className="ring-arc" cx={size / 2} cy={size / 2} r={radius} fill="none" stroke={LEVEL_COLOR[level] ?? LEVEL_COLOR.ok} strokeWidth={stroke} strokeLinecap="round"
        style={{ strokeDasharray: `${filled} ${length}` }} transform={`rotate(-90 ${size / 2} ${size / 2})`}
      />
      {percent && <text x={size / 2} y={size / 2 + 4} textAnchor="middle" className={numberClass}>{Math.round(fraction * 100)}%</text>}
    </svg>
  );
};

export default Ring;

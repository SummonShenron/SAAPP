import React from 'react';
import type { StepKind } from './stepKinds';
import './__styles__/StepIcons.css';

// The little animated icon a running step has in its dot (rules in stepKinds.ts, motion in __styles__/StepIcons.css): </> for code, a globe for the web, a cylinder for a database, a chat bubble for
// conversation, a book for reading, a magnifier for looking, an envelope, a calendar, a pin, a spark for thinking, a browser window, and one general icon (three joined dots) for any step with no icon of its own.
// Drawn on a 16 x 16 grid at the dot's own 14px, in the accent colour. Decoration only (aria-hidden): the step's words say what it is doing.

const ICONS: Record<StepKind, () => React.JSX.Element> = {
  code: () => (
    <>
      <path className="si-lt" d="M6 4.5 2.5 8 6 11.5" />
      <path className="si-gt" d="M10 4.5 13.5 8 10 11.5" />
      <path className="si-slash" d="M9 3.5 7 12.5" />
    </>
  ),
  web: () => (
    <>
      <circle cx="8" cy="8" r="6" />
      <path d="M2 8h12" />
      <ellipse className="si-meridian" cx="8" cy="8" rx="2.6" ry="6" />
    </>
  ),
  database: () => (
    <>
      <path d="M3 4v8M13 4v8" />
      <ellipse className="si-layer si-layer-1" cx="8" cy="4" rx="5" ry="2" />
      <path className="si-layer si-layer-2" d="M3 8c0 1.1 2.2 2 5 2s5-.9 5-2" />
      <path className="si-layer si-layer-3" d="M3 12c0 1.1 2.2 2 5 2s5-.9 5-2" />
    </>
  ),
  chat: () => (
    <>
      <path d="M3 3h10a1 1 0 0 1 1 1v6.5a1 1 0 0 1-1 1H8.5L5.5 14v-2.5H3a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z" />
      <circle className="si-dot si-dot-1" cx="5.2" cy="7.3" r="0.9" />
      <circle className="si-dot si-dot-2" cx="8" cy="7.3" r="0.9" />
      <circle className="si-dot si-dot-3" cx="10.8" cy="7.3" r="0.9" />
    </>
  ),
  read: () => (
    <>
      <path d="M8 4.2C6.8 3.4 4.6 3.2 2.5 3.5v8.3c2.1-.3 4.3-.1 5.5.7V4.2z" />
      <path d="M8 4.2c1.2-.8 3.4-1 5.5-.7v8.3c-2.1-.3-4.3-.1-5.5.7V4.2z" />
      <path className="si-line si-line-1" d="M9.9 6.1h2" />
      <path className="si-line si-line-2" d="M9.9 8.1h2" />
    </>
  ),
  search: () => (
    <g className="si-sweep">
      <circle cx="7" cy="7" r="4.2" />
      <path d="m10.2 10.2 3.3 3.3" />
    </g>
  ),
  mail: () => (
    <>
      <rect x="2" y="3.5" width="12" height="9" rx="1.5" />
      <path className="si-flap" d="M2.5 5 8 9l5.5-4" pathLength={1} />
    </>
  ),
  calendar: () => (
    <>
      <rect x="2.5" y="3.5" width="11" height="10" rx="1.5" />
      <path d="M2.5 6.8h11M5.5 2.2v2.6M10.5 2.2v2.6" />
      <rect className="si-day" x="4.6" y="8.4" width="2" height="2" rx="0.4" />
    </>
  ),
  map: () => (
    <>
      <path className="si-pin" d="M8 14s4.5-4.2 4.5-7.5a4.5 4.5 0 1 0-9 0C3.5 9.8 8 14 8 14z" />
      <circle className="si-pin" cx="8" cy="6.5" r="1.6" />
    </>
  ),
  think: () => <path className="si-twinkle" d="M8 2l1.3 4.7L14 8l-4.7 1.3L8 14l-1.3-4.7L2 8l4.7-1.3z" />,
  browser: () => (
    <>
      <rect x="2" y="3" width="12" height="10" rx="1.5" />
      <path d="M2 6h12" />
      <path className="si-cursor" d="M7.5 8.3v3.4l1.1-.9.7 1.6.7-.3-.7-1.6 1.4-.1z" />
    </>
  ),
  inject: () => (
    <>
      <path d="M4.9 8.4 7.6 11.1 11.5 7.2 8.8 4.5z" />
      <path d="M6.2 9.8 3.6 12.4" />
      <path d="M7.9 3.6 12.4 8.1" />
      <g className="si-plunger">
        <path d="M10.1 5.9 12.2 3.8" />
        <path d="M11 2.5 13.5 5" />
      </g>
    </>
  ),
  add: () => <path className="si-plus" d="M8 3v10M3 8h10" />,
  generic: () => (
    <>
      <path d="M8 3.8 3.8 12M8 3.8 12.2 12M3.8 12h8.4" />
      <circle className="si-node si-node-1" cx="8" cy="3.8" r="1.6" />
      <circle className="si-node si-node-2" cx="3.8" cy="12" r="1.6" />
      <circle className="si-node si-node-3" cx="12.2" cy="12" r="1.6" />
    </>
  ),
};

/** The icon for a step's kind (an unknown or missing kind is the general icon). `still` holds it standing, for the summary beside a finished reply's steps. */
const StepIcon: React.FC<{ kind?: StepKind; still?: boolean }> = ({ kind = 'generic', still = false }) => {
  const Shape = ICONS[kind] ?? ICONS.generic;
  return (
    <svg className={`si si-${kind in ICONS ? kind : 'generic'}${still ? ' si-still' : ''}`} width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
      <Shape />
    </svg>
  );
};

export default StepIcon;

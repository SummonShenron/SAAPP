import React, { useEffect, useRef } from 'react';

const WIDTH_KEY = 'sessionColumnWidth';
const MIN_W = 220;
const MAX_W = 520;
const DEFAULT_W = 272;

function clampWidth(value: number): number {
  return Math.max(MIN_W, Math.min(MAX_W, Math.round(value)));
}

function applyWidth(width: number) {
  document.documentElement.style.setProperty('--session-column-w', `${width}px`);
}

/** A grip on the column's left edge: drag it to widen or narrow the session column. The width is kept in localStorage. */
export default function ColumnResizer() {
  const startX = useRef(0);
  const startW = useRef(DEFAULT_W);

  useEffect(() => {
    const saved = Number(window.localStorage.getItem(WIDTH_KEY));
    if (Number.isFinite(saved) && saved > 0) applyWidth(clampWidth(saved));
  }, []);

  const onPointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    const column = event.currentTarget.parentElement;
    startX.current = event.clientX;
    startW.current = column ? column.getBoundingClientRect().width : DEFAULT_W;
    event.currentTarget.setPointerCapture(event.pointerId);
  };

  const onPointerMove = (event: React.PointerEvent<HTMLDivElement>) => {
    if (!event.currentTarget.hasPointerCapture(event.pointerId)) return;
    // The handle sits on the left edge, so dragging left makes the column wider.
    applyWidth(clampWidth(startW.current + (startX.current - event.clientX)));
  };

  const onPointerUp = (event: React.PointerEvent<HTMLDivElement>) => {
    if (!event.currentTarget.hasPointerCapture(event.pointerId)) return;
    event.currentTarget.releasePointerCapture(event.pointerId);
    const current = document.documentElement.style.getPropertyValue('--session-column-w');
    const width = parseInt(current, 10);
    if (Number.isFinite(width)) window.localStorage.setItem(WIDTH_KEY, String(width));
  };

  return (
    <div
      className="sc-resizer"
      role="separator"
      aria-orientation="vertical"
      aria-label="Drag to resize the session column"
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerUp}
    />
  );
}

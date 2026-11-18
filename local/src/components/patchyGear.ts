// The two little gears that turn beside Patchy's head while the tool agent is going round its loop (MiniPatchy.tsx, MiniPatchy.css `.mp-gear`). A gear is just a toothed outline; this draws one. No React in here, so it can be
// tested (patchyLoop.test.mjs).

const round = (n: number) => Math.round(n * 100) / 100;

/**
 * The outline of a gear centred on (cx, cy): `teeth` flat-topped teeth that stand `outer` from the centre, with the gaps between them at `inner`. Each tooth takes about a quarter of the way round its own slice and the
 * gap the rest, so two gears whose teeth counts differ still look meshed when they turn. Pure.
 */
export function gearPath(cx: number, cy: number, outer: number, inner: number, teeth: number): string {
  const count = Math.max(3, Math.floor(teeth));
  const slice = (Math.PI * 2) / count;
  const point = (radius: number, angle: number) => `${round(cx + radius * Math.cos(angle))} ${round(cy + radius * Math.sin(angle))}`;
  const parts: string[] = [];
  for (let i = 0; i < count; i++) {
    const a = i * slice - Math.PI / 2;
    parts.push(
      `${i === 0 ? 'M' : 'L'} ${point(inner, a)}`,
      `L ${point(outer, a + slice * 0.12)}`,
      `L ${point(outer, a + slice * 0.38)}`,
      `L ${point(inner, a + slice * 0.5)}`,
    );
  }
  return `${parts.join(' ')} Z`;
}

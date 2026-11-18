// The Sonic Health Home tab's rows (pages/SonicHealthHome.tsx): the left stack, the chart stack and the right stack hold different numbers of panels, so each stack divides the same number of shared rows among its own panels.
// A panel that spans two rows ends exactly where the panels beside it end, so every panel's top and bottom sit on a row line the other stacks share. Kept apart from React so the rule can be tested (sonicHealthHomeLayout.test.mjs).

/**
 * How many rows each panel of a stack covers, so the stack covers `rows` rows in all (never fewer than one each). Every panel starts with one row; each spare row goes to the panel that would otherwise have the
 * most height to fill per row it has, judged by `heights` (a rough natural height for each panel; equal when not given, which hands the spare rows to the first panels, one each). Pure.
 */
export function rowSpans(count: number, rows: number, heights: readonly number[] = []): number[] {
  if (count <= 0) return [];
  const spans = Array.from({ length: count }, () => 1);
  const need = (i: number) => (heights[i] > 0 ? heights[i] : 1) / spans[i];
  for (let spare = rows - count; spare > 0; spare--) {
    let pick = 0;
    for (let i = 1; i < count; i++) if (need(i) > need(pick)) pick = i;
    spans[pick] += 1;
  }
  return spans;
}

/** The shared rows for stacks of these sizes: as many as the biggest stack has panels, so no panel is ever squeezed to less than a row. Pure. */
export function sharedRows(...counts: number[]): number {
  return Math.max(1, ...counts);
}

import { useState } from 'react';
import { useMediaQuery } from '../hooks/useMediaQuery';
import { GRAPH_MIN_WIDTH_PX, readStepsStyle, stepsGraphOn } from './stepsStyle';

/** Whether the conversation's steps are drawn as a graph (stepsStyle.ts): wide enough and not switched off. Read once for the stored choice, followed live for the window's width. */
export function useStepsGraph(): boolean {
  const wide = useMediaQuery(`(min-width: ${GRAPH_MIN_WIDTH_PX}px)`);
  const [stored] = useState(readStepsStyle);
  return stepsGraphOn(stored, wide);
}

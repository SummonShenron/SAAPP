import { useEffect, useState } from 'react';

/**
 * True when the person has asked their system for less motion, or when the caller says so (the dev preview, a test). The graph loader (LoaderGraphs.tsx) then holds each drawing still on one calm frame and
 * skips the landing and the merge. Follows the setting live if it changes while the page is open.
 */
export function useReducedMotion(force?: boolean): boolean {
  const query = typeof window !== 'undefined' && typeof window.matchMedia === 'function' ? '(prefers-reduced-motion: reduce)' : '';
  const [system, setSystem] = useState(() => (query ? window.matchMedia(query).matches : false));
  useEffect(() => {
    if (!query) return undefined;
    const media = window.matchMedia(query);
    const update = () => setSystem(media.matches);
    media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
  }, [query]);
  return force ?? system;
}

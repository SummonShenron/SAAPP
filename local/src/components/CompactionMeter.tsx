import React, { useEffect, useRef, useState } from 'react';
import Ring from './Ring';
import { RETURN_MS, didCompact, isOverloaded, markerFraction, meterLevel, meterTitle, type CompactionView } from './compactionMeterLogic';

/**
 * The memory compaction meter (it replaced the context ring): a ring, drawn like the usage ring beside it (components/Ring.tsx), filled to how many of Sonic's memory notes are waiting to be folded
 * together against the number at which they are. It fills smoothly as notes are added, goes red and pulses when the count is over the limit and being folded ("overloaded"), and when the compaction
 * has happened and the count falls, the arc traces back to the start, slower than it filled. `mini` is the smaller one for the phone strip. The words ("Memory", "37 of 100 notes") are drawn by the
 * caller, as the usage gauge's are. Counts only: nothing of any note is shown or sent.
 */
const CompactionMeter: React.FC<{ view: CompactionView; contextText?: string | null; mini?: boolean }> = ({ view, contextText, mini = false }) => {
  const [returning, setReturning] = useState(false);
  const previous = useRef<CompactionView | null>(null);

  useEffect(() => {
    const compacted = didCompact(previous.current, view);
    previous.current = view;
    if (!compacted) return undefined;
    setReturning(true);
    const timer = window.setTimeout(() => setReturning(false), RETURN_MS + 200);
    return () => window.clearTimeout(timer);
  }, [view]);

  const overloaded = isOverloaded(view);
  const title = meterTitle(view, contextText);
  const level = overloaded ? 'blocked' : meterLevel(view);
  return (
    <span title={title} style={{ display: 'inline-flex', lineHeight: 0 }}>
      {mini
        ? <Ring fraction={markerFraction(view)} level={level} size={14} stroke={3} percent={false} trackClass="rail-ring-track" animated returning={returning} overloaded={overloaded} />
        : <Ring fraction={markerFraction(view)} level={level} label={title} trackClass="sc-gauge-track" numberClass="sc-gauge-number" animated returning={returning} overloaded={overloaded} />}
    </span>
  );
};

export default CompactionMeter;

import React, { useEffect, useMemo, useState } from 'react';
import type { UsageDay } from '../api';
import { tokens, usd } from './sonicHealthFormat';

type Mode = 'cost' | 'tokens' | 'calls' | 'turns';

const WIDE = 800; // the chart's drawing width on a roomy screen; on a phone it is drawn narrower, so it keeps a useful height
const NARROW = 420;
const HEIGHT = 300;

/** True on a phone-sized screen. */
function useNarrow(): boolean {
  const query = '(max-width: 640px)';
  const [narrow, setNarrow] = useState<boolean>(() => typeof window !== 'undefined' && window.matchMedia(query).matches);
  useEffect(() => {
    const media = window.matchMedia(query);
    const update = () => setNarrow(media.matches);
    media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
  }, []);
  return narrow;
}
const MARGIN = { top: 14, right: 12, bottom: 30, left: 54 };
const PLAIN = '#38bdf8';
const TOOL = '#f59e0b';
const COST = '#38bdf8';
const TOKENS = '#818cf8';
const EVAL = '#a8a29e'; // eval runs: counted in the totals but shown apart from the app's own use

/** The smallest "round" number (1, 2, 2.5, 5 times a power of ten) at or above `value`, so the axis ends on a clean figure. */
export function niceCeil(value: number): number {
  if (!(value > 0)) return 1;
  const power = Math.pow(10, Math.floor(Math.log10(value)));
  const f = value / power;
  const step = f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10;
  return step * power;
}

function shortDay(iso: string): string {
  const [, month, day] = iso.split('-').map(Number);
  const names = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  return `${names[(month || 1) - 1]} ${day}`;
}

function axisLabel(mode: Mode, value: number): string {
  if (mode === 'cost') return value === 0 ? '$0' : value >= 1 ? `$${Number.isInteger(value) ? value : value.toFixed(2)}` : `$${value.toFixed(2)}`;
  if (mode === 'tokens') return value === 0 ? '0' : tokens(value);
  return String(Math.round(value * 10) / 10);
}

function valueOf(mode: Mode, day: UsageDay): number {
  if (mode === 'cost') return day.cost ?? 0;
  if (mode === 'tokens') return day.tokens;
  if (mode === 'calls') return day.calls;
  return day.turns;
}

function dayDetail(day: UsageDay, priced: boolean): string {
  const parts = [shortDay(day.day)];
  if (priced && day.cost !== null) parts.push(`${usd(day.cost) || '$0.00'} estimated`);
  const fromEvals = day.eval_calls ?? 0;
  parts.push(
    `${tokens(day.tokens)} tokens`,
    `${day.calls} model calls${fromEvals ? ` (${fromEvals} from evals)` : ''}`,
    `${day.turns} chat turn${day.turns === 1 ? '' : 's'}${day.tool_turns ? ` (${day.tool_turns} with tools)` : ''}`,
  );
  // Calls per chat turn, from the app's own calls only: what one message costs in model calls (evals are not chat turns).
  if (day.turns > 0) parts.push(`${((day.calls - fromEvals) / day.turns).toFixed(1)} calls per turn`);
  return parts.join(' · ');
}

/** Bars for each day of the period: what the model calls cost, how many tokens or model calls they used (eval runs shown apart), or how many chat turns there were. */
export const DailyChart: React.FC<{ daily: UsageDay[]; dailyLimitUsd?: number | null; onOpen?: () => void }> = ({ daily, dailyLimitUsd, onOpen }) => {
  const priced = daily.some(day => day.cost !== null);
  const narrow = useNarrow();
  const width = narrow ? NARROW : WIDE;
  const height = HEIGHT;
  const [requested, setMode] = useState<Mode>('cost');
  const mode: Mode = requested === 'cost' && !priced ? 'tokens' : requested;
  const [hover, setHover] = useState<number | null>(null);

  const geometry = useMemo(() => {
    const values = daily.map(day => valueOf(mode, day));
    const limit = mode === 'cost' && dailyLimitUsd ? dailyLimitUsd : 0;
    const max = niceCeil(Math.max(...values, limit, 0) * 1.05);
    const plotW = width - MARGIN.left - MARGIN.right;
    const plotH = height - MARGIN.top - MARGIN.bottom;
    const slot = daily.length ? plotW / daily.length : plotW;
    return { values, max, plotW, plotH, slot, limit };
  }, [daily, mode, dailyLimitUsd, width, height]);

  if (daily.length === 0) return <div className="sh-hint">No daily figures yet.</div>;

  const { values, max, plotW, plotH, slot, limit } = geometry;
  const y = (v: number) => MARGIN.top + plotH - (Math.min(v, max) / max) * plotH;
  const barW = Math.max(Math.min(slot * 0.62, 56), 3);
  const every = daily.length <= 10 ? 1 : daily.length <= 35 ? 5 : 10;
  const ticks = [0, 0.25, 0.5, 0.75, 1].map(f => f * max);
  const shown = hover ?? daily.length - 1;
  const total = values.reduce((a, b) => a + b, 0);
  const summary = mode === 'cost' ? `${usd(total) || '$0.00'} in total, ${usd(total / daily.length) || '$0.00'} a day`
    : mode === 'tokens' ? `${tokens(total)} tokens in total, ${tokens(Math.round(total / daily.length))} a day`
    : mode === 'calls' ? `${total} model calls in total, ${Math.round(total / daily.length)} a day`
    : `${total} chat turns in total, ${(total / daily.length).toFixed(1)} a day`;

  return (
    <div className="sh-chart">
      <div className="sh-chart-head">
        <div className="sh-seg" role="group" aria-label="What the chart shows">
          {(['cost', 'tokens', 'calls', 'turns'] as Mode[]).map(m => (
            <button key={m} type="button" className={`sh-seg-btn${mode === m ? ' sh-seg-active' : ''}`} disabled={m === 'cost' && !priced} onClick={() => setMode(m)}>
              {m === 'cost' ? 'Cost' : m === 'tokens' ? 'Tokens' : m === 'calls' ? 'Calls' : 'Turns'}
            </button>
          ))}
        </div>
        <span className="sh-chart-summary">{summary}</span>
        {onOpen && <button type="button" className="sh-link-btn" onClick={onOpen}>Open Usage ›</button>}
      </div>
      <svg className="sh-chart-svg" viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`Daily ${mode} for the last ${daily.length} days. ${summary}.`} onMouseLeave={() => setHover(null)}>
        {ticks.map(t => (
          <g key={t}>
            <line x1={MARGIN.left} x2={width - MARGIN.right} y1={y(t)} y2={y(t)} className="sh-chart-grid" />
            <text x={MARGIN.left - 8} y={y(t) + 4} textAnchor="end" className="sh-chart-axis">{axisLabel(mode, t)}</text>
          </g>
        ))}
        {daily.map((day, i) => {
          const cx = MARGIN.left + slot * i + slot / 2;
          const plain = day.turns - day.tool_turns;
          return (
            <g
              key={day.day}
              tabIndex={0}
              role="img"
              aria-label={dayDetail(day, priced)}
              className={`sh-chart-day${shown === i ? ' sh-chart-day-on' : ''}`}
              onMouseEnter={() => setHover(i)}
              onFocus={() => setHover(i)}
              onBlur={() => setHover(null)}
            >
              <rect x={MARGIN.left + slot * i} y={MARGIN.top} width={slot} height={plotH} className="sh-chart-hit" />
              {mode === 'calls' ? (
                <>
                  {/* The app's own calls underneath, any eval calls stacked on top in grey. */}
                  <rect x={cx - barW / 2} y={y(day.calls - (day.eval_calls ?? 0))} width={barW} height={Math.max(MARGIN.top + plotH - y(day.calls - (day.eval_calls ?? 0)), day.calls > 0 ? 1 : 0)} fill={PLAIN} rx={2} />
                  <rect x={cx - barW / 2} y={y(day.calls)} width={barW} height={Math.max(y(day.calls - (day.eval_calls ?? 0)) - y(day.calls), 0)} fill={EVAL} rx={2} />
                </>
              ) : mode === 'turns' ? (
                <>
                  <rect x={cx - barW / 2} y={y(plain)} width={barW} height={Math.max(MARGIN.top + plotH - y(plain), 0)} fill={PLAIN} rx={2} />
                  <rect x={cx - barW / 2} y={y(day.turns)} width={barW} height={Math.max(y(plain) - y(day.turns), 0)} fill={TOOL} rx={2} />
                </>
              ) : (
                <rect x={cx - barW / 2} y={y(values[i])} width={barW} height={Math.max(MARGIN.top + plotH - y(values[i]), values[i] > 0 ? 1 : 0)} fill={mode === 'cost' ? COST : TOKENS} rx={2} />
              )}
              {i % every === 0 || i === daily.length - 1 ? (
                <text x={cx} y={height - 10} textAnchor="middle" className="sh-chart-axis">{shortDay(day.day)}</text>
              ) : null}
            </g>
          );
        })}
        {limit > 0 && (
          <g>
            <line x1={MARGIN.left} x2={width - MARGIN.right} y1={y(limit)} y2={y(limit)} className="sh-chart-limit" />
            <text x={width - MARGIN.right} y={y(limit) - 5} textAnchor="end" className="sh-chart-limit-label">daily budget {usd(limit)}</text>
          </g>
        )}
      </svg>
      {mode === 'calls' && (
        <div className="sh-chart-legend"><span><i style={{ background: PLAIN }} />the app's own calls</span><span><i style={{ background: EVAL }} />from eval runs</span></div>
      )}
      {mode === 'turns' && (
        <div className="sh-chart-legend"><span><i style={{ background: PLAIN }} />plain chat</span><span><i style={{ background: TOOL }} />used the tool agent</span></div>
      )}
      <div className="sh-chart-detail" aria-live="polite">{dayDetail(daily[shown], priced)}</div>
    </div>
  );
};

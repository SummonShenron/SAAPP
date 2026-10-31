import React, { useMemo, useState } from 'react';
import type { CountersSummary } from '../api';
import {
  OPENING_GROUPS, PEAK_GROUPS, RANK_GROUPS, averageLine, hasLine, hasStack, niceMax, percent, periodDays, ratioLine, stackPoints, stackTotals,
  type LinePoint, type StackGroup, type StackPoint,
} from './counterSeries';
import './__styles__/CounterCharts.css';

// Charts for the coding-loop counters on Sonic Health (data: components/counterSeries.ts, from the counters summary's `daily`). Plain SVG in the app's theme colours, so they follow the light and dark themes.
// Every chart has a legend, says its numbers when a day is hovered or focused, and has a table underneath, so no colour is the only way to read it. Counts only: nothing about a person.

const WIDTH = 440;      // about the width it is drawn at in the two-column layout, so the axis text is close to its real size
const HEIGHT = 180;
const MARGIN = { top: 10, right: 8, bottom: 24, left: 36 };
const PLOT_W = WIDTH - MARGIN.left - MARGIN.right;
const PLOT_H = HEIGHT - MARGIN.top - MARGIN.bottom;
const HINT = 'Hover or focus a day to read its numbers.';

const shortDay = (iso: string): string => {
  const [, month, day] = iso.split('-').map(Number);
  return `${['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][(month || 1) - 1]} ${day}`;
};

const labelEvery = (count: number): number => Math.max(1, Math.ceil(count / 6));

const Legend: React.FC<{ items: Array<{ label: string; color: string }> }> = ({ items }) => (
  <ul className="cc-legend">
    {items.map(item => (
      <li key={item.label}><span className="cc-swatch" style={{ background: item.color }} aria-hidden="true" />{item.label}</li>
    ))}
  </ul>
);

const Figure: React.FC<{ title: string; note: string; empty: boolean; caption?: string; legend?: Array<{ label: string; color: string }>; table?: React.ReactNode; children: React.ReactNode }> = ({ title, note, empty, caption, legend, table, children }) => (
  <figure className="cc-fig">
    <figcaption><strong>{title}</strong><span>{note}</span></figcaption>
    {empty ? <div className="cc-empty">Nothing to draw yet. This fills in after the next coding turns.</div> : (
      <>
        {children}
        {caption !== '' && <div className="cc-caption" aria-live="polite">{caption ?? HINT}</div>}
        {legend && <Legend items={legend} />}
        {table && <details className="cc-table"><summary>Show as a table</summary>{table}</details>}
      </>
    )}
  </figure>
);

const DayAxis: React.FC<{ days: string[]; xOf: (index: number) => number }> = ({ days, xOf }) => {
  const step = labelEvery(days.length);
  return (
    <>
      {days.map((day, index) => (index === days.length - 1 || (index % step === 0 && days.length - 1 - index >= step)) && (
        <text key={day} className="cc-tick" x={xOf(index)} y={HEIGHT - 6} textAnchor="middle">{shortDay(day)}</text>
      ))}
    </>
  );
};

const detail = (point: StackPoint, groups: StackGroup[], unit: string): string => (
  point.total > 0
    ? `${shortDay(point.day)}: ${point.total} ${unit}. ${groups.map((group, index) => `${group.label.split(' (')[0]} ${percent(point.shares[index])}`).join(', ')}.`
    : `${shortDay(point.day)}: no ${unit}.`
);

/** One bar per day, each filled to 100% and split by the groups' shares. */
const StackedDays: React.FC<{ title: string; note: string; days: string[]; points: StackPoint[]; groups: StackGroup[]; unit: string }> = ({ title, note, days, points, groups, unit }) => {
  const [active, setActive] = useState<number | null>(null);
  const slot = PLOT_W / points.length;
  const barW = Math.max(3, Math.min(34, slot - 4));
  const xOf = (index: number) => MARGIN.left + slot * index + slot / 2;
  return (
    <Figure
      title={title} note={note} empty={!hasStack(points)} caption={active !== null ? detail(points[active], groups, unit) : undefined}
      legend={groups.map(group => ({ label: group.label, color: group.color }))}
      table={(
        <table>
          <thead><tr><th>Day</th>{groups.map(group => <th key={group.id}>{group.label.split(' (')[0]}</th>)}<th>{unit}</th></tr></thead>
          <tbody>{points.filter(point => point.total > 0).map(point => <tr key={point.day}><td>{shortDay(point.day)}</td>{point.values.map((value, index) => <td key={groups[index].id}>{value} ({percent(point.shares[index])})</td>)}<td>{point.total}</td></tr>)}</tbody>
        </table>
      )}
    >
      <svg className="cc-svg" viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img" aria-label={`${title}. ${note}`} onMouseLeave={() => setActive(null)}>
        {[0, 0.5, 1].map(tick => (
          <g key={tick}>
            <line className="cc-grid" x1={MARGIN.left} x2={WIDTH - MARGIN.right} y1={MARGIN.top + PLOT_H * (1 - tick)} y2={MARGIN.top + PLOT_H * (1 - tick)} />
            <text className="cc-tick" x={MARGIN.left - 6} y={MARGIN.top + PLOT_H * (1 - tick) + 3} textAnchor="end">{percent(tick)}</text>
          </g>
        ))}
        {points.map((point, index) => {
          let top = MARGIN.top + PLOT_H;
          return (
            <g key={point.day} className={active === index ? 'cc-col cc-col-on' : 'cc-col'}>
              {point.total > 0 && point.shares.map((share, groupIndex) => {
                const height = share * PLOT_H;
                top -= height;
                return share > 0 ? <rect key={groups[groupIndex].id} x={xOf(index) - barW / 2} y={top} width={barW} height={Math.max(height, 0)} rx={1.5} fill={groups[groupIndex].color} className="cc-seg" /> : null;
              })}
              <rect
                x={MARGIN.left + slot * index} y={MARGIN.top} width={slot} height={PLOT_H} fill="transparent" tabIndex={0} role="img" aria-label={detail(point, groups, unit)}
                onMouseEnter={() => setActive(index)} onFocus={() => setActive(index)} onBlur={() => setActive(null)}
              />
            </g>
          );
        })}
        <DayAxis days={days} xOf={xOf} />
      </svg>
    </Figure>
  );
};

const lineDetail = (day: string, series: Array<{ label: string; points: LinePoint[] }>, index: number, format: (value: number) => string, unit: string): string => (
  `${shortDay(day)}: ` + series.map(item => {
    const point = item.points[index];
    return point.value === null ? `${item.label} none` : `${item.label} ${format(point.value)} (${point.n} ${unit})`;
  }).join('; ') + '.'
);

/** One or two lines over the days. A day with no turns is a gap, not a zero. */
const LineDays: React.FC<{ title: string; note: string; days: string[]; series: Array<{ label: string; color: string; points: LinePoint[] }>; format: (value: number) => string; unit: string; axisMax?: number }> = ({ title, note, days, series, format, unit, axisMax }) => {
  const [active, setActive] = useState<number | null>(null);
  const top = axisMax ?? niceMax(Math.max(0, ...series.flatMap(item => item.points.map(point => point.value ?? 0))));
  const slot = PLOT_W / days.length;
  const xOf = (index: number) => MARGIN.left + slot * index + slot / 2;
  const yOf = (value: number) => MARGIN.top + PLOT_H * (1 - Math.min(value / top, 1));
  const paths = series.map(item => {
    let d = '';
    let pen = false;
    item.points.forEach((point, index) => {
      if (point.value === null) { pen = false; return; }
      d += `${pen ? 'L' : 'M'}${xOf(index).toFixed(1)} ${yOf(point.value).toFixed(1)} `;
      pen = true;
    });
    return d.trim();
  });
  return (
    <Figure
      title={title} note={note} empty={!series.some(item => hasLine(item.points))} caption={active !== null ? lineDetail(days[active], series, active, format, unit) : undefined}
      legend={series.map(item => ({ label: item.label, color: item.color }))}
      table={(
        <table>
          <thead><tr><th>Day</th>{series.map(item => <th key={item.label}>{item.label}</th>)}</tr></thead>
          <tbody>{days.map((day, index) => series.some(item => item.points[index].value !== null) && <tr key={day}><td>{shortDay(day)}</td>{series.map(item => <td key={item.label}>{item.points[index].value === null ? '-' : `${format(item.points[index].value as number)} (${item.points[index].n} ${unit})`}</td>)}</tr>)}</tbody>
        </table>
      )}
    >
      <svg className="cc-svg" viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img" aria-label={`${title}. ${note}`} onMouseLeave={() => setActive(null)}>
        {[0, 0.5, 1].map(tick => (
          <g key={tick}>
            <line className="cc-grid" x1={MARGIN.left} x2={WIDTH - MARGIN.right} y1={MARGIN.top + PLOT_H * (1 - tick)} y2={MARGIN.top + PLOT_H * (1 - tick)} />
            <text className="cc-tick" x={MARGIN.left - 6} y={MARGIN.top + PLOT_H * (1 - tick) + 3} textAnchor="end">{format(top * tick)}</text>
          </g>
        ))}
        {active !== null && <line className="cc-cross" x1={xOf(active)} x2={xOf(active)} y1={MARGIN.top} y2={MARGIN.top + PLOT_H} />}
        {series.map((item, index) => paths[index] && <path key={item.label} d={paths[index]} fill="none" stroke={item.color} strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" />)}
        {series.map(item => item.points.map((point, index) => point.value !== null && (
          <circle key={`${item.label}-${days[index]}`} cx={xOf(index)} cy={yOf(point.value)} r={4} fill={item.color} stroke="var(--cc-surface)" strokeWidth={2} />
        )))}
        {days.map((day, index) => (
          <rect
            key={day} x={MARGIN.left + slot * index} y={MARGIN.top} width={slot} height={PLOT_H} fill="transparent" tabIndex={0} role="img" aria-label={lineDetail(day, series, index, format, unit)}
            onMouseEnter={() => setActive(index)} onFocus={() => setActive(index)} onBlur={() => setActive(null)}
          />
        ))}
        <DayAxis days={days} xOf={xOf} />
      </svg>
    </Figure>
  );
};

/** Two horizontal bars, this period and the one before, each split by the groups' shares. */
const ComparedBars: React.FC<{ title: string; note: string; groups: StackGroup[]; rows: Array<{ label: string; point: StackPoint }>; unit: string }> = ({ title, note, groups, rows, unit }) => (
  <Figure
    title={title} note={note} empty={!rows.some(row => row.point.total > 0)} legend={groups.map(group => ({ label: group.label, color: group.color }))} caption=""
    table={(
      <table>
        <thead><tr><th>Period</th>{groups.map(group => <th key={group.id}>{group.label}</th>)}<th>{unit}</th></tr></thead>
        <tbody>{rows.map(row => <tr key={row.label}><td>{row.label}</td>{row.point.values.map((value, index) => <td key={groups[index].id}>{value} ({percent(row.point.shares[index])})</td>)}<td>{row.point.total}</td></tr>)}</tbody>
      </table>
    )}
  >
    <div className="cc-bars" role="img" aria-label={`${title}. ${note}`}>
      {rows.map(row => (
        <div className="cc-bar-row" key={row.label}>
          <span className="cc-bar-label">{row.label}<small>{row.point.total} {unit}</small></span>
          <div className="cc-bar-track">
            {row.point.total === 0 ? <span className="cc-bar-none">none</span> : row.point.shares.map((share, index) => share > 0 && (
              <span key={groups[index].id} className="cc-bar-seg" style={{ width: `${share * 100}%`, background: groups[index].color }} title={`${groups[index].label}: ${row.point.values[index]} (${percent(share)})`}>
                {share >= 0.1 ? percent(share) : ''}
              </span>
            ))}
          </div>
        </div>
      ))}
    </div>
  </Figure>
);

const steps = (value: number): string => (Number.isInteger(value) ? String(value) : value.toFixed(1));

export const CounterCharts: React.FC<{ counters: CountersSummary }> = ({ counters }) => {
  const days = useMemo(() => periodDays(counters.days, new Date()), [counters.days]);
  const daily = counters.daily;
  const opening = useMemo(() => stackPoints(daily, days, OPENING_GROUPS), [daily, days]);
  const peaks = useMemo(() => stackPoints(daily, days, PEAK_GROUPS), [daily, days]);
  const allTurns = useMemo(() => averageLine(daily, days, 'code_first_edit_steps', 'code_first_edit_turns'), [daily, days]);
  const blockTurns = useMemo(() => averageLine(daily, days, 'orient_first_edit_steps', 'orient_first_edit_turns'), [daily, days]);
  const capHit = useMemo(() => ratioLine(daily, days, 'code_cap_hit', 'code_turns'), [daily, days]);
  return (
    <section className="service-card sh-card counter-charts">
      <div className="card-badge">CODING LOOP, LAST {counters.days} DAYS</div>
      <h2>Where coding turns go wrong</h2>
      <p className="card-description">Counts only, one point per day. A day with no turns is left empty, not drawn as zero.</p>
      <div className="cc-grid-layout">
        <StackedDays title="How a coding turn opens" note="Its first move, as a share of that day's turns." days={days} points={opening} groups={OPENING_GROUPS} unit="turns" />
        <LineDays
          title="Steps until the first edit" note="Average step at which the first edit was proposed. Lower means it gets to the point sooner." days={days} unit="turns" format={steps}
          series={[{ label: 'All coding turns', color: 'var(--cc-orange)', points: allTurns }, { label: 'Turns given the "where to look" block', color: 'var(--cc-blue)', points: blockTurns }]}
        />
        <ComparedBars
          title="Where the edited file ranked in the block" note="Turns given the block that applied an edit: was the file it changed one the block listed?" groups={RANK_GROUPS} unit="edit turns"
          rows={[{ label: 'This period', point: stackTotals(counters.totals, RANK_GROUPS) }, { label: 'Before', point: stackTotals(counters.previous_totals, RANK_GROUPS) }]}
        />
        <StackedDays title="Biggest prompt in a coding turn" note="Share of turns by their biggest single prompt. Over 100K tokens Haiku's price is 5x higher." days={days} points={peaks} groups={PEAK_GROUPS} unit="turns" />
        <LineDays
          title="Turns that ran out of steps" note="Share of coding turns that hit the step limit." days={days} unit="turns" format={value => percent(value)} axisMax={1}
          series={[{ label: 'Coding turns that hit the limit', color: 'var(--cc-orange)', points: capHit }]}
        />
      </div>
    </section>
  );
};

export default CounterCharts;

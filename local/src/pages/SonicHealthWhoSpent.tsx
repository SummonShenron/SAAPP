import React, { useState } from 'react';
import type { UsersSummary } from '../api';
import { barFraction, barItems, dayHeadline, money, percent, shortDay, shortName, tableRows } from './usersDayLayout';
import { tokens } from './sonicHealthFormat';

/** Which day to show: today (the newest) until someone picks another. The days come newest first in the menu. */
const DayPicker: React.FC<{ days: string[]; value: number; onChange: (index: number) => void; label: string }> = ({ days, value, onChange, label }) => (
  <select className="sh-select" aria-label={label} value={value} onChange={e => onChange(Number(e.target.value))}>
    {days.map((day, index) => ({ day, index })).reverse().map(({ day, index }) => (
      <option key={day} value={index}>{shortDay(day)}{index === days.length - 1 ? ' (today)' : ''}</option>
    ))}
  </select>
);

function dayLabel(days: string[], index: number): string {
  return index === days.length - 1 ? 'Today' : shortDay(days[index]);
}

/**
 * The dashboard's graph: for one day (today until another is picked), who used it and what each cost, as bars of the DAY's whole cost, so the length of a bar is
 * that account's share. You are in the app's orange; the biggest ten accounts are named, the rest are added up. Counts and dollars only; the page is admin-only.
 * The full list is the table on the Usage tab.
 */
export const WhoSpentTile: React.FC<{ users: UsersSummary | null | undefined; onOpen: (tab: string) => void }> = ({ users, onOpen }) => {
  const data = users?.daily_people;
  const [picked, setPicked] = useState<number | null>(null);
  if (users === undefined) return null;
  if (!data || data.by_day.length === 0) {
    return (
      <section className="sh-home-tile sh-chart-tile">
        <div className="sh-home-tile-head"><h3>Who spent what</h3></div>
        <div className="sh-hint">{users === null ? 'The per-account figures could not be read just now.' : 'Nothing to show yet: no prices are set, or no spend has been counted.'}</div>
      </section>
    );
  }
  const index = Math.min(picked ?? data.by_day.length - 1, data.by_day.length - 1);
  const day = data.by_day[index];
  const items = barItems(day);
  return (
    <section className="sh-home-tile sh-chart-tile sh-who">
      <div className="sh-home-tile-head">
        <h3>Who spent what</h3>
        <DayPicker days={data.days} value={index} onChange={setPicked} label="Day to show" />
        <button type="button" className="sh-link-btn" onClick={() => onOpen('usage')}>Full table ›</button>
      </div>
      <div className="sh-who-headline" aria-live="polite">{dayHeadline(day, data.viewer, dayLabel(data.days, index))}</div>
      {items.length === 0 ? (
        <div className="sh-hint">No one used Sonic on this day.</div>
      ) : (
        <ul className="sh-who-bars" aria-label={`Spend by account on ${shortDay(day.day)}, as a share of the day`}>
          {items.map(item => {
            const fraction = barFraction(item, day);
            return (
              <li key={item.key} className="sh-who-row" title={item.title}>
                <span className={`sh-who-label${item.kind === 'you' ? ' sh-who-label-you' : ''}`}>{item.label}</span>
                <span className="sh-who-track"><span className={`sh-who-fill sh-who-${item.kind}`} style={{ width: `${fraction > 0 ? Math.max(fraction * 100, 1.5) : 0}%` }} /></span>
                <span className="sh-who-value">{money(item.cost)}{item.share !== null ? ` · ${percent(item.share)}` : ''}</span>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
};

/** The Usage tab's full table: every account that used the chosen day, then the evals and the background jobs, with the day's total. */
export const WhoSpentTable: React.FC<{ users: UsersSummary | null | undefined }> = ({ users }) => {
  const data = users?.daily_people;
  const [picked, setPicked] = useState<number | null>(null);
  if (!data || data.by_day.length === 0) return null;
  const index = Math.min(picked ?? data.by_day.length - 1, data.by_day.length - 1);
  const day = data.by_day[index];
  const rows = tableRows(day);
  return (
    <section className="service-card sh-card">
      <div className="card-badge">{shortDay(day.day).toUpperCase()}</div>
      <h2>Every account, one day</h2>
      <p className="card-description">
        Who used the chosen day and what each cost, against the whole day's estimated cost. Counts and dollars only: no prompts, no replies. Admin-only. The share is of the
        day's total, which also holds the evals and the background jobs listed below the accounts.
      </p>
      <div className="sh-chart-head">
        <DayPicker days={data.days} value={index} onChange={setPicked} label="Day to show in the table" />
        <span className="sh-chart-summary">{dayHeadline(day, data.viewer, dayLabel(data.days, index))}</span>
      </div>
      {rows.length === 0 ? (
        <div className="sh-hint">No one used Sonic on this day.</div>
      ) : (
        <div className="sh-scroll">
          <table className="sh-table">
            <thead>
              <tr><th>Account</th><th>Cost</th><th>Share of day</th><th>Turns</th><th>Calls</th><th>Tokens</th></tr>
            </thead>
            <tbody>
              {rows.map(row => (
                <tr key={row.key} className={row.you ? 'sh-who-you-row' : undefined}>
                  <td title={row.account}>{row.isAccount ? shortName(row.account) : row.account}{row.you ? ' (you)' : ''}</td>
                  <td>{money(row.cost)}</td>
                  <td>{percent(row.share)}</td>
                  <td>{row.turns ?? ''}</td>
                  <td>{row.calls ?? ''}</td>
                  <td>{row.tokens !== null ? tokens(row.tokens) : ''}</td>
                </tr>
              ))}
              <tr className="sh-who-total-row"><td>The whole day</td><td>{money(day.total)}</td><td>{day.total > 0 ? '100%' : ''}</td><td /><td /><td /></tr>
            </tbody>
          </table>
        </div>
      )}
      {day.more_people > 0 && <div className="sh-hint">{day.more_people} more account{day.more_people === 1 ? '' : 's'} used this day and are not listed (the table shows the 60 biggest).</div>}
    </section>
  );
};

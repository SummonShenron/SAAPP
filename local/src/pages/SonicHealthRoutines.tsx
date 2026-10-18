import React from 'react';
import type { RoutineRow } from '../api';

function every(hours: number): string {
  if (hours >= 47) return `every ${Math.round(hours / 24)} days`;
  if (hours >= 23) return 'daily';
  if (hours === 1) return 'hourly';
  return `every ${Number.isInteger(hours) ? hours : hours.toFixed(1)} hours`;
}

function when(iso: string | null | undefined): string {
  if (!iso) return 'not yet';
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? 'not yet' : date.toLocaleString(undefined, { weekday: 'short', hour: 'numeric', minute: '2-digit' });
}

const STATUS_CLASS: Record<string, string> = { ok: '', attention: 'sh-err', failed: 'sh-err', skipped: '' };

export const RoutinesTab: React.FC<{ routines: RoutineRow[] | null | undefined }> = ({ routines }) => {
  if (!routines) return <div className="sh-hint">The routines could not be read just now.</div>;
  return (
    <div className="sh-stack">
      <section className="service-card sh-card">
        <h2>Routines</h2>
        <div className="sh-hint">
          The jobs the server runs on a schedule. This list is the schedule, and it is also what Sonic is allowed to say about its plans: asked what it
          has planned or what it did today, it answers from these rows and these results, in the person's own time, and says nothing that is not here.
          Only admins hear what the health check actually found; everyone else hears that it found nothing unusual or flagged something.
        </div>
        <table className="sh-table">
          <thead><tr><th>Routine</th><th>Runs</th><th>Last run</th><th>What it found</th><th>Next due</th></tr></thead>
          <tbody>
            {routines.map(row => (
              <tr key={row.id}>
                <td title={row.does}>{row.label}{row.uses_model && <span className="sh-pill">model</span>}</td>
                <td>{every(row.every_hours)}</td>
                <td>{when(row.last?.started_at)}</td>
                <td className={row.last ? STATUS_CLASS[row.last.status] : undefined}>{row.last ? row.last.result : 'has not run yet'}</td>
                <td>{when(row.next_due_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="sh-hint">Times are in your own timezone. A job that has not run yet has no promised time.</div>
      </section>
    </div>
  );
};

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { api, type UserTask, type TaskRun, type TaskSpec, type TasksResponse } from '../api';
import './__styles__/SelfService.css';
import './__styles__/Memory.css';
import './__styles__/Tasks.css';

const WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
const FALLBACK_SOURCE_LABELS: Record<string, string> = { web: 'The web', jooble: 'Jooble (job listings)', adzuna: 'Adzuna (job listings)' };
const JOB_SOURCES = ['jooble', 'adzuna'];
const POLL_EVERY_MS = 4000;
const POLL_FOR_MS = 120000;

const EMPTY_DRAFT: TaskSpec = { title: '', topic: '', details: '', cadence: 'weekly', hour: 8, weekday: 0, sources: ['web'], location: '' };

function hourLabel(hour: number): string {
  return `${hour % 12 || 12} ${hour < 12 ? 'AM' : 'PM'}`;
}

function formatWhen(iso: string | null): string {
  if (!iso) return '';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleString(undefined, { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
}

function statusLine(task: UserTask): string {
  if (task.status === 'paused') return task.paused_because === 'failing' ? 'Paused: it failed three times in a row' : 'Paused';
  const next = formatWhen(task.next_run_at);
  return next ? `Next run ${next}` : 'Scheduled';
}

function lastLine(task: UserTask): string {
  if (!task.last_run_at) return 'Has not run yet';
  const when = formatWhen(task.last_run_at);
  if (task.last_status === 'ok') return `Last run ${when}: found something new`;
  if (task.last_status === 'nothing_new') return `Last run ${when}: nothing new`;
  return `Last run ${when}: did not finish`;
}

export const TasksPage: React.FC = () => {
  const [data, setData] = useState<TasksResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [wizard, setWizard] = useState<number>(0); // 0 closed, 1 what, 2 when, 3 review
  const [draft, setDraft] = useState<TaskSpec>(EMPTY_DRAFT);
  const [creating, setCreating] = useState(false);
  const [wizardError, setWizardError] = useState('');
  const [openId, setOpenId] = useState<string | null>(null);
  const [runs, setRuns] = useState<TaskRun[]>([]);
  const [runsLoading, setRunsLoading] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [running, setRunning] = useState<Record<string, number>>({}); // task id -> its last_run_at when "run now" was pressed
  const mounted = useRef(true);
  const openIdRef = useRef<string | null>(null);

  const reload = useCallback(async () => {
    const next = await api.listTasks();
    setData(next);
    return next;
  }, []);

  useEffect(() => {
    let cancelled = false;
    api.listTasks()
      .then(next => { if (!cancelled) setData(next); })
      .catch((err: unknown) => { if (!cancelled) setError(err instanceof Error ? err.message : 'Failed to load your tasks.'); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; mounted.current = false; };
  }, []);

  const selectOpen = (id: string | null) => { openIdRef.current = id; setOpenId(id); };

  const openResults = useCallback(async (task: UserTask) => {
    if (openId === task.id) { selectOpen(null); return; }
    selectOpen(task.id);
    setRuns([]);
    setRunsLoading(true);
    try {
      const { runs: found } = await api.listTaskRuns(task.id);
      setRuns(found);
      if (task.waiting > 0) {
        await api.markTaskSeen(task.id);
        await reload();
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load what that task found.');
    } finally {
      setRunsLoading(false);
    }
  }, [openId, reload]);

  // Waits for a manual run to finish: the page checks back every few seconds, for up to two minutes.
  const waitForRun = async (taskId: string, before: string | null) => {
    const started = Date.now();
    while (mounted.current && Date.now() - started < POLL_FOR_MS) {
      await new Promise(resolve => window.setTimeout(resolve, POLL_EVERY_MS));
      try {
        const next = await reload();
        const task = next.tasks.find(t => t.id === taskId);
        if (task && task.last_run_at !== before) {
          if (openIdRef.current === taskId) setRuns((await api.listTaskRuns(taskId)).runs);
          break;
        }
      } catch { /* try again until the time is up */ }
    }
    if (mounted.current) setRunning(prev => Object.fromEntries(Object.entries(prev).filter(([id]) => id !== taskId)));
  };

  const handleRunNow = async (task: UserTask) => {
    setError('');
    setBusyId(task.id);
    try {
      await api.runTaskNow(task.id);
      setRunning(prev => ({ ...prev, [task.id]: Date.now() }));
      void waitForRun(task.id, task.last_run_at);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to start that task.');
    } finally {
      setBusyId(null);
    }
  };

  const handleToggle = async (task: UserTask) => {
    setError('');
    setBusyId(task.id);
    try {
      await api.updateTask(task.id, { status: task.status === 'active' ? 'paused' : 'active' });
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update that task.');
    } finally {
      setBusyId(null);
    }
  };

  const handleDelete = async (task: UserTask) => {
    if (!window.confirm(`Delete "${task.title}" and everything it found? This cannot be undone.`)) return;
    setError('');
    setBusyId(task.id);
    try {
      await api.deleteTask(task.id);
      if (openId === task.id) selectOpen(null);
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete that task.');
    } finally {
      setBusyId(null);
    }
  };

  const openWizard = () => { setDraft(EMPTY_DRAFT); setWizardError(''); setWizard(1); };
  const closeWizard = () => { setWizard(0); setWizardError(''); };

  const handleCreate = async () => {
    setCreating(true);
    setWizardError('');
    try {
      await api.createTask({ ...draft, title: draft.title.trim() || draft.topic.trim().slice(0, 60) });
      closeWizard();
      await reload();
    } catch (err) {
      setWizardError(err instanceof Error ? err.message : 'Failed to create that task.');
    } finally {
      setCreating(false);
    }
  };

  if (loading) return <div className="self-service-loading">Loading your tasks...</div>;

  const tasks = data?.tasks ?? [];
  const limits = data?.limits;
  const atLimit = !!limits && tasks.length >= limits.max_tasks;
  const patch = (changes: Partial<TaskSpec>) => setDraft(prev => ({ ...prev, ...changes }));
  const labels = { ...FALLBACK_SOURCE_LABELS, ...(limits?.source_labels ?? {}) };
  const wantsJobs = draft.sources.some(source => JOB_SOURCES.includes(source));

  return (
    <div className="self-service-container">
      <header className="self-service-header">
        <h1>Tasks</h1>
        <p>Things Sonic looks into for you on a schedule. What it finds waits here, and Sonic mentions it when you're back.</p>
      </header>

      <div className="vertical-card-stack">
        <section className="service-card memory-card">
          <div className="card-badge">{tasks.length}{limits ? ` OF ${limits.max_tasks}` : ''}</div>
          <h2>Your Tasks</h2>
          <p className="card-description">
            Each task searches the web at the time you pick and keeps what's new since the last time. Times are in your own timezone.
            {limits ? ` You can use ${limits.runs_per_day} runs a day (${limits.runs_today} used in the last 24 hours), including "Run now".` : ''}
          </p>
          {error && <div className="memory-edit-error" role="alert">{error}</div>}

          {tasks.length === 0 && wizard === 0 && (
            <div className="empty-manifest-notice">No tasks yet. Try something like "new remote Python developer jobs" every Monday morning.</div>
          )}

          <div className="memory-list tasks-list">
            {tasks.map(task => (
              <div key={task.id} className="task-item">
                <div className="memory-row task-row">
                  <div className="memory-row-main">
                    <div className="memory-row-badges">
                      <span className="memory-badge memory-source-badge">{task.schedule}</span>
                      {task.status === 'paused' && <span className="memory-badge memory-status-badge">paused</span>}
                      {task.waiting > 0 && <span className="memory-badge task-new-badge">{task.waiting} new</span>}
                    </div>
                    <div className="memory-row-text task-title">{task.title}</div>
                    <div className="task-meta">{task.topic}{task.details ? ` · ${task.details}` : ''}{task.location ? ` · near ${task.location}` : ''}</div>
                    <div className="task-meta">Looks at: {task.sources.map(source => labels[source] ?? source).join(', ')}</div>
                    <div className="task-meta">{statusLine(task)} · {lastLine(task)}</div>
                  </div>
                  <div className="task-actions">
                    <button type="button" className="memory-edit-save" onClick={() => openResults(task)}>
                      {openId === task.id ? 'Hide results' : 'Results'}
                    </button>
                    <button type="button" className="memory-edit-cancel" disabled={busyId === task.id || task.id in running || task.status === 'paused'} onClick={() => handleRunNow(task)}>
                      {task.id in running ? 'Running...' : 'Run now'}
                    </button>
                    <button type="button" className="memory-edit-cancel" disabled={busyId === task.id} onClick={() => handleToggle(task)}>
                      {task.status === 'active' ? 'Pause' : 'Resume'}
                    </button>
                    <button type="button" className="delete-row-btn" disabled={busyId === task.id} onClick={() => handleDelete(task)}>Delete</button>
                  </div>
                </div>

                {openId === task.id && (
                  <div className="task-results">
                    {runsLoading && <div className="task-meta">Loading...</div>}
                    {!runsLoading && runs.length === 0 && <div className="task-meta">It hasn't run yet. Use "Run now" to try it, or wait for {task.schedule}.</div>}
                    {runs.map(run => (
                      <div key={run.id} className="task-run">
                        <div className="task-run-head">
                          <span>{formatWhen(run.started_at)}{run.trigger === 'manual' ? ' · run by you' : ''}</span>
                        </div>
                        {run.status === 'failed' && <div className="task-meta">That run didn't finish, so nothing was found.</div>}
                        {run.status === 'nothing_new' && <div className="task-meta">Nothing new this time.</div>}
                        {run.status === 'ok' && (
                          <>
                            <div className="task-headline">{run.headline}</div>
                            <ul className="task-items">
                              {run.items.map((item, index) => (
                                <li key={`${run.id}-${index}`}>
                                  <a href={item.url} target="_blank" rel="noopener noreferrer">{item.title}</a>
                                  <span className="task-domain"> {item.domain}</span>
                                  <div className="task-note">{item.note}</div>
                                </li>
                              ))}
                            </ul>
                          </>
                        )}
                      </div>
                    ))}
                    {runs.some(r => r.status === 'ok') && (
                      <div className="memory-edit-hint">Summaries are written by Sonic from public pages and can be wrong. Check the page before you act on it.</div>
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>

          {wizard === 0 && (
            <div className="task-new-row">
              <button type="button" className="memory-edit-save" disabled={atLimit} onClick={openWizard}>New task</button>
              {atLimit && <span className="task-meta">You're at the limit of {limits?.max_tasks}. Delete one to add another.</span>}
            </div>
          )}
        </section>

        {wizard > 0 && (
          <section className="service-card memory-card">
            <div className="card-badge">STEP {wizard} OF 3</div>
            <h2>New Task</h2>

            {wizard === 1 && (
              <div className="task-form">
                <label className="task-label" htmlFor="task-topic">What should Sonic look for?</label>
                <input id="task-topic" className="manifest-search-input" type="text" maxLength={160} autoFocus value={draft.topic}
                  placeholder="e.g. new remote Python developer jobs" onChange={e => patch({ topic: e.target.value })} />
                <label className="task-label" htmlFor="task-details">Anything to narrow it down? <span className="task-optional">optional</span></label>
                <input id="task-details" className="manifest-search-input" type="text" maxLength={240} value={draft.details}
                  placeholder="e.g. senior roles only, no contract work" onChange={e => patch({ details: e.target.value })} />
                <label className="task-label" htmlFor="task-title">Give it a name <span className="task-optional">optional</span></label>
                <input id="task-title" className="manifest-search-input" type="text" maxLength={60} value={draft.title}
                  placeholder="e.g. Python jobs" onChange={e => patch({ title: e.target.value })} />
                <div className="memory-edit-hint">Describe what you want rather than pasting links: Sonic does the searching. It can't run tasks about health, legal or money trouble, or about following a private person.</div>
              </div>
            )}

            {wizard === 2 && (
              <div className="task-form">
                <label className="task-label" htmlFor="task-cadence">How often?</label>
                <select id="task-cadence" className="memory-category-select" value={draft.cadence} onChange={e => patch({ cadence: e.target.value as 'daily' | 'weekly' })}>
                  <option value="daily">Every day</option>
                  <option value="weekly">Every week</option>
                </select>
                {draft.cadence === 'weekly' && (
                  <>
                    <label className="task-label" htmlFor="task-weekday">On</label>
                    <select id="task-weekday" className="memory-category-select" value={draft.weekday} onChange={e => patch({ weekday: Number(e.target.value) })}>
                      {WEEKDAYS.map((day, index) => <option key={day} value={index}>{day}</option>)}
                    </select>
                  </>
                )}
                <label className="task-label" htmlFor="task-hour">Around</label>
                <select id="task-hour" className="memory-category-select" value={draft.hour} onChange={e => patch({ hour: Number(e.target.value) })}>
                  {Array.from({ length: 24 }, (_, hour) => <option key={hour} value={hour}>{hourLabel(hour)}</option>)}
                </select>
                <label className="task-label">Where to look</label>
                <div className="task-sources">
                  {(limits?.sources ?? ['web']).map(source => (
                    <label key={source} className="task-source">
                      <input type="checkbox" checked={draft.sources.includes(source)}
                        onChange={e => patch({ sources: e.target.checked ? [...draft.sources, source] : draft.sources.filter(s => s !== source) })} />
                      {labels[source] ?? source}
                    </label>
                  ))}
                </div>
                {wantsJobs && (
                  <>
                    <label className="task-label" htmlFor="task-location">Where should the jobs be? <span className="task-optional">optional</span></label>
                    <input id="task-location" className="manifest-search-input" type="text" maxLength={80} value={draft.location}
                      placeholder="e.g. Austin, TX, or Remote" onChange={e => patch({ location: e.target.value })} />
                  </>
                )}
              </div>
            )}

            {wizard === 3 && (
              <div className="task-form">
                <div className="task-review">
                  <div><strong>Looking for:</strong> {draft.topic}</div>
                  {draft.details && <div><strong>Narrowed by:</strong> {draft.details}</div>}
                  <div><strong>Runs:</strong> {draft.cadence === 'weekly' ? `every ${WEEKDAYS[draft.weekday]}` : 'every day'} around {hourLabel(draft.hour)}</div>
                  <div><strong>Where:</strong> {draft.sources.map(s => labels[s] ?? s).join(', ')}</div>
                  {wantsJobs && draft.location.trim() && <div><strong>Jobs near:</strong> {draft.location}</div>}
                </div>
                <div className="memory-edit-hint">Sonic reads a handful of public pages each time and keeps only what's new since the last run. It uses a little of your daily allowance each run.</div>
              </div>
            )}

            {wizardError && <div className="memory-edit-error" role="alert">{wizardError}</div>}
            <div className="memory-edit-row task-wizard-nav">
              <button type="button" className="memory-edit-cancel" onClick={wizard === 1 ? closeWizard : () => setWizard(wizard - 1)} disabled={creating}>
                {wizard === 1 ? 'Cancel' : 'Back'}
              </button>
              {wizard < 3 ? (
                <button type="button" className="memory-edit-save" disabled={wizard === 1 && draft.topic.trim().length < 4 || wizard === 2 && draft.sources.length === 0} onClick={() => setWizard(wizard + 1)}>Next</button>
              ) : (
                <button type="button" className="memory-edit-save" disabled={creating} onClick={handleCreate}>{creating ? 'Creating...' : 'Create task'}</button>
              )}
            </div>
          </section>
        )}
      </div>
    </div>
  );
};

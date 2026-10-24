import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api, type TaskKind, type UserTask, type TaskRun, type TaskSpec, type TasksResponse } from '../api';
import './__styles__/SelfService.css';
import './__styles__/Memory.css';
import './__styles__/Tasks.css';

const WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
const FALLBACK_SOURCE_LABELS: Record<string, string> = { web: 'The web', jooble: 'Jooble (job listings)', adzuna: 'Adzuna (job listings)' };
const JOB_SOURCES = ['jooble', 'adzuna'];
const POLL_EVERY_MS = 4000;
const POLL_FOR_MS = 120000;

const EMPTY_DRAFT: TaskSpec = { kind: 'research', repo: '', title: '', topic: '', details: '', cadence: 'weekly', hour: 8, weekday: 0, window_hours: 0, sources: ['web'], location: '' };
const WINDOW_CHOICES: { hours: number; label: string }[] = [
  { hours: 0, label: 'At that time' },
  { hours: 2, label: 'Sometime in the 2 hours after, at a quiet moment' },
  { hours: 3, label: 'Sometime in the 3 hours after, at a quiet moment' },
];
const KIND_CHOICES: { kind: TaskKind; label: string; blurb: string }[] = [
  { kind: 'research', label: 'Look something up', blurb: 'Searches the web (and job boards) for what you describe, and keeps what is new.' },
  { kind: 'pr_review', label: 'Review my pull requests', blurb: 'Reads your repository\'s open pull requests and tells you what a careful reviewer would flag.' },
  { kind: 'code_audit', label: 'Audit my code', blurb: 'Scans your repository for security problems and risky code, and tells you what is worth a look.' },
];
const REPO_KINDS: TaskKind[] = ['pr_review', 'code_audit'];
const MAX_EXTRA_SEARCHES = 3;
const REPO_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,38}\/[A-Za-z0-9_.-]{1,100}$/;

function hourLabel(hour: number): string {
  return `${hour % 12 || 12} ${hour < 12 ? 'AM' : 'PM'}`;
}

/** "around 8 AM", or for a window "between 8 and 10 AM, at a quiet moment" (the server words a saved task the same way, in `schedule`). */
function windowLabel(hour: number, windowHours: number): string {
  if (windowHours <= 0) return `around ${hourLabel(hour)}`;
  const end = (hour + windowHours) % 24;
  const sameHalf = (hour % 24 < 12) === (end < 12);
  return `between ${sameHalf ? hour % 12 || 12 : hourLabel(hour)} and ${hourLabel(end)}, at a quiet moment`;
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
  const navigate = useNavigate();
  const [data, setData] = useState<TasksResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [wizard, setWizard] = useState<number>(0); // 0 closed, 1 kind, 2 what, 3 when, 4 review
  const [draft, setDraft] = useState<TaskSpec>(EMPTY_DRAFT);
  const [editingId, setEditingId] = useState<string | null>(null);
  const wizardRef = useRef<HTMLElement | null>(null);
  const isRepo = REPO_KINDS.includes(draft.kind ?? 'research');
  const extras = draft.extra_topics ?? [];
  const extrasOk = extras.every(t => !t.trim() || t.trim().length >= 4);
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

  const openWizard = () => { setDraft(EMPTY_DRAFT); setWizardError(''); setEditingId(null); setWizard(1); };
  const closeWizard = () => { setWizard(0); setWizardError(''); setEditingId(null); };

  // The form opens below the list, so bring it into view when an Edit button far above it opens it.
  useEffect(() => {
    if (editingId) wizardRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }, [editingId]);

  const openEdit = (task: UserTask) => {
    setDraft({
      ...EMPTY_DRAFT,
      kind: task.kind,
      title: task.title ?? '',
      topic: task.topic ?? '',
      extra_topics: task.extra_topics ?? [],
      details: task.details ?? '',
      cadence: task.cadence,
      hour: task.hour,
      weekday: task.weekday ?? 0,
      window_hours: task.window_hours ?? 0,
      sources: task.sources ?? ['web'],
      location: task.location ?? '',
      repo: task.repo ?? '',
    });
    setEditingId(task.id);
    setWizardError('');
    setWizard(2);
  };

  const handleCreate = async () => {
    setCreating(true);
    setWizardError('');
    try {
      const spec: TaskSpec = isRepo
        ? { ...draft, repo: (draft.repo ?? '').trim(), title: draft.title.trim(), cadence: draft.kind === 'code_audit' ? 'weekly' : draft.cadence }
        : { ...draft, title: draft.title.trim() || draft.topic.trim().slice(0, 60), extra_topics: extras.map(t => t.trim()).filter(Boolean) };
      if (editingId) {
        await api.updateTask(editingId, spec);
      } else {
        await api.createTask(spec);
      }
      closeWizard();
      await reload();
    } catch (err) {
      setWizardError(err instanceof Error ? err.message : editingId ? 'Failed to save those changes.' : 'Failed to create that task.');
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
  const wantsJobs = !isRepo && draft.sources.some(source => JOB_SOURCES.includes(source));
  const githubConnected = limits?.github_connected !== false;
  const repoOk = REPO_PATTERN.test((draft.repo ?? '').trim());
  const kindLabel = KIND_CHOICES.find(choice => choice.kind === draft.kind)?.label ?? '';

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
            Each task runs at the time you pick and keeps what's new since the last time: a web search, or a read-only look at your own repository. Times are in your own timezone.
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
                    {REPO_KINDS.includes(task.kind) ? (
                      <>
                        <div className="task-meta">{task.kind === 'pr_review' ? 'Reviews the open pull requests in' : 'Audits the code in'} {task.repo}{task.details ? ` · ${task.details}` : ''}</div>
                        <div className="task-meta">Reads with your own GitHub token, read-only. It never comments, opens or changes anything.</div>
                      </>
                    ) : (
                      <>
                        <div className="task-meta">{[task.topic, ...(task.extra_topics ?? [])].join('; ')}{task.details ? ` · ${task.details}` : ''}{task.location ? ` · near ${task.location}` : ''}</div>
                        <div className="task-meta">Looks at: {task.sources.map(source => labels[source] ?? source).join(', ')}</div>
                      </>
                    )}
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
                    <button type="button" className="memory-edit-cancel" disabled={busyId === task.id} onClick={() => openEdit(task)}>Edit</button>
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
                        {run.reason && <div className="task-meta">{run.reason}</div>}
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
                      <div className="memory-edit-hint">
                        {REPO_KINDS.includes(task.kind)
                          ? 'These notes are written by an AI model from your code and pull requests and can be wrong or miss things. Check the code before you act on one.'
                          : 'Summaries are written by Sonic from public pages and can be wrong. Check the page before you act on it.'}
                      </div>
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
          <section className="service-card memory-card" ref={wizardRef}>
            <div className="card-badge">STEP {wizard} OF 4</div>
            <h2>{editingId ? 'Edit Task' : 'New Task'}</h2>

            {wizard === 1 && (
              <div className="task-form">
                <div className="task-label">What kind of task?</div>
                <div className="task-kinds" role="radiogroup" aria-label="Kind of task">
                  {KIND_CHOICES.map(choice => (
                    <label key={choice.kind} className={`task-kind${draft.kind === choice.kind ? ' task-kind-on' : ''}`}>
                      <input type="radio" name="task-kind" checked={draft.kind === choice.kind}
                        onChange={() => patch({ kind: choice.kind, cadence: choice.kind === 'code_audit' ? 'weekly' : draft.cadence })} />
                      <span className="task-kind-label">{choice.label}</span>
                      <span className="task-meta">{choice.blurb}</span>
                    </label>
                  ))}
                </div>
              </div>
            )}

            {wizard === 2 && isRepo && (
              <div className="task-form">
                {!githubConnected && (
                  <div className="memory-edit-error" role="alert">
                    These tasks read your repository with your own GitHub token, so they can only see what you can. Add one first.{' '}
                    <button type="button" className="memory-edit-cancel" onClick={() => navigate('/integrations')}>Open Integrations</button>
                  </div>
                )}
                <label className="task-label" htmlFor="task-repo">Which repository?</label>
                <input id="task-repo" className="manifest-search-input" type="text" maxLength={141} autoFocus value={draft.repo ?? ''}
                  placeholder="owner/name, e.g. SummonShenron/SAAPP" onChange={e => patch({ repo: e.target.value })} />
                <label className="task-label" htmlFor="task-details-repo">Anything to focus on? <span className="task-optional">optional</span></label>
                <input id="task-details-repo" className="manifest-search-input" type="text" maxLength={240} value={draft.details}
                  placeholder="e.g. authentication and input handling" onChange={e => patch({ details: e.target.value })} />
                <label className="task-label" htmlFor="task-title-repo">Give it a name <span className="task-optional">optional</span></label>
                <input id="task-title-repo" className="manifest-search-input" type="text" maxLength={60} value={draft.title}
                  placeholder={draft.kind === 'pr_review' ? 'e.g. Weekly PR review' : 'e.g. Weekly security audit'} onChange={e => patch({ title: e.target.value })} />
                <div className="memory-edit-hint">
                  Sonic only reads: it never comments, opens issues, pushes or changes anything. To do the work it sends {draft.kind === 'pr_review' ? "each pull request's changes" : 'short excerpts of your code'} to
                  the AI model it answers with, so only use it on code you are happy to share with that service.
                </div>
              </div>
            )}

            {wizard === 2 && !isRepo && (
              <div className="task-form">
                <label className="task-label" htmlFor="task-topic">What should Sonic look for?</label>
                <input id="task-topic" className="manifest-search-input" type="text" maxLength={160} autoFocus value={draft.topic}
                  placeholder="e.g. new remote Python developer jobs" onChange={e => patch({ topic: e.target.value })} />
                {extras.map((extra, index) => (
                  <div key={index} className="task-extra-row">
                    <input className="manifest-search-input" type="text" maxLength={160} value={extra} aria-label={`Another search ${index + 1}`}
                      placeholder="e.g. forward deployed engineer" onChange={e => patch({ extra_topics: extras.map((t, i) => (i === index ? e.target.value : t)) })} />
                    <button type="button" className="memory-edit-cancel" aria-label={`Remove search ${index + 1}`}
                      onClick={() => patch({ extra_topics: extras.filter((_, i) => i !== index) })}>Remove</button>
                  </div>
                ))}
                {extras.length < MAX_EXTRA_SEARCHES && (
                  <button type="button" className="memory-edit-cancel task-add-search" onClick={() => patch({ extra_topics: [...extras, ''] })}>+ Add another search</button>
                )}
                {extras.length > 0 && <div className="memory-edit-hint">Each search runs in the same run and counts as this one task, so a job hunt for several titles needs only one.</div>}
                <label className="task-label" htmlFor="task-details">Anything to narrow it down? <span className="task-optional">optional</span></label>
                <input id="task-details" className="manifest-search-input" type="text" maxLength={240} value={draft.details}
                  placeholder="e.g. senior roles only, no contract work" onChange={e => patch({ details: e.target.value })} />
                <label className="task-label" htmlFor="task-title">Give it a name <span className="task-optional">optional</span></label>
                <input id="task-title" className="manifest-search-input" type="text" maxLength={60} value={draft.title}
                  placeholder="e.g. Python jobs" onChange={e => patch({ title: e.target.value })} />
                <div className="memory-edit-hint">Describe what you want rather than pasting links: Sonic does the searching. It can't run tasks about health, legal or money trouble, or about following a private person.</div>
              </div>
            )}

            {wizard === 3 && (
              <div className="task-form">
                <label className="task-label" htmlFor="task-cadence">How often?</label>
                <select id="task-cadence" className="memory-category-select" value={draft.kind === 'code_audit' ? 'weekly' : draft.cadence} disabled={draft.kind === 'code_audit'}
                  onChange={e => patch({ cadence: e.target.value as 'daily' | 'weekly' })}>
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
                <label className="task-label" htmlFor="task-window">Run it <span className="task-optional">optional</span></label>
                <select id="task-window" className="memory-category-select" value={draft.window_hours ?? 0} onChange={e => patch({ window_hours: Number(e.target.value) })}>
                  {WINDOW_CHOICES.map(choice => <option key={choice.hours} value={choice.hours}>{choice.label}</option>)}
                </select>
                {(draft.window_hours ?? 0) > 0 && (
                  <div className="memory-edit-hint">
                    Sonic waits until you've been away from the app for a little while and there's room in the usage budget, and runs it at the end of the window either way.
                    Each run says why it ran when it did.
                  </div>
                )}
                {!isRepo && <label className="task-label">Where to look</label>}
                <div className="task-sources">
                  {!isRepo && (limits?.sources ?? ['web']).map(source => (
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

            {wizard === 4 && (
              <div className="task-form">
                <div className="task-review">
                  {isRepo ? (
                    <>
                      <div><strong>Task:</strong> {kindLabel}</div>
                      <div><strong>Repository:</strong> {(draft.repo ?? '').trim()}</div>
                      {draft.details && <div><strong>Focus:</strong> {draft.details}</div>}
                    </>
                  ) : (
                    <>
                      <div><strong>Looking for:</strong> {[draft.topic, ...extras.map(t => t.trim()).filter(Boolean)].join('; ')}</div>
                      {draft.details && <div><strong>Narrowed by:</strong> {draft.details}</div>}
                    </>
                  )}
                  <div><strong>Runs:</strong> {(draft.kind === 'code_audit' ? 'weekly' : draft.cadence) === 'weekly' ? `every ${WEEKDAYS[draft.weekday]}` : 'every day'} {windowLabel(draft.hour, draft.window_hours ?? 0)}</div>
                  {!isRepo && <div><strong>Where:</strong> {draft.sources.map(s => labels[s] ?? s).join(', ')}</div>}
                  {wantsJobs && draft.location.trim() && <div><strong>Jobs near:</strong> {draft.location}</div>}
                </div>
                <div className="memory-edit-hint">
                  {isRepo
                    ? 'Read-only, with your own GitHub token. Each pull request is reviewed once per commit, and each finding is reported once. It uses a little of your daily allowance each run.'
                    : "Sonic reads a handful of public pages each time and keeps only what's new since the last run. It uses a little of your daily allowance each run."}
                </div>
              </div>
            )}

            {wizardError && <div className="memory-edit-error" role="alert">{wizardError}</div>}
            <div className="memory-edit-row task-wizard-nav">
              <button type="button" className="memory-edit-cancel" onClick={wizard === 1 || (editingId && wizard === 2) ? closeWizard : () => setWizard(wizard - 1)} disabled={creating}>
                {wizard === 1 || (editingId && wizard === 2) ? 'Cancel' : 'Back'}
              </button>
              {wizard < 4 ? (
                <button type="button" className="memory-edit-save"
                  disabled={wizard === 2 && (isRepo ? !repoOk || !githubConnected : draft.topic.trim().length < 4 || !extrasOk) || wizard === 3 && !isRepo && draft.sources.length === 0}
                  onClick={() => setWizard(wizard + 1)}>Next</button>
              ) : (
                <button type="button" className="memory-edit-save" disabled={creating} onClick={handleCreate}>{creating ? (editingId ? 'Saving...' : 'Creating...') : (editingId ? 'Save changes' : 'Create task')}</button>
              )}
            </div>
          </section>
        )}
      </div>
    </div>
  );
};

import { useCallback, useEffect, useState } from 'react';
import { api, type ProjectSettings, type RunnerInfo, type RunnerList, type RunnerPairing, type RunnerProject, type RunnerScript } from '../api';
import { OS_LABEL, checkIsOn, detectOs, launchHint, minutesLeft, noChecksLine, notConnectedReason, requestSentence, requestsOpen, requestsToShow, runHint, statusSentence, updateNote, withAutoUpdate, withCheck, withFakeKeys, withReadOnlyAuto, type OsKind } from '../runnerLogic';
import './__styles__/RunnerSettingsCard.css';

const POLL_WHILE_PAIRING_MS = 4000;

function download(script: RunnerScript): void {
  const url = URL.createObjectURL(new Blob([script.text], { type: 'text/plain' }));
  const link = document.createElement('a');
  link.href = url;
  link.download = script.filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/**
 * Asks for a project to be added to one of the person's computers. Only the folder's NAME goes to SAAPP; the runner then shows a window on that computer where the person chooses the folder (or says no).
 */
function AddProjectForm({ runner, disabled, onAsked, onError }: { runner: RunnerInfo; disabled: boolean; onAsked: () => Promise<void>; onError: (message: string) => void }) {
  const [folder, setFolder] = useState('');
  const [asking, setAsking] = useState(false);
  const ask = async () => {
    const name = folder.trim();
    if (!name) return;
    setAsking(true);
    try {
      await api.addRunnerProject(runner.id, name);
      setFolder('');
      await onAsked();
    } catch (problem) {
      onError(problem instanceof Error ? problem.message : 'Could not ask for that project.');
    } finally {
      setAsking(false);
    }
  };
  return (
    <div className="runner-add runner-add-project">
      <input className="account-input" type="text" maxLength={80} placeholder="Project folder name, for example my-app" aria-label={`Name of a project folder to add on ${runner.name}`} value={folder}
        disabled={disabled || asking} onChange={event => setFolder(event.target.value)} onKeyDown={event => { if (event.key === 'Enter' && !asking && !disabled) void ask(); }} />
      <button type="button" className="account-btn" disabled={disabled || asking || !folder.trim()} onClick={() => void ask()}>{asking ? 'Asking...' : 'Add a project'}</button>
    </div>
  );
}

/**
 * Settings, "Run checks on your machine" (backend/utils/runner_jobs.py, runner/). An optional small program on the person's own computer that runs their project's tests, type checks or builds when Sonic
 * asks and they press Run on a card. Here a person adds a runner (a one-time code and a starter script for their system), sees whether each is connected, and removes one (its key stops working at once).
 */
export default function RunnerSettingsCard() {
  const [list, setList] = useState<RunnerList | null>(null);
  const [pairing, setPairing] = useState<RunnerPairing | null>(null);
  const [name, setName] = useState('');
  const [os, setOs] = useState<OsKind>(() => detectOs(typeof navigator === 'undefined' ? '' : navigator.platform || '', typeof navigator === 'undefined' ? '' : navigator.userAgent));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [copied, setCopied] = useState('');
  const [now, setNow] = useState(() => Date.now());
  const [saving, setSaving] = useState('');

  const load = useCallback(async () => {
    try {
      setList(await api.listRunners());
      setError('');
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : 'Could not load your runners.');
    }
  }, []);

  // The first load, written as a promise chain so no state is set inside the effect itself (the same shape the other settings cards use). Later loads (after an add or a remove, and while a code is showing) call `load`.
  useEffect(() => {
    let cancelled = false;
    api.listRunners()
      .then(result => { if (!cancelled) setList(result); })
      .catch(problem => { if (!cancelled) setError(problem instanceof Error ? problem.message : 'Could not load your runners.'); });
    return () => { cancelled = true; };
  }, []);

  // While an update the person asked for is under way, look again every few seconds so the runner reads "Connected" on its new version as soon as it is back.
  // The same while a request to add a project is waiting for the person to answer the window on their computer: the project then shows up by itself.
  const updating = !!list && list.runners.some(runner => runner.update.pending || requestsOpen(runner.requests));
  useEffect(() => {
    if (!updating) return undefined;
    const timer = window.setInterval(() => { void load(); }, POLL_WHILE_PAIRING_MS);
    return () => window.clearInterval(timer);
  }, [updating, load]);

  // "Last seen 3 hours ago" stays true as time passes, so the page's clock moves on every half minute.
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 30000);
    return () => window.clearInterval(timer);
  }, []);

  // While a code is showing, look for the new runner connecting (it then reads "Connected" without a reload) and keep the minutes left up to date.
  useEffect(() => {
    if (!pairing) return undefined;
    const timer = window.setInterval(() => { setNow(Date.now()); void load(); }, POLL_WHILE_PAIRING_MS);
    return () => window.clearInterval(timer);
  }, [pairing, load]);

  const add = async () => {
    setBusy(true);
    setError('');
    try {
      const label = name.trim() || (list && list.runners.length ? `runner ${list.runners.length + 1}` : 'my computer');
      setPairing(await api.createRunnerPairing(label));
      setNow(Date.now());
      setName('');
      await load();
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : 'Could not make a code.');
    } finally {
      setBusy(false);
    }
  };

  const remove = async (id: string, label: string) => {
    if (!window.confirm(`Remove "${label}"? Its key stops working at once, and any check waiting on it is cancelled.`)) return;
    setError('');
    try {
      await api.removeRunner(id);
      await load();
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : 'Could not remove it.');
    }
  };

  // A tick shows at once and is saved in the background; if the save fails the list goes back to how it was and says so.
  const save = async (runner: RunnerInfo, show: (current: RunnerList) => RunnerList, send: () => Promise<unknown>) => {
    const before = list;
    setList(current => current && show(current));
    setSaving(runner.id);
    setError('');
    try {
      await send();
    } catch (problem) {
      setList(before);
      setError(problem instanceof Error ? problem.message : 'Could not save that.');
    } finally {
      setSaving('');
    }
  };

  // A choice for ONE project: which of its checks are on, and whether they run with a fake AI key.
  const changeProject = (runner: RunnerInfo, project: RunnerProject, next: ProjectSettings) =>
    save(runner, current => ({ ...current, runners: current.runners.map(item => (item.id !== runner.id ? item : { ...item, projects: item.projects.map(p => (p.id === project.id ? { ...p, settings: next } : p)) })) }),
      () => api.updateRunnerSettings(runner.id, { project_id: project.id, disabled: next.disabled, fake_keys: next.fake_keys }));

  // The runner's own choice: whether it updates itself without a click.
  const changeAutoUpdate = (runner: RunnerInfo, on: boolean) =>
    save(runner, current => ({ ...current, runners: current.runners.map(item => (item.id === runner.id ? { ...item, settings: withAutoUpdate(item.settings, on) } : item)) }),
      () => api.updateRunnerSettings(runner.id, { auto_update: on }));

  // The computer's own choice: whether command lines that only look around run without a click on a card.
  const changeReadOnlyAuto = (runner: RunnerInfo, on: boolean) =>
    save(runner, current => ({ ...current, runners: current.runners.map(item => (item.id === runner.id ? { ...item, settings: withReadOnlyAuto(item.settings, on) } : item)) }),
      () => api.updateRunnerSettings(runner.id, { read_only_auto: on }));

  const removeProject = async (runner: RunnerInfo, project: RunnerProject) => {
    if (!window.confirm(`Stop serving "${project.name}" on ${runner.name}? Nothing in the folder is touched, and no check can be run in it until you add it again.`)) return;
    setError('');
    try {
      await api.removeRunnerProject(runner.id, project.id);
      await load();
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : 'Could not remove that project.');
    }
  };

  // Pressing Update tells the runner to update the next time it asks (when no check is running); it installs from where it was installed and restarts. The page then watches for it to come back.
  const update = async (runner: RunnerInfo) => {
    setSaving(runner.id);
    setError('');
    try {
      await api.requestRunnerUpdate(runner.id);
      await load();
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : 'Could not ask the runner to update.');
    } finally {
      setSaving('');
    }
  };

  const copy = async (what: string, text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(what);
      window.setTimeout(() => setCopied(''), 2000);
    } catch {
      setCopied('');
    }
  };

  const atLimit = !!list && list.runners.length >= list.limit;
  const scripts = pairing?.scripts ?? null;
  const left = pairing ? minutesLeft(pairing.expires_at, now) : 0;

  return (
    <section className="account-card runner-card" aria-labelledby="runner-heading">
      <h3 id="runner-heading">Run checks on your machine</h3>
      <p className="account-note runner-intro">
        Optional. A small program on your own computer, the SAAPP runner, can run your project's tests or type check when Sonic asks and you press Run on a card in the chat. It finds those checks in your
        project by itself, so there is nothing to configure: turn any of them off below. It never runs a command Sonic or SAAPP supplies, and only after you approve each run.
      </p>

      {list && list.runners.length === 0 && <p className="account-hint">You have no runners yet. Add one on each computer whose projects you want Sonic to be able to check.</p>}
      {list && list.runners.length > 0 && (
        <ul className="runner-list">
          {list.runners.map(runner => (
            <li key={runner.id} className="runner-row">
              <div className="runner-main">
                <div className="account-label">{runner.name}</div>
                <div className="account-hint">
                  <span className={`runner-dot ${runner.connected ? 'runner-dot-on' : ''}`} aria-hidden="true" />
                  {runner.connected ? 'Connected' : 'Not connected'}
                  {runner.version ? ` · version ${runner.version}` : ''}
                  {runner.projects.length ? ` · ${runner.projects.length} project${runner.projects.length === 1 ? '' : 's'}` : ''}
                </div>
                {runner.connected && statusSentence(runner.status) && <div className="account-hint runner-warning" role="status">{statusSentence(runner.status)}</div>}
                {runner.connected && launchHint(runner.launch) && <div className="account-hint">{launchHint(runner.launch)}</div>}
                {!runner.connected && <div className="account-hint">{notConnectedReason(runner.last_seen, now)}</div>}
                {updateNote(runner) && (
                  <div className="account-hint runner-update">
                    {updateNote(runner)!.text}
                    {updateNote(runner)!.canPress && (
                      <button type="button" className="account-btn runner-update-button" disabled={saving === runner.id} onClick={() => void update(runner)}>Update to {runner.update.wanted}</button>
                    )}
                  </div>
                )}
              </div>
              <button type="button" className="account-btn" onClick={() => void remove(runner.id, runner.name)}>Remove</button>

              <div className="runner-projects">
                {runner.projects.length === 0 && <p className="account-hint">No project yet. Add one below: a window opens on that computer and you choose the folder there.</p>}
                {runner.projects.map(project => (
                  <section key={project.id} className="runner-project" aria-label={`Project ${project.name}`}>
                    <div className="runner-project-head">
                      <div className="account-label">{project.name}</div>
                      <button type="button" className="runner-link" onClick={() => void removeProject(runner, project)}>Remove project</button>
                    </div>
                    {runner.connected && statusSentence(project.status) && <div className="account-hint runner-warning" role="status">{statusSentence(project.status)}</div>}
                    {project.checks.length === 0 && !(runner.connected && statusSentence(project.status)) && <div className="account-hint">{noChecksLine(runner.connected)}</div>}
                    {project.checks.map(check => (
                      <label key={check.name} className="runner-check">
                        <input type="checkbox" checked={checkIsOn(project.settings, check.name)} disabled={saving === runner.id}
                          onChange={event => void changeProject(runner, project, withCheck(project.settings, check.name, event.target.checked))} />
                        <span className="runner-check-text">
                          <span className="account-label">{check.name}</span>
                          <code className="runner-check-command">{check.command}</code>
                          {check.ready === false && <span className="account-hint runner-warning">Its program can't be found on that computer, so this check would fail.</span>}
                        </span>
                      </label>
                    ))}
                    <label className="runner-check">
                      <input type="checkbox" checked={project.settings.fake_keys} disabled={saving === runner.id}
                        onChange={event => void changeProject(runner, project, withFakeKeys(project.settings, event.target.checked))} />
                      <span className="runner-check-text">
                        <span className="account-label">Use a fake AI key while this project's checks run</span>
                        <span className="account-hint">Keeps a test from spending real money: GOOGLE_API_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY and GEMINI_API_KEY are set to a made-up value for the run, even if the project's .env file holds a real one.</span>
                      </span>
                    </label>
                  </section>
                ))}
                {requestsToShow(runner.requests).map(request => (
                  <div key={request.id} className={`account-hint runner-request runner-request-${request.state}`} role="status">{requestSentence(request)}</div>
                ))}
                <AddProjectForm runner={runner} disabled={!runner.connected} onAsked={load} onError={setError} />
                {!runner.connected && <p className="account-hint">Start this runner first; then you can add a project.</p>}
                {runner.commands && (
                  <label className="runner-check">
                    <input type="checkbox" checked={runner.settings.read_only_auto} disabled={saving === runner.id} onChange={event => void changeReadOnlyAuto(runner, event.target.checked)} />
                    <span className="runner-check-text">
                      <span className="account-label">Run commands that only look around without asking</span>
                      <span className="account-hint">Commands such as git status, git diff, ls, cat and grep -rn skip the card's Run button, so Sonic can look at your project without a click each time. Anything that runs a program, writes a file or changes git still asks, and a command that changes things asks twice. What they print is shown to Sonic. Off by default.</span>
                    </span>
                  </label>
                )}
                <label className="runner-check">
                  <input type="checkbox" checked={runner.settings.auto_update} disabled={saving === runner.id} onChange={event => void changeAutoUpdate(runner, event.target.checked)} />
                  <span className="runner-check-text">
                    <span className="account-label">Update this runner automatically</span>
                    <span className="account-hint">When a newer version is available it installs it by itself, only while no check is running, then restarts. Off by default: until you turn it on, an update waits for you to press Update.</span>
                  </span>
                </label>
              </div>
            </li>
          ))}
        </ul>
      )}

      <div className="runner-add">
        <input className="account-input" type="text" maxLength={40} placeholder="Name this computer (optional)" aria-label="Name for the new runner" value={name}
          disabled={busy || atLimit} onChange={event => setName(event.target.value)} onKeyDown={event => { if (event.key === 'Enter' && !busy && !atLimit) void add(); }} />
        <button type="button" className="account-btn" disabled={busy || atLimit || !list} onClick={() => void add()}>{busy ? 'Making a code...' : 'Add a runner'}</button>
      </div>
      {atLimit && list && <p className="account-hint">You can have {list.limit} runners at most. Remove one you no longer use to add another.</p>}
      {error && <div className="account-error" role="alert">{error}</div>}

      {pairing && (
        <div className="runner-pairing">
          <div className="runner-code-line">
            <span className="account-label">Your one-time code</span>
            <code className="runner-code" aria-label="Pairing code">{pairing.code}</code>
            <button type="button" className="account-btn" onClick={() => void copy('code', pairing.code)}>{copied === 'code' ? 'Copied' : 'Copy'}</button>
          </div>
          <div className="account-hint">{left > 0 ? `It works once, for about ${left} more minute${left === 1 ? '' : 's'}.` : 'This code has expired. Choose Add a runner for a new one.'}</div>

          {scripts ? (
            <>
              <div className="runner-os">
                <button type="button" className="account-btn runner-primary" onClick={() => download(scripts[os])}>Download setup for {OS_LABEL[os]}</button>
                <span className="account-hint">Other systems:</span>
                {(Object.keys(OS_LABEL) as OsKind[]).filter(kind => kind !== os).map(kind => (
                  <button key={kind} type="button" className="runner-link" onClick={() => setOs(kind)}>{OS_LABEL[kind]}</button>
                ))}
              </div>
              <p className="account-note">{runHint(os, scripts[os].filename)}</p>
              <details className="runner-details">
                <summary>View the script before you run it</summary>
                <pre>{scripts[os].text}</pre>
              </details>
              <p className="account-note">The script carries only this one-time code, never a key. It asks before it does anything, installs nothing system-wide, and makes a private environment in your home folder.</p>
            </>
          ) : (
            <p className="account-note">
              {pairing.install_problem || 'The install address could not be built, so there is no starter script to download.'}{' '}
              If you installed the runner yourself, pair this computer with this code: <code>saapp-runner pair --server {window.location.origin} {pairing.code}</code>
            </p>
          )}

          {pairing.commands && (
            <details className="runner-details">
              <summary>Prefer to type it? The same setup as commands</summary>
              <pre>{pairing.commands.join('\n')}</pre>
              <button type="button" className="account-btn" onClick={() => void copy('commands', (pairing.commands ?? []).join('\n'))}>{copied === 'commands' ? 'Copied' : 'Copy the commands'}</button>
            </details>
          )}

          <div className="runner-done">
            <button type="button" className="account-btn" onClick={() => setPairing(null)}>Done</button>
            <span className="account-hint">When the runner connects it shows above as Connected.</span>
          </div>
        </div>
      )}
    </section>
  );
}

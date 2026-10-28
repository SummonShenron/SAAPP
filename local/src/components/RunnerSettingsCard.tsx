import { useCallback, useEffect, useState } from 'react';
import { api, type RunnerInfo, type RunnerList, type RunnerPairing, type RunnerScript, type RunnerSettings } from '../api';
import { OS_LABEL, checkIsOn, detectOs, minutesLeft, noChecksLine, runHint, withCheck, withFakeKeys, type OsKind } from '../runnerLogic';
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
  const change = async (runner: RunnerInfo, next: RunnerSettings) => {
    const before = list;
    const show = (settings: RunnerSettings) => setList(current => current && { ...current, runners: current.runners.map(item => (item.id === runner.id ? { ...item, settings } : item)) });
    show(next);
    setSaving(runner.id);
    setError('');
    try {
      show((await api.updateRunnerSettings(runner.id, next)).settings);
    } catch (problem) {
      setList(before);
      setError(problem instanceof Error ? problem.message : 'Could not save that.');
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

      {list && list.runners.length === 0 && <p className="account-hint">You have no runners yet.</p>}
      {list && list.runners.length > 0 && (
        <ul className="runner-list">
          {list.runners.map(runner => (
            <li key={runner.id} className="runner-row">
              <div className="runner-main">
                <div className="account-label">{runner.name}</div>
                <div className="account-hint">
                  <span className={`runner-dot ${runner.connected ? 'runner-dot-on' : ''}`} aria-hidden="true" />
                  {runner.connected ? 'Connected' : 'Not connected'}
                  {runner.root_name ? ` · folder ${runner.root_name}` : ''}
                  {runner.version ? ` · version ${runner.version}` : ''}
                </div>
              </div>
              <button type="button" className="account-btn" onClick={() => void remove(runner.id, runner.name)}>Remove</button>
              <div className="runner-checks">
                {runner.checks.length === 0 && <div className="account-hint">{noChecksLine(runner.connected)}</div>}
                {runner.checks.map(check => (
                  <label key={check.name} className="runner-check">
                    <input type="checkbox" checked={checkIsOn(runner.settings, check.name)} disabled={saving === runner.id}
                      onChange={event => void change(runner, withCheck(runner.settings, check.name, event.target.checked))} />
                    <span className="runner-check-text">
                      <span className="account-label">{check.name}</span>
                      <code className="runner-check-command">{check.command}</code>
                    </span>
                  </label>
                ))}
                <label className="runner-check">
                  <input type="checkbox" checked={runner.settings.fake_keys} disabled={saving === runner.id}
                    onChange={event => void change(runner, withFakeKeys(runner.settings, event.target.checked))} />
                  <span className="runner-check-text">
                    <span className="account-label">Use a fake AI key while checks run</span>
                    <span className="account-hint">Keeps a test from spending real money: GOOGLE_API_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY and GEMINI_API_KEY are set to a made-up value for the run, even if your project's .env file holds a real one.</span>
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
              {pairing.install_problem || 'The install address is not set on this server yet (SAAPP_RUNNER_INSTALL_URL), so there is no starter script to download.'}{' '}
              If you installed the runner yourself, pair it with this code: <code>saapp-runner pair --server {window.location.origin} --root &lt;your project folder&gt; {pairing.code}</code>
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

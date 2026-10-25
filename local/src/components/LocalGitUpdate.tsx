import React, { useEffect, useRef, useState } from 'react';
import { api } from '../api';
import { folderGitFs } from '../localGit/gitFs';
import { applyImport, headTip, planImport, type ImportPlan } from '../localGit/importCommit';
import type { CommittedReply, LocalGitMode } from '../localGit/commitResult';
import { hasPermission, type FsDirectoryHandle } from '../localWorkspace';
import './__styles__/LocalGitUpdate.css';

interface Props {
  reply: CommittedReply;
  /** "button": check, show the plan, wait for "Do it". "auto": check and, when everything is safe, do it at once. */
  mode: Exclude<LocalGitMode, 'off'>;
  /** Only the newest reply is acted on automatically, and only once per commit in this tab. */
  isLatest: boolean;
  /** The connected folder, or null when none is connected and readable. */
  getHandle: () => FsDirectoryHandle | null;
}

type Phase =
  | { name: 'idle' }
  | { name: 'working'; text: string }
  | { name: 'confirm'; plan: ImportPlan }
  | { name: 'done'; text: string }
  | { name: 'refused'; reasons: string[] };

const HANDLED_KEY = 'sonic.localGitHandled';

function alreadyHandled(commit: string): boolean {
  try {
    return (sessionStorage.getItem(HANDLED_KEY) ?? '').split(',').includes(commit);
  } catch {
    return false;
  }
}

function markHandled(commit: string): void {
  try {
    const seen = (sessionStorage.getItem(HANDLED_KEY) ?? '').split(',').filter(Boolean);
    sessionStorage.setItem(HANDLED_KEY, [...seen.slice(-19), commit].join(','));
  } catch {
    /* worst case it is looked at again */
  }
}

function doneText(plan: ImportPlan): string {
  const at = plan.commit.slice(0, 7);
  if (plan.action === 'advance') return `Done. ${plan.branch} in your repository is now at ${at}.`;
  if (plan.action === 'create-switch') return `Done. You are now on ${plan.branch} at ${at}.`;
  return `Done. You are now on ${plan.branch} at ${at}, with the merge in it.`;
}

/**
 * Under Sonic's "Committed" and "Merged" replies: brings that commit into the person's own repository without git, by writing it into the connected folder's .git (see localGit/importCommit.ts).
 * It checks before it writes and refuses, in words, whenever anything looks unsafe; the commands in the reply still work. In auto mode the check and the update happen as soon as the reply arrives.
 */
export const LocalGitUpdate: React.FC<Props> = ({ reply, mode, isLatest, getHandle }) => {
  const [phase, setPhase] = useState<Phase>({ name: 'idle' });
  const started = useRef(false);

  const plan = async (fromClick: boolean): Promise<ImportPlan | null> => {
    const handle = getHandle();
    if (!handle) {
      setPhase({ name: 'refused', reasons: ['Connect your local folder first (the folder that holds your repository).'] });
      return null;
    }
    setPhase({ name: 'working', text: 'Checking your repository...' });
    try {
      // Writing into .git needs the folder opened for editing. A click may ask the browser; automatic mode only goes ahead when that was already allowed.
      if (!(await hasPermission(handle, 'readwrite', fromClick))) {
        setPhase({ name: 'refused', reasons: [fromClick ? 'The browser was not allowed to change this folder, so nothing was done.' : 'The browser has not been allowed to change this folder yet. Press the button to allow it.'] });
        return null;
      }
      const fs = folderGitFs(handle);
      const bundle = await api.getLocalGitBundle(reply.repo, reply.commit, await headTip(fs));
      const planned = await planImport(fs, bundle, reply.branch);
      if (planned.ok) return planned.plan;
      setPhase(planned.already ? { name: 'done', text: planned.reasons[0] } : { name: 'refused', reasons: planned.reasons });
      return null;
    } catch (error) {
      setPhase({ name: 'refused', reasons: [error instanceof Error ? error.message : 'Something went wrong reading the commit.'] });
      return null;
    }
  };

  const apply = async (toApply: ImportPlan) => {
    const handle = getHandle();
    if (!handle) return;
    setPhase({ name: 'working', text: 'Writing into .git...' });
    const result = await applyImport(folderGitFs(handle), toApply);
    markHandled(reply.commit);
    setPhase(result.ok ? { name: 'done', text: `${doneText(toApply)} Run "git status" to see for yourself; your files were not touched.` } : { name: 'refused', reasons: [result.reason] });
  };

  const check = async (fromClick: boolean) => {
    const found = await plan(fromClick);
    if (!found) return;
    if (mode === 'auto') await apply(found);
    else setPhase({ name: 'confirm', plan: found });
  };

  useEffect(() => {
    if (mode !== 'auto' || !isLatest || started.current || alreadyHandled(reply.commit)) return;
    started.current = true;
    void check(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, isLatest, reply.commit]);

  const label = reply.kind === 'merge' ? 'Update my local repo with this merge (experimental)' : 'Update my local repo (experimental)';
  return (
    <div className="local-git-update" role="group" aria-label="Update your local repository">
      {phase.name === 'idle' && (
        <button type="button" className="local-git-update-btn" onClick={() => void check(true)}>{label}</button>
      )}
      {phase.name === 'working' && <p className="local-git-update-text" role="status">{phase.text}</p>}
      {phase.name === 'confirm' && (
        <div className="local-git-update-confirm">
          <p className="local-git-update-text">This will, without running git:</p>
          <ul>{phase.plan.summary.map(line => <li key={line}>{line}</li>)}</ul>
          <div className="local-git-update-actions">
            <button type="button" className="local-git-update-btn" onClick={() => void apply(phase.plan)}>Do it</button>
            <button type="button" className="local-git-update-link" onClick={() => setPhase({ name: 'idle' })}>Cancel</button>
          </div>
        </div>
      )}
      {phase.name === 'done' && <p className="local-git-update-text" role="status">{phase.text}</p>}
      {phase.name === 'refused' && (
        <div className="local-git-update-refused" role="status">
          <p className="local-git-update-text">Nothing was changed. {phase.reasons.join(' ')}</p>
          <p className="local-git-update-text">The commands above still work.</p>
          <button type="button" className="local-git-update-link" onClick={() => setPhase({ name: 'idle' })}>Try again</button>
        </div>
      )}
    </div>
  );
};

export default LocalGitUpdate;

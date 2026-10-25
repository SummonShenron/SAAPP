import React, { useState } from 'react';
import { api } from '../api';
import { folderGitFs } from '../localGit/gitFs';
import { applyImport, planImport, type ImportPlan } from '../localGit/importCommit';
import { hasPermission, type FsDirectoryHandle } from '../localWorkspace';
import './__styles__/LocalGitUpdate.css';

interface Props {
  repo: string;
  commit: string;
  branch: string;
  /** The connected folder, or null when none is connected and readable. */
  getHandle: () => FsDirectoryHandle | null;
}

type Phase =
  | { name: 'idle' }
  | { name: 'working'; text: string }
  | { name: 'confirm'; plan: ImportPlan }
  | { name: 'done'; text: string }
  | { name: 'refused'; reasons: string[] };

/**
 * A button under Sonic's "Committed" reply (behind a switch, see localGit/commitResult.ts): brings that commit into the person's own repository without git, by writing it into the
 * connected folder's .git. Everything it will do is listed and confirmed first; it refuses, in words, whenever anything looks unsafe, and the commands in the reply still work.
 */
export const LocalGitUpdate: React.FC<Props> = ({ repo, commit, branch, getHandle }) => {
  const [phase, setPhase] = useState<Phase>({ name: 'idle' });

  const check = async () => {
    const handle = getHandle();
    if (!handle) {
      setPhase({ name: 'refused', reasons: ['Connect your local folder first (the folder that holds your repository).'] });
      return;
    }
    setPhase({ name: 'working', text: 'Checking your repository...' });
    try {
      // Writing into .git needs the folder opened for editing; the browser asks once.
      if (!(await hasPermission(handle, 'readwrite', true))) {
        setPhase({ name: 'refused', reasons: ['The browser was not allowed to change this folder, so nothing was done.'] });
        return;
      }
      const bundle = await api.getLocalGitBundle(repo, commit);
      const planned = await planImport(folderGitFs(handle), bundle, branch);
      setPhase(planned.ok ? { name: 'confirm', plan: planned.plan } : { name: 'refused', reasons: planned.reasons });
    } catch (error) {
      setPhase({ name: 'refused', reasons: [error instanceof Error ? error.message : 'Something went wrong reading the commit.'] });
    }
  };

  const apply = async (plan: ImportPlan) => {
    const handle = getHandle();
    if (!handle) return;
    setPhase({ name: 'working', text: 'Writing into .git...' });
    const result = await applyImport(folderGitFs(handle), plan);
    setPhase(result.ok
      ? { name: 'done', text: `Done. ${plan.action === 'switch' ? `You are now on ${plan.branch}` : `${plan.branch} is now`} at ${plan.commit.slice(0, 7)}. Run "git status" to see for yourself; your files were not touched.` }
      : { name: 'refused', reasons: [result.reason] });
  };

  return (
    <div className="local-git-update" role="group" aria-label="Update your local repository">
      {phase.name === 'idle' && (
        <button type="button" className="local-git-update-btn" onClick={() => void check()}>Update my local repo (experimental)</button>
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

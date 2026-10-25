// Finding the commit in Sonic's "Committed" and "Merged" replies, so the chat can bring it into the person's own repository. The wording is a contract with
// backend/services/commit_flow.py (execute, execute_merge), pinned by a backend test:
//   **Committed** [`abc1234`](https://github.com/owner/repo/commit/<40 hex>) to `branch` on `owner/repo` ...
//   **Merged** pull request #9 into `main` (squash) on `owner/repo`: [`abc1234`](https://github.com/owner/repo/commit/<40 hex>) ...

export interface CommittedReply {
  kind: 'commit' | 'merge';
  repo: string;
  commit: string;
  /** The branch the commit is on: the one Sonic committed to, or the one the pull request was merged into. */
  branch: string;
}

const COMMITTED = /\*\*Committed\*\* \[`[0-9a-f]{7}`\]\(https:\/\/github\.com\/([A-Za-z0-9._-]+\/[A-Za-z0-9._-]+)\/commit\/([0-9a-f]{40})\) to `([^`\s]+)` on `([^`\s]+)`/;
const MERGED = /\*\*Merged\*\* pull request #\d+ into `([^`\s]+)` \((?:squash|merge|rebase)\) on `([^`\s]+)`: \[`[0-9a-f]{7}`\]\(https:\/\/github\.com\/([A-Za-z0-9._-]+\/[A-Za-z0-9._-]+)\/commit\/([0-9a-f]{40})\)/;

export function parseCommittedReply(text: string): CommittedReply | null {
  const match = COMMITTED.exec(text || '');
  if (!match || match[1].toLowerCase() !== match[4].toLowerCase()) return null;
  return { kind: 'commit', repo: match[1], commit: match[2], branch: match[3] };
}

export function parseMergedReply(text: string): CommittedReply | null {
  const match = MERGED.exec(text || '');
  if (!match || match[2].toLowerCase() !== match[3].toLowerCase()) return null;
  return { kind: 'merge', repo: match[3], commit: match[4], branch: match[1] };
}

export function parseGitReply(text: string): CommittedReply | null {
  return parseCommittedReply(text) ?? parseMergedReply(text);
}

// How the page keeps the person's own repository in step with what Sonic commits and merges on GitHub:
//   off     nothing is offered.
//   button  a button under the reply; it checks, shows what it will do, and waits for "Do it" (the default).
//   auto    when a reply arrives, it checks and, if everything is safe, does it without asking (the commit was already approved on the card); anything unsafe shows the reasons and the commands still work.
export type LocalGitMode = 'off' | 'button' | 'auto';

const MODE_KEY = 'sonic.localGitMode';
const MODES: LocalGitMode[] = ['off', 'button', 'auto'];

export function loadLocalGitMode(): LocalGitMode {
  try {
    const saved = typeof localStorage === 'undefined' ? null : localStorage.getItem(MODE_KEY);
    return MODES.includes(saved as LocalGitMode) ? (saved as LocalGitMode) : 'button';
  } catch {
    return 'button';
  }
}

export function saveLocalGitMode(mode: LocalGitMode): void {
  try {
    localStorage.setItem(MODE_KEY, mode);
  } catch {
    /* kept for this visit only */
  }
}

export function nextLocalGitMode(mode: LocalGitMode): LocalGitMode {
  return MODES[(MODES.indexOf(mode) + 1) % MODES.length];
}

export function localGitModeLabel(mode: LocalGitMode): string {
  if (mode === 'auto') return 'Local git: update automatically after Sonic commits (experimental)';
  if (mode === 'off') return 'Local git: leave it to me (no button)';
  return 'Local git: offer a button after Sonic commits (experimental)';
}

// Finding the commit in Sonic's "Committed" reply, so the chat can offer to bring it into the person's own repository. The wording is a contract with
// backend/services/commit_flow.py (execute): `**Committed** [`abc1234`](https://github.com/owner/repo/commit/<40 hex>) to `branch` on `owner/repo``.

export interface CommittedReply {
  repo: string;
  commit: string;
  branch: string;
}

const PATTERN = /\*\*Committed\*\* \[`[0-9a-f]{7}`\]\(https:\/\/github\.com\/([A-Za-z0-9._-]+\/[A-Za-z0-9._-]+)\/commit\/([0-9a-f]{40})\) to `([^`\s]+)` on `([^`\s]+)`/;

export function parseCommittedReply(text: string): CommittedReply | null {
  const match = PATTERN.exec(text || '');
  if (!match || match[1].toLowerCase() !== match[4].toLowerCase()) return null;
  return { repo: match[1], commit: match[2], branch: match[3] };
}

/** The update is a spike: it only shows up for someone who turned it on (localStorage "sonic.localGitSpike" = "1"), and never throws. */
export function localGitSpikeEnabled(): boolean {
  try {
    return typeof localStorage !== 'undefined' && localStorage.getItem('sonic.localGitSpike') === '1';
  } catch {
    return false;
  }
}

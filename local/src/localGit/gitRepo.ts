// Reading the parts of a repository's ".git" folder that decide whether Sonic's import is safe: where HEAD points, what a branch is at, which remote is "origin", and whether git is in the middle of something.
import type { GitFs } from './gitFs.ts';

const decoder = new TextDecoder('utf-8');

export async function readText(fs: GitFs, path: string): Promise<string | null> {
  const bytes = await fs.read(path);
  return bytes ? decoder.decode(bytes) : null;
}

export type Head = { kind: 'branch'; ref: string; name: string } | { kind: 'detached'; sha: string };

export async function readHead(fs: GitFs): Promise<Head | null> {
  const text = (await readText(fs, '.git/HEAD'))?.trim();
  if (!text) return null;
  const branch = /^ref:\s*(refs\/heads\/\S+)$/.exec(text);
  if (branch) return { kind: 'branch', ref: branch[1], name: branch[1].slice('refs/heads/'.length) };
  return /^[0-9a-f]{40}$/.test(text) ? { kind: 'detached', sha: text } : null;
}

/** What a ref (for example "refs/heads/main") points at: the loose file first, then ".git/packed-refs". Null when there is no such ref. */
export async function resolveRef(fs: GitFs, ref: string): Promise<string | null> {
  const loose = (await readText(fs, `.git/${ref}`))?.trim();
  if (loose && /^[0-9a-f]{40}$/.test(loose)) return loose;
  const packed = await readText(fs, '.git/packed-refs');
  if (!packed) return null;
  for (const line of packed.split(/\r?\n/)) {
    if (!line || line.startsWith('#') || line.startsWith('^')) continue;
    const [sha, name] = line.split(' ');
    if (name === ref && /^[0-9a-f]{40}$/.test(sha)) return sha;
  }
  return null;
}

export async function originUrl(fs: GitFs): Promise<string | null> {
  const config = await readText(fs, '.git/config');
  if (!config) return null;
  let inOrigin = false;
  for (const raw of config.split(/\r?\n/)) {
    const line = raw.trim();
    if (line.startsWith('[')) inOrigin = /^\[remote\s+"origin"\]$/.test(line);
    else if (inOrigin) {
      const match = /^url\s*=\s*(.+)$/.exec(line);
      if (match) return match[1].trim();
    }
  }
  return null;
}

/** Whether a remote URL (https or ssh form) is this GitHub repository, "owner/name". */
export function sameRepo(url: string | null, repo: string): boolean {
  const match = url ? /github\.com[:/]+([^/\s]+\/[^/\s]+?)(?:\.git)?\/?$/i.exec(url.trim()) : null;
  return !!match && match[1].toLowerCase() === repo.toLowerCase();
}

/** The branch names git accepts that Sonic will also write into a path: letters, digits, dots, dashes, slashes; no "..", no trailing slash or dot. */
export function safeBranchName(name: string): boolean {
  return /^[A-Za-z0-9][A-Za-z0-9._/-]*$/.test(name) && name.length <= 100 && !name.includes('..') && !name.includes('//') && !/[/.]$/.test(name) && !name.endsWith('.lock');
}

/** Reasons it is not safe to change this repository right now, in words. An empty list means nothing is in the way. */
export async function blockers(fs: GitFs): Promise<string[]> {
  const reasons: string[] = [];
  if (!(await fs.exists('.git/HEAD'))) return ['This folder has no .git folder of its own (it may be a submodule or a linked worktree), so it was left alone.'];
  if (await fs.exists('.git/index.lock')) reasons.push('Git is busy in this folder (there is a .git/index.lock file). Finish what git is doing and try again.');
  for (const [file, what] of [['MERGE_HEAD', 'a merge'], ['CHERRY_PICK_HEAD', 'a cherry-pick'], ['REVERT_HEAD', 'a revert'], ['BISECT_LOG', 'a bisect'], ['rebase-merge', 'a rebase'], ['rebase-apply', 'a rebase']]) {
    if (await fs.exists(`.git/${file}`)) reasons.push(`Git is in the middle of ${what}, so nothing was changed.`);
  }
  const config = (await readText(fs, '.git/config')) ?? '';
  if (/objectformat\s*=\s*sha256/i.test(config)) reasons.push('This repository uses SHA-256 object ids, which this does not handle.');
  return reasons;
}

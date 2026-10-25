// Bringing a commit Sonic made on GitHub into the person's own repository without git: the commit, the trees and the blobs it changed are written as loose objects, the branch is moved (or
// created and switched to), and the index is updated for the changed files. Their working files are never touched, so anything that was not part of the commit stays a plain local change.
//
// Nothing is written until the whole commit has been checked: every blob, tree and the commit itself must hash to the id GitHub reports, the repository's origin must be this repository, git
// must not be busy, and the branch the person is on must be exactly where Sonic's commit starts. Anything else is returned as reasons in words and the caller falls back to the commands.
import { IndexRefused, newEntry, parseIndex, serializeIndex, withEntries } from './gitIndex.ts';
import type { GitFs } from './gitFs.ts';
import { blockers, originUrl, readHead, resolveRef, safeBranchName, sameRepo } from './gitRepo.ts';
import { deflateZlib, objectBytes, objectId, parseIsoDate, serializeCommit, serializeTree, type ObjectType, type TreeEntry } from './objects.ts';

export interface BundlePerson {
  name: string;
  email: string;
  date: string;      // ISO 8601, as GitHub gives it
}

export interface CommitBundle {
  repo: string;                                       // "owner/name"
  commit: { sha: string; tree: string; parents: string[]; author: BundlePerson; committer: BundlePerson; message: string; signature: string | null };
  trees: { sha: string; entries: TreeEntry[] }[];    // the root tree and every folder on the way to a changed file
  blobs: { sha: string; content: string }[];          // base64
  files: { path: string; sha: string; mode: string }[];
}

export interface ImportPlan {
  repo: string;
  branch: string;
  commit: string;
  parent: string;
  currentBranch: string;
  action: 'advance' | 'switch';
  objects: { id: string; type: ObjectType; body: Uint8Array }[];
  files: { path: string; mode: string; sha: string; size: number }[];
  summary: string[];
}

export type PlanResult = { ok: true; plan: ImportPlan } | { ok: false; reasons: string[] };

const short = (sha: string) => sha.slice(0, 7);

function base64ToBytes(text: string): Uint8Array {
  const binary = atob(text);
  const out = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) out[i] = binary.charCodeAt(i);
  return out;
}

const personOf = (p: BundlePerson) => ({ name: p.name, email: p.email, ...(() => { const d = parseIsoDate(p.date); return { timestamp: d.timestamp, offset: d.offset }; })() });

/** Hashes everything in the bundle and checks it is consistent: the commit rebuilds to its id, each tree to its id, each blob to its id, and each changed file is where its tree says. */
async function verifiedObjects(bundle: CommitBundle): Promise<{ objects: ImportPlan['objects']; sizes: Map<string, number> } | string> {
  const objects: ImportPlan['objects'] = [];
  const sizes = new Map<string, number>();
  const blobIds = new Set<string>();
  for (const blob of bundle.blobs) {
    const body = base64ToBytes(blob.content);
    if ((await objectId('blob', body)) !== blob.sha) return 'A file Sonic sent does not match its id, so nothing was written.';
    objects.push({ id: blob.sha, type: 'blob', body });
    sizes.set(blob.sha, body.length);
    blobIds.add(blob.sha);
  }
  const treeById = new Map<string, TreeEntry[]>();
  for (const tree of bundle.trees) {
    const body = serializeTree(tree.entries);
    if ((await objectId('tree', body)) !== tree.sha) return 'A folder listing Sonic sent does not match its id, so nothing was written.';
    objects.push({ id: tree.sha, type: 'tree', body });
    treeById.set(tree.sha, tree.entries);
  }
  const c = bundle.commit;
  let commitBody: Uint8Array;
  try {
    commitBody = serializeCommit({ tree: c.tree, parents: c.parents, author: personOf(c.author), committer: personOf(c.committer), message: c.message, signature: c.signature });
  } catch {
    return 'The commit has a date this could not read, so nothing was written.';
  }
  if ((await objectId('commit', commitBody)) !== c.sha) return 'The commit as rebuilt here does not match the id GitHub reports (so it may be signed or in a form this does not handle). Nothing was written.';
  objects.push({ id: c.sha, type: 'commit', body: commitBody });
  for (const file of bundle.files) {
    let treeId = c.tree;
    const names = file.path.split('/');
    for (const folder of names.slice(0, -1)) {
      const next = treeById.get(treeId)?.find(entry => entry.name === folder && entry.mode.replace(/^0+/, '') === '40000');
      if (!next) return `The folder ${folder} is not in what Sonic sent, so nothing was written.`;
      treeId = next.sha;
    }
    const leaf = treeById.get(treeId)?.find(entry => entry.name === names[names.length - 1]);
    if (!leaf || leaf.sha !== file.sha || !blobIds.has(file.sha) || !/^100(644|755)$/.test(leaf.mode)) return `The file ${file.path} is not what its folder listing says, so nothing was written.`;
  }
  return { objects, sizes };
}

export async function planImport(fs: GitFs, bundle: CommitBundle, branch: string): Promise<PlanResult> {
  const busy = await blockers(fs);
  if (busy.length) return { ok: false, reasons: busy };
  const reasons: string[] = [];
  if (!safeBranchName(branch)) return { ok: false, reasons: ['That branch name is not one this will write.'] };
  const url = await originUrl(fs);
  if (!sameRepo(url, bundle.repo)) reasons.push(`This folder's origin is ${url ?? 'not set'}, not ${bundle.repo}, so it was left alone.`);
  if (bundle.commit.parents.length !== 1) reasons.push('Only a commit with one parent is handled here.');
  const head = await readHead(fs);
  if (!head || head.kind !== 'branch') reasons.push('Your folder is not on a branch (HEAD is detached), so it was left alone.');
  if (reasons.length || !head || head.kind !== 'branch') return { ok: false, reasons };

  const parent = bundle.commit.parents[0];
  const tip = await resolveRef(fs, head.ref);
  if (tip !== parent) {
    reasons.push(`Your branch ${head.name} is at ${tip ? short(tip) : 'an unknown commit'}, but Sonic's commit starts from ${short(parent)}. Update your branch first (git pull), or use the commands instead.`);
    return { ok: false, reasons };
  }
  const existing = branch === head.name ? tip : await resolveRef(fs, `refs/heads/${branch}`);
  if (branch !== head.name && existing && existing !== bundle.commit.sha) {
    return { ok: false, reasons: [`A branch called ${branch} already exists in your repository at ${short(existing)}, so it was left alone.`] };
  }
  const indexBytes = await fs.read('.git/index');
  if (!indexBytes) return { ok: false, reasons: ['This repository has no index file yet, so it was left alone.'] };
  try {
    parseIndex(indexBytes);
  } catch (error) {
    return { ok: false, reasons: [error instanceof IndexRefused ? `Git's index: ${error.message}.` : 'Git\'s index could not be read.'] };
  }

  const verified = await verifiedObjects(bundle);
  if (typeof verified === 'string') return { ok: false, reasons: [verified] };
  const action = branch === head.name ? 'advance' : 'switch';
  const files = bundle.files.map(file => ({ path: file.path, mode: file.mode, sha: file.sha, size: verified.sizes.get(file.sha) ?? 0 }));
  return {
    ok: true,
    plan: {
      repo: bundle.repo, branch, commit: bundle.commit.sha, parent, currentBranch: head.name, action, objects: verified.objects, files,
      summary: [
        `Write ${verified.objects.length} git objects (${files.length} changed file${files.length === 1 ? '' : 's'}) into .git, each checked against GitHub's id.`,
        action === 'advance' ? `Move your branch ${branch} forward to ${short(bundle.commit.sha)}.` : `Create the branch ${branch} at ${short(bundle.commit.sha)} and switch to it (you are on ${head.name} now).`,
        `Update git's index for the changed files, and the remote-tracking branch origin/${branch}.`,
        'Your working files are not touched: anything that was not part of the commit stays a normal local change.',
      ],
    },
  };
}

function crlfToLf(bytes: Uint8Array): Uint8Array {
  const out: number[] = [];
  for (let i = 0; i < bytes.length; i++) if (!(bytes[i] === 13 && bytes[i + 1] === 10)) out.push(bytes[i]);
  return new Uint8Array(out);
}

const objectPath = (id: string) => `.git/objects/${id.slice(0, 2)}/${id.slice(2)}`;
const lineOf = (text: string) => new TextEncoder().encode(`${text}\n`);

export type ApplyResult = { ok: true; wrote: number } | { ok: false; reason: string };

export async function applyImport(fs: GitFs, plan: ImportPlan): Promise<ApplyResult> {
  if (await fs.exists('.git/index.lock')) return { ok: false, reason: 'Git became busy in this folder (a .git/index.lock appeared), so nothing was changed.' };
  await fs.write('.git/index.lock', new Uint8Array());           // the same lock git itself takes: a git command started now waits for us instead of colliding
  let wrote = 0;
  try {
    for (const object of plan.objects) {
      const path = objectPath(object.id);
      if (await fs.exists(path)) continue;
      await fs.write(path, await deflateZlib(objectBytes(object.type, object.body)));
      wrote += 1;
    }
    const indexBytes = await fs.read('.git/index');
    if (!indexBytes) return { ok: false, reason: 'The index disappeared, so nothing more was changed.' };
    const entries = [];
    for (const file of plan.files) {
      const onDisk = await fs.read(file.path);
      // The file counts as holding the committed content when it is byte for byte the blob, or the blob once its Windows line endings are turned back into plain ones (what git itself does when it
      // adds a file under autocrlf, and so what it records the file's own size and times for).
      const same = onDisk !== null && ((await objectId('blob', onDisk)) === file.sha || (await objectId('blob', crlfToLf(onDisk))) === file.sha);
      entries.push(newEntry(file.path, parseInt(file.mode, 8), file.sha, file.size, same ? await fs.stat(file.path) : null));
    }
    await fs.write('.git/index', await serializeIndex(withEntries(parseIndex(indexBytes), entries)));
    await fs.write(`.git/refs/remotes/origin/${plan.branch}`, lineOf(plan.commit));
    await fs.write(`.git/refs/heads/${plan.branch}`, lineOf(plan.commit));
    if (plan.action === 'switch') await fs.write('.git/HEAD', lineOf(`ref: refs/heads/${plan.branch}`));
    return { ok: true, wrote };
  } catch (error) {
    return { ok: false, reason: `Something went wrong while writing (${error instanceof Error ? error.message : 'unknown error'}). Your working files are untouched; run "git status" and "git fsck" to check the repository.` };
  } finally {
    await fs.remove('.git/index.lock');
  }
}

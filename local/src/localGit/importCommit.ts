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
  compare_tree?: string | null;                       // the folder-listing id of the commit the person's branch is at now, when GitHub has that commit (see backend/services/git_import.py)
}

export interface ImportPlan {
  repo: string;
  branch: string;
  commit: string;
  parent: string;
  currentBranch: string;
  action: 'advance' | 'create-switch' | 'move-switch';
  objects: { id: string; type: ObjectType; body: Uint8Array }[];
  files: { path: string; mode: string; sha: string; size: number }[];
  summary: string[];
}

export type PlanResult = { ok: true; plan: ImportPlan } | { ok: false; reasons: string[]; already?: boolean };

/** The commit the branch the person is on points at, or null (no repository, detached HEAD, nothing there). Used to ask GitHub whether that commit holds exactly what Sonic merged. */
export async function headTip(fs: GitFs): Promise<string | null> {
  const head = await readHead(fs);
  return head && head.kind === 'branch' ? resolveRef(fs, head.ref) : null;
}

const short = (sha: string) => sha.slice(0, 7);

function base64ToBytes(text: string): Uint8Array {
  const binary = atob(text);
  const out = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) out[i] = binary.charCodeAt(i);
  return out;
}

const personOf = (p: BundlePerson) => ({ name: p.name, email: p.email, ...(() => { const d = parseIsoDate(p.date); return { timestamp: d.timestamp, offset: d.offset }; })() });

/** Time-zone offsets ("-0500") to try, most likely first: the ones given, this browser's own, UTC, then every quarter hour from -12:00 to +14:00. */
export function candidateOffsets(given: string[] = []): string[] {
  const format = (minutes: number) => `${minutes < 0 ? '-' : '+'}${String(Math.floor(Math.abs(minutes) / 60)).padStart(2, '0')}${String(Math.abs(minutes) % 60).padStart(2, '0')}`;
  const all: string[] = [...given, format(-new Date().getTimezoneOffset()), '+0000'];
  for (let minutes = -720; minutes <= 840; minutes += 15) all.push(format(minutes));
  return [...new Set(all)];
}

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
  // GitHub's API gives a commit's dates in UTC ("...Z") and drops the time zone the commit was written with (a commit made for someone in Chicago is stored with -0500), but the zone is part of
  // the commit and therefore of its id. It is found by trying the zones people live in until the commit rebuilds to exactly the id GitHub reports: the id is the check, so nothing is guessed.
  let commitBody: Uint8Array | null = null;
  try {
    const author = personOf(c.author);
    const committer = personOf(c.committer);
    for (const offset of candidateOffsets([author.offset])) {
      const body = serializeCommit({ tree: c.tree, parents: c.parents, author: { ...author, offset }, committer: { ...committer, offset }, message: c.message, signature: c.signature });
      if ((await objectId('commit', body)) === c.sha) {
        commitBody = body;
        break;
      }
    }
  } catch {
    return 'The commit has a date this could not read, so nothing was written.';
  }
  if (!commitBody) return 'The commit as rebuilt here does not match the id GitHub reports (so it may be signed or in a form this does not handle). Nothing was written.';
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
  const currentTip = await resolveRef(fs, head.ref);
  const targetTip = branch === head.name ? currentTip : await resolveRef(fs, `refs/heads/${branch}`);
  if (targetTip === bundle.commit.sha) return { ok: false, already: true, reasons: [`Your repository already has this commit on ${branch}.`] };
  const startsFrom = (name: string, tip: string | null) => `Your branch ${name} is at ${tip ? short(tip) : 'an unknown commit'}, but Sonic's commit starts from ${short(parent)}. Update your branch first (git pull), or use the commands instead.`;
  let action: ImportPlan['action'];
  if (branch === head.name) {
    // A push to the branch they are on.
    if (currentTip !== parent) return { ok: false, reasons: [startsFrom(head.name, currentTip)] };
    action = 'advance';
  } else if (targetTip === null) {
    // A new branch, made from where they are.
    if (currentTip !== parent) return { ok: false, reasons: [startsFrom(head.name, currentTip)] };
    action = 'create-switch';
  } else {
    // The branch exists here: the commit is a pull request Sonic merged into it. It must be at exactly the commit the merge started from, and the branch they are on must hold exactly the merged
    // content, so that moving to it changes no file of theirs.
    if (targetTip !== parent) return { ok: false, reasons: [startsFrom(branch, targetTip)] };
    if (!bundle.compare_tree || bundle.compare_tree !== bundle.commit.tree) {
      return { ok: false, reasons: [`A branch called ${branch} already exists here, and your current branch (${head.name}) does not hold exactly what Sonic's commit contains, so switching to ${branch} could change your files. Use the commands instead.`] };
    }
    action = 'move-switch';
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
  const files = bundle.files.map(file => ({ path: file.path, mode: file.mode, sha: file.sha, size: verified.sizes.get(file.sha) ?? 0 }));
  return {
    ok: true,
    plan: {
      repo: bundle.repo, branch, commit: bundle.commit.sha, parent, currentBranch: head.name, action, objects: verified.objects, files,
      summary: [
        `Write ${verified.objects.length} git objects (${files.length} changed file${files.length === 1 ? '' : 's'}) into .git, each checked against GitHub's id.`,
        action === 'advance' ? `Move your branch ${branch} forward to ${short(bundle.commit.sha)}.` : action === 'create-switch' ? `Create the branch ${branch} at ${short(bundle.commit.sha)} and switch to it (you are on ${head.name} now).` : `Move ${branch} forward to ${short(bundle.commit.sha)} and switch to it (you are on ${head.name}, which holds exactly the same files).`,
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
    if (plan.action !== 'advance') await fs.write('.git/HEAD', lineOf(`ref: refs/heads/${plan.branch}`));
    return { ok: true, wrote };
  } catch (error) {
    return { ok: false, reason: `Something went wrong while writing (${error instanceof Error ? error.message : 'unknown error'}). Your working files are untouched; run "git status" and "git fsck" to check the repository.` };
  } finally {
    await fs.remove('.git/index.lock');
  }
}

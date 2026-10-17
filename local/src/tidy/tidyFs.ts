// Tidy up a folder: doing it, and undoing it, through the browser's own folder access. No server is involved and nothing is uploaded: the page reads
// only names, sizes and dates, and moves files inside the one folder the person chose. Written against small interfaces (below) rather than the
// browser's own types, so the same code can be tested against an in-memory folder (tidyFs.test.mjs).
//
// The rules this file keeps, whatever goes wrong:
//  - nothing is overwritten: a name that is already taken gets "(2)";
//  - nothing is deleted except an original whose copy has just been verified, and if even that cannot be done the copy is removed again;
//  - every move that happened is recorded, so all of it can be undone;
//  - one file failing never stops the others.
import { reverseMoves, uniqueName, type TidyFile, type TidyMove } from './tidyCore.ts';

export interface TidyFileLike {
  kind: 'file';
  name: string;
  getFile(): Promise<{ size: number; lastModified: number }>;
  createWritable(): Promise<{ write(data: unknown): Promise<void>; close(): Promise<void> }>;
  /** Present in newer browsers. When it works it is the best way to move a file: nothing is copied. */
  move?(destination: TidyDirLike, newName?: string): Promise<void>;
}

export interface TidyDirLike {
  kind: 'directory';
  name: string;
  values(): AsyncIterable<TidyFileLike | TidyDirLike>;
  getDirectoryHandle(name: string, options?: { create?: boolean }): Promise<TidyDirLike>;
  getFileHandle(name: string, options?: { create?: boolean }): Promise<TidyFileLike>;
  removeEntry(name: string, options?: { recursive?: boolean }): Promise<void>;
}

/** A file bigger than this is only moved if the browser can move it in place; copying that much just to tidy a folder is slow and risky. */
export const COPY_LIMIT_BYTES = 200 * 1024 * 1024;

export interface Scan {
  files: TidyFile[];
  folderNames: string[];
}

/** The loose files in the folder (not what is inside its sub-folders): name, size, date. Contents are never read. */
export async function scanFolder(root: TidyDirLike): Promise<Scan> {
  const files: TidyFile[] = [];
  const folderNames: string[] = [];
  for await (const entry of root.values()) {
    if (entry.kind === 'directory') { folderNames.push(entry.name); continue; }
    try {
      const info = await entry.getFile();
      files.push({ name: entry.name, size: info.size, modified: info.lastModified });
    } catch {
      // a file that cannot be looked at is simply left out of the plan
    }
  }
  return { files, folderNames };
}

async function ensureFolder(root: TidyDirLike, path: string, created: Set<string>): Promise<TidyDirLike> {
  let dir = root;
  let soFar = '';
  for (const part of path.split('/').filter(Boolean)) {
    soFar = soFar ? `${soFar}/${part}` : part;
    let exists = true;
    try {
      await dir.getDirectoryHandle(part);
    } catch {
      exists = false;
    }
    dir = await dir.getDirectoryHandle(part, { create: true });
    if (!exists) created.add(soFar);
  }
  return dir;
}

async function namesIn(dir: TidyDirLike): Promise<Set<string>> {
  const names = new Set<string>();
  for await (const entry of dir.values()) names.add(entry.name.toLowerCase());
  return names;
}

export interface MoveFailure {
  name: string;
  reason: string;
}

export interface ApplyResult {
  done: TidyMove[];
  failed: MoveFailure[];
  /** Folders this run created (and so may remove again if they end up empty). */
  createdFolders: string[];
}

type Progress = (finished: number, total: number) => void;

/** Moves one file from one folder into another, by browser move when it can, else by a verified copy. Throws with a plain reason when it cannot. */
async function moveOne(from: TidyDirLike, name: string, to: TidyDirLike, newName: string): Promise<void> {
  const source = await from.getFileHandle(name);
  if (source.move) {
    try {
      await source.move(to, newName);
      return;
    } catch {
      // This browser or folder does not allow a direct move; fall through to copying.
    }
  }
  const info = await source.getFile();
  if (info.size > COPY_LIMIT_BYTES) throw new Error('too large to move safely');
  const copy = await to.getFileHandle(newName, { create: true });
  const writable = await copy.createWritable();
  await writable.write(info);
  await writable.close();
  const written = await copy.getFile();
  if (written.size !== info.size) {
    await to.removeEntry(newName).catch(() => undefined);
    throw new Error('the copy did not match, so nothing was changed');
  }
  try {
    await from.removeEntry(name);
  } catch {
    await to.removeEntry(newName).catch(() => undefined);      // the original is in use: put things back as they were
    throw new Error('the file is open in another program');
  }
}

/** Moves each file into its folder, creating the folders. Never throws for one file: the failure is reported and the rest carry on. */
export async function applyMoves(root: TidyDirLike, moves: TidyMove[], onProgress?: Progress): Promise<ApplyResult> {
  const done: TidyMove[] = [];
  const failed: MoveFailure[] = [];
  const created = new Set<string>();
  const folders = new Map<string, { dir: TidyDirLike; taken: Set<string> }>();

  for (let i = 0; i < moves.length; i++) {
    const move = moves[i];
    try {
      let target = folders.get(move.folder);
      if (!target) {
        const dir = await ensureFolder(root, move.folder, created);
        target = { dir, taken: await namesIn(dir) };
        folders.set(move.folder, target);
      }
      const to = uniqueName(move.name, target.taken);
      await moveOne(root, move.name, target.dir, to);
      target.taken.add(to.toLowerCase());
      done.push({ ...move, to });
    } catch (error) {
      failed.push({ name: move.name, reason: error instanceof Error && error.message ? error.message : 'could not be moved' });
    }
    onProgress?.(i + 1, moves.length);
  }
  return { done, failed, createdFolders: [...created] };
}

/** Puts back every move in `done`, last first, then removes the folders that run created if they are now empty. */
export async function undoMoves(root: TidyDirLike, done: TidyMove[], createdFolders: string[], onProgress?: Progress): Promise<ApplyResult> {
  const back = reverseMoves(done);
  const undone: TidyMove[] = [];
  const failed: MoveFailure[] = [];
  const rootNames = await namesIn(root);

  for (let i = 0; i < back.length; i++) {
    const move = back[i];                                  // name = where it is now (inside the folder); to = what it was called
    try {
      const dir = await ensureFolderIfExists(root, move.folder);
      const restored = uniqueName(move.to as string, rootNames);
      await moveOne(dir, move.name, root, restored);
      rootNames.add(restored.toLowerCase());
      undone.push({ name: move.name, folder: move.folder, to: restored });
    } catch (error) {
      failed.push({ name: move.to as string, reason: error instanceof Error && error.message ? error.message : 'could not be put back' });
    }
    onProgress?.(i + 1, back.length);
  }

  // Deepest first, and only if empty: removeEntry without "recursive" refuses a folder that still has something in it.
  for (const path of [...createdFolders].sort((a, b) => b.split('/').length - a.split('/').length)) {
    try {
      const parts = path.split('/');
      const leaf = parts.pop() as string;
      let parent = root;
      for (const part of parts) parent = await parent.getDirectoryHandle(part);
      await parent.removeEntry(leaf);
    } catch {
      // not empty, or already gone: leave it
    }
  }
  return { done: undone, failed, createdFolders: [] };
}

async function ensureFolderIfExists(root: TidyDirLike, path: string): Promise<TidyDirLike> {
  let dir = root;
  for (const part of path.split('/').filter(Boolean)) dir = await dir.getDirectoryHandle(part);
  return dir;
}

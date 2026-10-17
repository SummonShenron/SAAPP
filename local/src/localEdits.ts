// Applying (and undoing) an edit proposal on the user's real folder. This is the only place Sonic's
// proposals ever touch disk, and only because the user pressed Apply: the proposal is dry-run
// against the files' current contents first (localEditsCore.planEdits), all-or-nothing, and the
// original contents of every touched file are saved before anything is written so one click can
// put it all back.
import { keyValueStore } from './idb';
import { editLocalDocx, renderLocalDocx } from './api';
import { bytesToBase64, hasPermission, type FsDirectoryHandle } from './localWorkspace';
import { changesMovedSince, forUndo, planEdits, type EditProposal, type FileChange } from './localEditsCore';

export interface UndoBatch {
  id: string; // the proposal id
  folder: string;
  summary: string;
  appliedAt: number;
  files: FileChange[];
}

const undoStore = keyValueStore('saapp-local-edits', 'undo');
const MAX_UNDO_BATCHES = 20;

export async function saveUndoBatch(batch: UndoBatch): Promise<void> {
  await undoStore.put(batch.id, batch);
  const all = (await undoStore.all<UndoBatch>()).sort((a, b) => b.appliedAt - a.appliedAt);
  for (const stale of all.slice(MAX_UNDO_BATCHES)) await undoStore.remove(stale.id);
}

export function loadUndoBatch(id: string): Promise<UndoBatch | null> {
  return undoStore.get<UndoBatch>(id);
}

export async function latestUndoBatch(): Promise<UndoBatch | null> {
  const all = await undoStore.all<UndoBatch>();
  return all.sort((a, b) => b.appliedAt - a.appliedAt)[0] ?? null;
}

async function resolveDirectory(root: FsDirectoryHandle, segments: string[], create: boolean): Promise<FsDirectoryHandle> {
  let dir = root;
  for (const segment of segments) dir = await dir.getDirectoryHandle(segment, { create });
  return dir;
}

/** The file's bytes, or null if it (or a directory on the way to it) doesn't exist. */
export async function readFileBytes(root: FsDirectoryHandle, path: string): Promise<Uint8Array | null> {
  const segments = path.split('/');
  const name = segments.pop() as string;
  try {
    const dir = await resolveDirectory(root, segments, false);
    return new Uint8Array(await (await (await dir.getFileHandle(name)).getFile()).arrayBuffer());
  } catch (error) {
    if (error instanceof DOMException && (error.name === 'NotFoundError' || error.name === 'TypeMismatchError')) return null;
    throw error;
  }
}

/** The file's text, or null if it (or a directory on the way to it) doesn't exist. */
export async function readFileText(root: FsDirectoryHandle, path: string): Promise<string | null> {
  const segments = path.split('/');
  const name = segments.pop() as string;
  try {
    const dir = await resolveDirectory(root, segments, false);
    return await (await (await dir.getFileHandle(name)).getFile()).text();
  } catch (error) {
    if (error instanceof DOMException && (error.name === 'NotFoundError' || error.name === 'TypeMismatchError')) {
      return null;
    }
    throw error;
  }
}

async function writeFileText(root: FsDirectoryHandle, path: string, text: string): Promise<void> {
  const segments = path.split('/');
  const name = segments.pop() as string;
  const dir = await resolveDirectory(root, segments, true);
  const writable = await (await dir.getFileHandle(name, { create: true })).createWritable();
  await writable.write(text);
  await writable.close();
}

async function writeFileBytes(root: FsDirectoryHandle, path: string, bytes: ArrayBuffer): Promise<void> {
  const segments = path.split('/');
  const name = segments.pop() as string;
  const dir = await resolveDirectory(root, segments, true);
  const writable = await (await dir.getFileHandle(name, { create: true })).createWritable();
  await writable.write(bytes);
  await writable.close();
}

/** "size:lastModified" of the file, or null when it does not exist. */
export async function fileStamp(root: FsDirectoryHandle, path: string): Promise<string | null> {
  const segments = path.split('/');
  const name = segments.pop() as string;
  try {
    const dir = await resolveDirectory(root, segments, false);
    const file = await (await dir.getFileHandle(name)).getFile();
    return `${file.size}:${file.lastModified}`;
  } catch (error) {
    if (error instanceof DOMException && (error.name === 'NotFoundError' || error.name === 'TypeMismatchError')) return null;
    throw error;
  }
}

async function removeFile(root: FsDirectoryHandle, path: string): Promise<void> {
  const segments = path.split('/');
  const name = segments.pop() as string;
  const dir = await resolveDirectory(root, segments, false);
  await dir.removeEntry(name);
}

/** Puts a file back to `before` (deleting it if it didn't exist before). */
async function restore(root: FsDirectoryHandle, change: FileChange): Promise<void> {
  if (change.before === null) await removeFile(root, change.path);
  else await writeFileText(root, change.path, change.before);
}

export type ApplyResult =
  | { ok: true; filesChanged: number }
  | { ok: false; errors: string[] };

export async function applyProposal(root: FsDirectoryHandle, proposal: EditProposal): Promise<ApplyResult> {
  // First thing, while the click's user activation is still fresh: asks for write access, which
  // the folder doesn't have until the user's first Apply (it was granted read-only).
  if (!(await hasPermission(root, 'readwrite', true))) {
    return { ok: false, errors: ['Write access to the folder was not granted, so nothing was changed.'] };
  }

  let plan;
  try {
    plan = await planEdits(proposal.edits, path => readFileText(root, path));
  } catch (error) {
    return { ok: false, errors: [`Could not read the folder: ${error instanceof Error ? error.message : String(error)}`] };
  }
  if (!plan.ok) {
    return {
      ok: false,
      errors: [...plan.errors, 'Nothing was changed. If you edited these files since Sonic read them, ask it to try again.'],
    };
  }

  // A new Word document is built by the server from Sonic's description, here and now, before anything is written, so a failure
  // to build one leaves the folder exactly as it was (the same all-or-nothing rule as the text edits).
  const documentBytes = new Map<string, ArrayBuffer>();
  for (const change of plan.changes) {
    try {
      if (change.document) {
        documentBytes.set(change.path, await renderLocalDocx(change.document));
      } else if (change.docxEdit) {
        // An edited copy: the original's CURRENT bytes (read here, never written) go to the server with the edits; what comes back has been checked to differ
        // from the original only where the edits say, and is saved under the new name.
        const original = await readFileBytes(root, change.docxEdit.source);
        if (!original) return { ok: false, errors: [`${change.docxEdit.source} is no longer in the folder, so nothing was changed.`] };
        documentBytes.set(change.path, await editLocalDocx(bytesToBase64(original), change.docxEdit.word_edits));
      }
    } catch (error) {
      return { ok: false, errors: [`Couldn't make ${change.path}: ${error instanceof Error ? error.message : String(error)} Nothing was changed.`] };
    }
  }

  const written: FileChange[] = [];
  try {
    for (const change of plan.changes) {
      const bytes = documentBytes.get(change.path);
      if (bytes) {
        await writeFileBytes(root, change.path, bytes);
        const entry: FileChange = { ...change };
        written.push(entry);                       // recorded before anything else can fail, so a rollback removes this file too
        entry.stamp = (await fileStamp(root, change.path)) ?? undefined;
      } else {
        await writeFileText(root, change.path, change.after);
        written.push(change);
      }
    }
  } catch (error) {
    // All-or-nothing: put back whatever was already written before reporting the failure.
    for (const change of written.reverse()) {
      try {
        await restore(root, change);
      } catch {
        // best effort — the failure below still tells the user exactly what happened
      }
    }
    return { ok: false, errors: [`Writing failed (${error instanceof Error ? error.message : String(error)}); the files were put back as they were.`] };
  }

  await saveUndoBatch({
    id: proposal.proposal_id,
    folder: proposal.folder,
    summary: proposal.summary,
    appliedAt: Date.now(),
    files: written.map(forUndo),
  });
  return { ok: true, filesChanged: plan.changes.length };
}

export type UndoResult =
  | { ok: true; filesRestored: number }
  | { ok: false; errors: string[] };

/** Restores every file in the batch to its pre-apply contents — but only if none of them has
 *  changed since the apply. If any has (the user kept editing), nothing is touched, because
 *  overwriting newer work with the old copy would be worse than not undoing. */
export async function undoBatch(root: FsDirectoryHandle, batchId: string): Promise<UndoResult> {
  const batch = await loadUndoBatch(batchId);
  if (!batch) return { ok: false, errors: ['There is no saved copy to undo this change from.'] };
  if (!(await hasPermission(root, 'readwrite', true))) {
    return { ok: false, errors: ['Write access to the folder was not granted, so nothing was changed.'] };
  }

  try {
    const moved = await changesMovedSince(batch.files, path => readFileText(root, path), path => fileStamp(root, path));
    if (moved.length > 0) {
      return {
        ok: false,
        errors: [`These files changed after the edit was applied, so nothing was undone: ${moved.join(', ')}`],
      };
    }
    for (const change of batch.files) await restore(root, change);
  } catch (error) {
    return { ok: false, errors: [`Undo failed: ${error instanceof Error ? error.message : String(error)}`] };
  }
  await undoStore.remove(batch.id);
  return { ok: true, filesRestored: batch.files.length };
}

// Browser side of "connect a local folder": the user picks a folder (File System Access API,
// Chromium desktop only), this module walks it, and uploads its text files to the server as a
// snapshot that Sonic reads instead of GitHub's copy. Read-only by design — the picker is opened in
// 'read' mode, so nothing here can write to the user's disk.
//
// The browser is the bridge: no helper to install, and the browser itself confines this tab to the
// one folder the user chose.
import { keyValueStore } from './idb';
import {
  getLocalWorkspaceStatus,
  syncLocalWorkspaceBatch,
  type LocalWorkspaceSyncResult,
} from './api';

interface FsEntryBase {
  name: string;
}
export interface FsWritable {
  write(data: string): Promise<void>;
  close(): Promise<void>;
}
export interface FsFileHandle extends FsEntryBase {
  kind: 'file';
  getFile(): Promise<File>;
  createWritable(): Promise<FsWritable>;
}
export interface FsDirectoryHandle extends FsEntryBase {
  kind: 'directory';
  values(): AsyncIterable<FsFileHandle | FsDirectoryHandle>;
  getDirectoryHandle(name: string, options?: { create?: boolean }): Promise<FsDirectoryHandle>;
  getFileHandle(name: string, options?: { create?: boolean }): Promise<FsFileHandle>;
  removeEntry(name: string): Promise<void>;
  queryPermission?(descriptor: { mode: 'read' | 'readwrite' }): Promise<PermissionState>;
  requestPermission?(descriptor: { mode: 'read' | 'readwrite' }): Promise<PermissionState>;
}
type DirectoryPickerWindow = Window & {
  showDirectoryPicker?: (options?: { mode?: 'read' | 'readwrite'; id?: string }) => Promise<FsDirectoryHandle>;
};

// These limits mirror backend/services/local_workspace.py, which enforces them again server-side —
// the client filters first so it never even reads, let alone uploads, a file the server would refuse.
const MAX_FILE_BYTES = 500_000;
const MAX_TOTAL_BYTES = 60_000_000;
const MAX_FILES = 8000;
const BATCH_MAX_BYTES = 2_500_000;
const BATCH_MAX_FILES = 300;

const SKIP_DIRS = new Set([
  'node_modules', '.git', 'dist', 'build', '__pycache__', '.venv', 'venv', '.next', 'coverage',
  'chroma_db', 'index-db',
]);
const TEXT_EXTENSIONS = new Set([
  'py', 'ts', 'tsx', 'js', 'jsx', 'mjs', 'cjs', 'json', 'md', 'mdx', 'css', 'scss', 'html', 'htm',
  'toml', 'yaml', 'yml', 'txt', 'sh', 'ps1', 'bat', 'sql', 'ini', 'cfg', 'conf', 'xml', 'graphql',
]);
const TEXT_FILENAMES = new Set(['dockerfile', 'makefile', 'procfile', 'license', 'readme']);
const SKIPPED_FILENAMES = new Set(['package-lock.json', 'yarn.lock', 'pnpm-lock.yaml', 'poetry.lock']);
const SECRET_FILENAMES = new Set([
  'id_rsa', 'id_dsa', 'id_ecdsa', 'id_ed25519', '.npmrc', '.pypirc', '.netrc', 'credentials.json',
  'secrets.json', 'secrets.yaml', 'secrets.yml', 'secrets.toml',
]);
const SECRET_SUFFIXES = ['.pem', '.key', '.p12', '.pfx', '.keystore', '.env'];
const ENV_TEMPLATE_SUFFIXES = ['.example', '.sample', '.template'];

// Whether this browser has already shown the user the "what gets uploaded" notice and been told
// to continue. localStorage can be blocked or cleared, in which case the notice simply shows again.
const CONSENT_KEY = 'saapp_local_folder_consent_v1';

export function hasLocalFolderConsent(): boolean {
  try {
    return localStorage.getItem(CONSENT_KEY) === 'yes';
  } catch {
    return false;
  }
}

export function recordLocalFolderConsent(): void {
  try {
    localStorage.setItem(CONSENT_KEY, 'yes');
  } catch {
    // not remembered; the notice will show next time
  }
}

export function isLocalWorkspaceSupported(): boolean {
  return typeof window !== 'undefined' && typeof (window as DirectoryPickerWindow).showDirectoryPicker === 'function';
}

function isSecretName(lowerName: string): boolean {
  if (ENV_TEMPLATE_SUFFIXES.some(s => lowerName.endsWith(s))) return false;
  return lowerName === '.env'
    || lowerName.startsWith('.env.')
    || SECRET_FILENAMES.has(lowerName)
    || SECRET_SUFFIXES.some(s => lowerName.endsWith(s));
}

/** Whether a file is worth uploading: source/text by extension or well-known name, not a lockfile
 *  or minified bundle, and never anything that looks like a secret. */
export function isSyncableFileName(name: string): boolean {
  const lower = name.toLowerCase();
  if (isSecretName(lower) || SKIPPED_FILENAMES.has(lower) || lower.endsWith('.min.js') || lower.endsWith('.map')) {
    return false;
  }
  if (ENV_TEMPLATE_SUFFIXES.some(s => lower.endsWith(s))) return true;
  if (TEXT_FILENAMES.has(lower)) return true;
  const dot = lower.lastIndexOf('.');
  return dot > 0 && TEXT_EXTENSIONS.has(lower.slice(dot + 1));
}

export async function pickFolder(): Promise<FsDirectoryHandle> {
  const picker = (window as DirectoryPickerWindow).showDirectoryPicker;
  if (!picker) throw new Error('This browser cannot open local folders.');
  return picker.call(window, { mode: 'read', id: 'saapp-workspace' });
}

export async function hasPermission(
  handle: FsDirectoryHandle,
  mode: 'read' | 'readwrite',
  request: boolean,
): Promise<boolean> {
  try {
    if (!handle.queryPermission) return true;
    if ((await handle.queryPermission({ mode })) === 'granted') return true;
    if (request && handle.requestPermission) {
      return (await handle.requestPermission({ mode })) === 'granted';
    }
  } catch {
    // fall through: treat any permission error as "not granted"
  }
  return false;
}

export function hasReadPermission(handle: FsDirectoryHandle, request: boolean): Promise<boolean> {
  return hasPermission(handle, 'read', request);
}

// --- remembering the chosen folder across reloads (IndexedDB can store the handle itself) ---

const handleStore = keyValueStore('saapp-local-workspace', 'handles');
const HANDLE_KEY = 'folder';

export async function saveFolderHandle(handle: FsDirectoryHandle): Promise<void> {
  await handleStore.put(HANDLE_KEY, handle);
}

export function loadFolderHandle(): Promise<FsDirectoryHandle | null> {
  return handleStore.get<FsDirectoryHandle>(HANDLE_KEY);
}

export async function clearFolderHandle(): Promise<void> {
  await handleStore.remove(HANDLE_KEY);
}

// --- scanning and uploading ---

export interface FolderScan {
  /** path -> "size:lastModified" for every file that qualifies for upload right now. */
  stamps: Map<string, string>;
  /** Files whose stamp differs from `prev` (new or edited). */
  changed: { path: string; file: File }[];
  /** Paths present in `prev` that are gone now. */
  deleted: string[];
  skipped: number;
  truncated: boolean;
}

export async function scanFolder(root: FsDirectoryHandle, prev: Map<string, string>): Promise<FolderScan> {
  const stamps = new Map<string, string>();
  const changed: { path: string; file: File }[] = [];
  let skipped = 0;
  let truncated = false;
  let totalBytes = 0;
  const stack: { dir: FsDirectoryHandle; prefix: string }[] = [{ dir: root, prefix: '' }];

  while (stack.length > 0) {
    const { dir, prefix } = stack.pop()!;
    for await (const entry of dir.values()) {
      if (entry.kind === 'directory') {
        if (!SKIP_DIRS.has(entry.name)) stack.push({ dir: entry, prefix: `${prefix}${entry.name}/` });
        continue;
      }
      if (!isSyncableFileName(entry.name)) continue;
      if (stamps.size >= MAX_FILES) {
        truncated = true;
        continue;
      }
      let file: File;
      try {
        file = await entry.getFile();
      } catch {
        skipped += 1;
        continue;
      }
      if (file.size > MAX_FILE_BYTES) {
        skipped += 1;
        continue;
      }
      if (totalBytes + file.size > MAX_TOTAL_BYTES) {
        truncated = true;
        continue;
      }
      totalBytes += file.size;
      const path = `${prefix}${entry.name}`;
      const stamp = `${file.size}:${file.lastModified}`;
      stamps.set(path, stamp);
      if (prev.get(path) !== stamp) changed.push({ path, file });
    }
  }

  const deleted = [...prev.keys()].filter(path => !stamps.has(path));
  return { stamps, changed, deleted, skipped, truncated };
}

export interface SyncOutcome {
  stamps: Map<string, string>;
  /** The server's file count after the last batch, or null when nothing needed uploading. */
  serverFileCount: number | null;
  uploaded: number;
  rejected: { path: string; reason: string }[];
  skipped: number;
  truncated: boolean;
}

/** Uploads what changed since `prev` (or everything, with reset=true, which also wipes the
 *  server's previous snapshot first) in size-bounded batches. */
export async function syncFolder(
  root: FsDirectoryHandle,
  prev: Map<string, string>,
  reset: boolean,
): Promise<SyncOutcome> {
  const scan = await scanFolder(root, reset ? new Map() : prev);
  const outcome: SyncOutcome = {
    stamps: scan.stamps,
    serverFileCount: null,
    uploaded: 0,
    rejected: [],
    skipped: scan.skipped,
    truncated: scan.truncated,
  };
  if (!reset && scan.changed.length === 0 && scan.deleted.length === 0) return outcome;

  let batch: { path: string; content: string }[] = [];
  let batchBytes = 0;
  let firstRequest = true;
  let deletedSent = false;

  const flush = async () => {
    if (batch.length === 0 && !(firstRequest && reset) && (deletedSent || scan.deleted.length === 0)) return;
    const result: LocalWorkspaceSyncResult = await syncLocalWorkspaceBatch({
      name: root.name,
      reset: reset && firstRequest,
      files: batch,
      deleted: deletedSent ? [] : scan.deleted,
    });
    deletedSent = true;
    firstRequest = false;
    outcome.serverFileCount = result.file_count;
    outcome.uploaded += result.accepted;
    outcome.rejected.push(...result.rejected);
    batch = [];
    batchBytes = 0;
  };

  for (const { path, file } of scan.changed) {
    const content = await file.text();
    batch.push({ path, content });
    batchBytes += file.size;
    if (batchBytes >= BATCH_MAX_BYTES || batch.length >= BATCH_MAX_FILES) await flush();
  }
  await flush();
  return outcome;
}

/** Whether the server's snapshot still matches what the last sync left there — it won't after a
 *  server restart wipes its temp directory, in which case the caller must do a full reset. */
export async function serverSnapshotMatches(expectedFileCount: number | null): Promise<boolean> {
  if (expectedFileCount === null) return false;
  const status = await getLocalWorkspaceStatus();
  return status.connected && status.file_count === expectedFileCount;
}

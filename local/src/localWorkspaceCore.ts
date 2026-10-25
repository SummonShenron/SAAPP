// The parts of the local-folder sync that need no browser, so they can be tested on their own (see localWorkspaceCore.test.mjs).

/** An error from the folder sync that knows the HTTP status it came back with (undefined when the request never got an answer). */
export class SyncRequestError extends Error {
  status?: number;
  constructor(message: string, status?: number) {
    super(message);
    this.name = 'SyncRequestError';
    this.status = status;
  }
}

/** Worth trying again: the network dropped (fetch rejects with a TypeError), or the server was busy, restarting or timed out. A refusal (sign in, too big, replaced by a newer upload) is not. */
export function isTransient(error: unknown): boolean {
  if (error instanceof SyncRequestError) {
    return error.status === undefined || error.status === 408 || error.status === 425 || error.status === 429 || error.status >= 500;
  }
  return error instanceof TypeError;
}

export interface RetryOptions {
  /** Tries in all, the first included. */
  attempts?: number;
  /** Waits before the second, third... try; the last one repeats. */
  delaysMs?: number[];
  isRetryable?: (error: unknown) => boolean;
  sleep?: (ms: number) => Promise<void>;
}

/** Runs `run`, and on a transient failure tries again after a short wait (a phone losing signal for a second, Render restarting) instead of failing the whole upload. */
export async function withRetry<T>(run: () => Promise<T>, options: RetryOptions = {}): Promise<T> {
  const attempts = options.attempts ?? 3;
  const delays = options.delaysMs ?? [500, 1500];
  const retryable = options.isRetryable ?? isTransient;
  const sleep = options.sleep ?? ((ms: number) => new Promise<void>(resolve => setTimeout(resolve, ms)));
  for (let attempt = 1; ; attempt += 1) {
    try {
      return await run();
    } catch (error) {
      if (attempt >= attempts || !retryable(error)) throw error;
      await sleep(delays.length ? delays[Math.min(attempt - 1, delays.length - 1)] : 0);
    }
  }
}

/** What the browser remembers about the last upload, so a reload does not forget it (stamps are "size:lastModified" per file). */
export interface SavedSyncState {
  folder: string;
  serverCount: number;
  stamps: [string, string][];
}

/** The remembered stamps, when they can be trusted: the same folder, and the server still holds exactly the files that upload left there. Anything else (a different folder, a server
 *  that restarted, a copy that expired or was replaced by another tab) means the memory is no use, and the next upload starts from scratch. */
export function restorableStamps(
  saved: SavedSyncState | null | undefined,
  folderName: string,
  server: { connected: boolean; file_count?: number } | null | undefined,
): Map<string, string> | null {
  if (!saved || !server || !server.connected) return null;
  if (saved.folder !== folderName || saved.serverCount !== server.file_count) return null;
  if (!Array.isArray(saved.stamps)) return null;
  return new Map(saved.stamps);
}

/** The branch name out of the text of `.git/HEAD` ("ref: refs/heads/feat/x" -> "feat/x"), or null for a detached HEAD, a worktree pointer or anything unreadable. */
export function parseGitHead(text: string): string | null {
  const match = /^ref:\s*refs\/heads\/(\S+)\s*$/m.exec(text || '');
  return match ? match[1] : null;
}

// The page's side of the chat stream's keepalive (backend/utils/stream_keepalive.py). The server says `: ready` when a stream starts and `: keepalive` after every 15 seconds of silence. A stream that has
// said so and then goes quiet for IDLE_LIMIT_MS is dead (the server restarted under it, or a proxy kept the socket open after the server behind it was gone), so the page gives up on it: the turn ends
// as "interrupted" with Retry, and Send and Continue work again, instead of staying busy until the person reloads (2026-10-30).
//
// Only a stream that made the promise is watched. A page ahead of its server, or a server that is not new enough, never sends the comment, and a long step with no output there is not a dead stream.

/** Four missed keepalives. */
export const IDLE_LIMIT_MS = 60_000;

/** Thrown in place of a read that has been quiet for too long. */
export class StreamIdleError extends Error {
  constructor() {
    super('The stream went quiet.');
    this.name = 'StreamIdleError';
  }
}

/** A server comment (": ready", ": keepalive"): not data, but proof the server is there and keeps the promise. */
export function isKeepaliveLine(line: string): boolean {
  return line.trim().startsWith(':');
}

/**
 * The read, unless it takes longer than `limitMs`: then `onIdle` runs (the caller cancels the reader) and the result is a StreamIdleError. With a null limit (a stream that never promised a keepalive) the
 * read is returned as it is, with no timer.
 */
export function readOrGiveUp<T>(read: Promise<T>, limitMs: number | null, onIdle: () => void): Promise<T> {
  if (limitMs === null) return read;
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(() => {
      try { onIdle(); } catch { /* giving up must not depend on the cleanup */ }
      reject(new StreamIdleError());
    }, limitMs);
    read.then(
      (value) => { clearTimeout(timer); resolve(value); },
      (error) => { clearTimeout(timer); reject(error); },
    );
  });
}

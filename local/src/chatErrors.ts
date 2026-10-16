// Why a chat message failed to send or finish, and what to tell the person. Pure (no React, no fetch), so it is tested by itself.
//
// This replaces one hardcoded sentence that blamed "vector assertions" and "local engine allocations" for every failure at all. The rule now: a
// message is only written for a cause we have evidence of (the HTTP status, or that the request never connected, or that the stream dropped), and
// the unknown case says it is unknown. Nothing here ever names a component it has no evidence about.
//
// Run the tests with: node --test src/chatErrors.test.mjs   (from local/)

export type ChatFailureKind =
  | 'session'        // 401: the sign-in is no longer valid
  | 'forbidden'      // 403: signed in, but this request was refused
  | 'bad_request'    // 400, 413, 422: the server could not accept the message as sent
  | 'rate_limited'   // 429
  | 'starting'       // 502, 503, 504: the server (or the proxy in front of it) did not answer, as when it is starting up or redeploying
  | 'server'         // 500 and the other 5xx: the server failed
  | 'network'        // the request never reached the server
  | 'interrupted'    // the connection dropped part-way through an answer
  | 'unknown';

export interface ChatSendErrorInit {
  status?: number;
  /** A short message from the server's own response (a plain string), when it sent one. Never a validation echo of what was sent. */
  detail?: string;
}

export class ChatSendError extends Error {
  readonly kind: ChatFailureKind;
  readonly status?: number;
  readonly detail?: string;

  constructor(kind: ChatFailureKind, init: ChatSendErrorInit = {}) {
    super(`Chat send failed (${kind}${init.status ? ` ${init.status}` : ''})`);
    this.name = 'ChatSendError';
    this.kind = kind;
    this.status = init.status;
    this.detail = init.detail;
  }
}

export function kindForStatus(status: number): ChatFailureKind {
  if (status === 401) return 'session';
  if (status === 403) return 'forbidden';
  if (status === 429) return 'rate_limited';
  if (status === 400 || status === 413 || status === 422) return 'bad_request';
  if (status === 502 || status === 503 || status === 504) return 'starting';
  if (status >= 500) return 'server';
  return 'unknown';
}

const MAX_DETAIL_CHARS = 160;

/**
 * The part of an error response that is safe and useful to show: the server's `detail` when it is a plain string. A 422 is left out whole, because
 * FastAPI's validation errors echo the input that was rejected, which is the person's own message. Control characters and length are cleaned up.
 */
export function safeDetail(status: number, body: unknown): string | undefined {
  if (status === 422 || !body || typeof body !== 'object') return undefined;
  const detail = (body as { detail?: unknown }).detail;
  if (typeof detail !== 'string') return undefined;
  const printable = Array.from(detail, ch => (ch.charCodeAt(0) < 32 || ch.charCodeAt(0) === 127 ? ' ' : ch)).join('');
  const flat = printable.replace(/\s+/g, ' ').trim();
  if (!flat) return undefined;
  return flat.length > MAX_DETAIL_CHARS ? `${flat.slice(0, MAX_DETAIL_CHARS - 1).trimEnd()}…` : flat;
}

/** What went wrong with any value thrown while sending, as a ChatSendError. A deliberate Stop (AbortError) is not a failure and must be handled by the caller first. */
export function toChatSendError(err: unknown): ChatSendError {
  if (err instanceof ChatSendError) return err;
  // fetch() rejects with a TypeError when the request never completes (offline, DNS, refused, blocked), and a body read can reject the same way.
  if (err instanceof TypeError) return new ChatSendError('network');
  return new ChatSendError('unknown');
}

export interface ChatErrorView {
  /** Plain words for the person. */
  message: string;
  /** Whether sending the same message again is a sensible thing to offer. */
  retryable: boolean;
  /** One line of technical detail, safe to show on request and in a bug report: no message text, no user content. */
  details: string;
}

export function describeChatError(err: ChatSendError): ChatErrorView {
  const where = err.status ? `HTTP ${err.status}` : err.kind === 'network' ? 'no response' : err.kind === 'interrupted' ? 'stream dropped' : 'no status';
  const details = [where, err.kind, err.detail].filter(Boolean).join(' · ');
  switch (err.kind) {
    case 'session':
      return { message: 'Your sign-in has expired, so that message was not sent. Sign in again, then send it again.', retryable: false, details };
    case 'forbidden':
      return { message: 'Sonic refused that request for your account, so the message was not sent.', retryable: false, details };
    case 'bad_request':
      return {
        message: err.status === 413
          ? 'That message (with its attachments) was too large for Sonic to accept. Try a shorter message or smaller files.'
          : 'Sonic could not accept that message as it was sent. If it has attachments, try without them.',
        retryable: false,
        details,
      };
    case 'rate_limited':
      return { message: 'Sonic is getting too many requests right now. Wait a minute, then try again.', retryable: true, details };
    case 'starting':
      return {
        message: "Sonic's server did not answer properly. It may be starting up or restarting. Give it a moment and try again.",
        retryable: true,
        details,
      };
    case 'server':
      return { message: "Something went wrong on Sonic's server, not with your message. Trying again may work.", retryable: true, details };
    case 'network':
      return { message: 'Could not reach Sonic. Check your connection, then try again.', retryable: true, details };
    case 'interrupted':
      return { message: 'The connection dropped before Sonic finished answering. Try again.', retryable: true, details };
    default:
      return { message: 'That message could not be sent, and the cause is not known. Trying again may work.', retryable: true, details };
  }
}

// src/api.ts
declare global {
  interface Window {
    Clerk?: {
      session?: {
        getToken: () => Promise<string | null>;
      };
    };
  }
}
const isLocal = window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1";

export const BASE_URL = import.meta.env.VITE_API_BASE || 
  (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1' 
    ? "http://localhost:8000" 
    : "https://saapp.onrender.com");

function getClerkPrimaryEmail(): string | null {
  const clerkUser = (window as any)?.Clerk?.user;
  const email = clerkUser?.primaryEmailAddress?.emailAddress || clerkUser?.emailAddresses?.[0]?.emailAddress;
  return typeof email === 'string' && email.trim() ? email.trim() : null;
}

function getEmbeddedMode(): boolean {
  const hash = window.location.hash.includes('?') ? window.location.hash.split('?')[1] : '';
  const searchParams = new URLSearchParams(window.location.search || hash);
  return searchParams.get('mode') === 'embed' || window.location.href.includes('mode=embed');
}

export function isGuestPrincipal(principal: string | null | undefined): boolean {
  return principal === 'guest' || principal === 'guest_bty';
}

export function getEffectivePrincipal(): string {
  const embeddedMode = getEmbeddedMode();
  const storedPrincipal = localStorage.getItem('principal');
  const clerkEmail = getClerkPrimaryEmail();
  const preferredPrincipal = clerkEmail || storedPrincipal || (embeddedMode ? 'guest_bty' : 'guest');
  const candidatePrincipal = embeddedMode ? 'guest_bty' : preferredPrincipal;

  if (isGuestPrincipal(candidatePrincipal)) {
    const guestToken = candidatePrincipal === 'guest_bty' ? 'guest-bty-token' : 'guest-sandbox-token';
    localStorage.setItem('guest_token', guestToken);
    localStorage.setItem('principal', candidatePrincipal);
    localStorage.setItem('x-user-id', candidatePrincipal);
    return candidatePrincipal;
  }

  if (clerkEmail && localStorage.getItem('principal') !== clerkEmail) {
    localStorage.setItem('principal', clerkEmail);
    localStorage.setItem('x-user-id', clerkEmail);
  }

  return (localStorage.getItem('principal') || candidatePrincipal).trim();
}

export interface ChatResponse {
  user: string;
  email: string;
  answer: string;
}

export interface MeResponse {
  username: string;
  email?: string | null;
  groups: string[];
}

/** What the server tells the browser about a user's own GitHub token: never the token itself. */
export interface GitHubTokenStatus {
  configured: boolean;
  last4: string | null;
  github_login: string | null;
}

async function readErrorDetail(res: Response, fallback: string): Promise<string> {
  try {
    const body = await res.json();
    if (typeof body?.detail === "string" && body.detail) return body.detail;
  } catch {
    // not JSON; use the generic message
  }
  return fallback;
}

export async function getGitHubToken(): Promise<GitHubTokenStatus> {
  const authHeaders = await getAuthHeaders();
  const res = await fetch(`${BASE_URL}/api/settings/github-token`, {
    headers: { ...authHeaders }
  });
  if (!res.ok) throw new Error(await readErrorDetail(res, "Failed to fetch GitHub token setting."));
  return res.json();
}

/** Saves a token (the server verifies it with GitHub first), or removes it when `token` is null. */
export async function updateGitHubToken(token: string | null): Promise<GitHubTokenStatus> {
  const authHeaders = await getAuthHeaders();
  const res = await fetch(`${BASE_URL}/api/settings/github-token`, {
    method: "PUT",
    headers: {
      "Content-Type": "application/json",
      ...authHeaders
    },
    body: JSON.stringify({ token })
  });
  if (!res.ok) throw new Error(await readErrorDetail(res, "Failed to update GitHub token setting."));
  return res.json();
}

/**
 * Security Helper: Generates authorization headers.
 * It checks if they are logged in as a guest, or requests a fresh JWT from Clerk's global instance.
 */
export const getAuthHeaders = async (): Promise<Record<string, string>> => {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };

  const principal = getEffectivePrincipal();

  let token: string | null | undefined = null;
  if (isGuestPrincipal(principal)) {
    token = principal === 'guest_bty' ? 'guest-bty-token' : 'guest-sandbox-token';
  } else {
    // The backend only trusts an email principal when it's backed by a verified Clerk JWT, so
    // wait for Clerk to finish loading rather than sending the email with no token. No guest_token
    // fallback here: a stale one from an earlier guest visit must not turn a signed-in user into the guest.
    token = await (await waitForClerk())?.session?.getToken();
  }

  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }

  headers["X-Principal"] = principal;
  headers["x-user-id"] = principal;

  return headers;
};
/**
 * Fetch current principal from backend.
 */
export async function getMe(usernameHint?: string): Promise<MeResponse> {
  const authHeaders = await getAuthHeaders();
  const clerkEmail = getClerkPrimaryEmail();
  const hint = usernameHint || clerkEmail || (typeof window !== "undefined" ? (window as any).CURRENT_USER : undefined);
  if (hint) {
    authHeaders["x-user-id"] = hint;
    authHeaders["X-Principal"] = hint;
    if (hint.includes('@')) {
      localStorage.setItem('principal', hint);
      localStorage.setItem('x-user-id', hint);
    }
  }

  const res = await fetch(`${BASE_URL}/api/me`, { headers: authHeaders });
  if (!res.ok) {
    throw new Error("Failed to fetch /api/me");
  }
  return res.json();
}

async function waitForClerk(): Promise<any> {
  const clerk = (window as any).Clerk;
  // If Clerk already loaded a session or is done loading, return immediately
  if (clerk?.session || (clerk?.loaded && !clerk?.user)) return clerk;

  for (let i = 0; i < 20; i++) { // poll up to 20 times (2 seconds total)
    await new Promise((res) => setTimeout(res, 100));
    const currentClerk = (window as any).Clerk;
    if (currentClerk?.session || currentClerk?.loaded) {
      return currentClerk;
    }
  }
  return (window as any).Clerk;
}

export async function logLogin(): Promise<void> {
  try {
    const authHeaders = await getAuthHeaders();
    await fetch(`${BASE_URL}/api/log-login`, {
      method: "POST",
      headers: { ...authHeaders }
    });
  } catch (err) {
    console.error("Failed to transmit login log event:", err);
  }
}
export interface KnowledgeBase {
  id: string;
  display_name: string;
}

/**
 * Fetch accessible workspace claims
 */
export async function getAffiliates(username: string): Promise<KnowledgeBase[]> {
  const authHeaders = await getAuthHeaders();
  const response = await fetch(
    `${BASE_URL}/api/affiliates?username=${encodeURIComponent(username)}`,
    { headers: { ...authHeaders } }
  );

  if (!response.ok) {
    throw new Error("Could not load secure workspace claims.");
  }
  const data = await response.json();
  return data.accessible_affiliates;
}

/**
 * Retrieve directory group list
 */
export async function getUserGroups(username: string): Promise<string[]> {
  const authHeaders = await getAuthHeaders();
  const res = await fetch(`${BASE_URL}/api/user/groups?username=${encodeURIComponent(username)}`, {
    headers: { ...authHeaders }
  });
  if (!res.ok) {
    throw new Error("Failed to retrieve directory authorization groups.");
  }
  console.log("successfully loaded user groups")
  const data = await res.json();
  return Array.isArray(data) ? data : (data.groups || []);
}

/**
 * Fetch vector document list for the space
 */
export async function getIngestedDocuments(username: string, affiliate: string): Promise<any[]> {
  const authHeaders = await getAuthHeaders();
  const res = await fetch(
    `${BASE_URL}/api/documents?affiliate=${encodeURIComponent(affiliate)}`,
    { headers: { ...authHeaders } }
  );
  if (!res.ok) throw new Error("Failed to fetch indexed document manifest.");
  return res.json();
}

/**
 * Execute secure files ingest pipeline
 */
export async function uploadDocuments(username: string, affiliate: string, files: FileList): Promise<any> {
  const formData = new FormData();
  Array.from(files).forEach((file) => formData.append("files", file));

  const authHeaders = await getAuthHeaders();
  delete authHeaders["Content-Type"]; // must let the browser set the multipart boundary

  const res = await fetch(
    `${BASE_URL}/api/upload?affiliate=${encodeURIComponent(affiliate)}`,
    {
      method: "POST",
      headers: { ...authHeaders },
      body: formData,
    }
  );

  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || "Upload pipeline execution failed.");
  }
  return res.json();
}

/**
 * Purge a document from vector space
 */
export async function deleteDocument(username: string, affiliate: string, docId: string): Promise<any> {
  const authHeaders = await getAuthHeaders();
  const res = await fetch(
    `${BASE_URL}/api/documents/${docId}?affiliate=${encodeURIComponent(affiliate)}`,
    {
      method: "DELETE",
      headers: { ...authHeaders },
    }
  );
  if (!res.ok) throw new Error("Failed to purge document from vector space.");
  return res.json();
}

/**
 * Get / set the current user's RAG strictness mode ("strict" | "open")
 */
export async function getRagMode(): Promise<{ rag_mode: string }> {
  const authHeaders = await getAuthHeaders();
  const res = await fetch(`${BASE_URL}/api/settings/rag-mode`, {
    headers: { ...authHeaders }
  });
  if (!res.ok) throw new Error("Failed to fetch RAG mode setting.");
  return res.json();
}

export async function updateRagMode(ragMode: string): Promise<{ rag_mode: string }> {
  const authHeaders = await getAuthHeaders();
  const res = await fetch(`${BASE_URL}/api/settings/rag-mode`, {
    method: "PUT",
    headers: {
      "Content-Type": "application/json",
      ...authHeaders
    },
    body: JSON.stringify({ rag_mode: ragMode })
  });
  if (!res.ok) throw new Error("Failed to update RAG mode setting.");
  return res.json();
}

/**
 * Get / set the current user's deep thinking setting — lets the tool agent take more
 * ReAct steps (and reconsider a premature answer more than once) at the cost of latency.
 */
export async function getDeepThinking(): Promise<{ deep_thinking: boolean }> {
  const authHeaders = await getAuthHeaders();
  const res = await fetch(`${BASE_URL}/api/settings/deep-thinking`, {
    headers: { ...authHeaders }
  });
  if (!res.ok) throw new Error("Failed to fetch deep thinking setting.");
  return res.json();
}

export async function updateDeepThinking(deepThinking: boolean): Promise<{ deep_thinking: boolean }> {
  const authHeaders = await getAuthHeaders();
  const res = await fetch(`${BASE_URL}/api/settings/deep-thinking`, {
    method: "PUT",
    headers: {
      "Content-Type": "application/json",
      ...authHeaders
    },
    body: JSON.stringify({ deep_thinking: deepThinking })
  });
  if (!res.ok) throw new Error("Failed to update deep thinking setting.");
  return res.json();
}

/**
 * Get / set the current user's pinned target repo ("owner/repo") — when set, tool_agent_node
 * uses it directly instead of guessing the repo from conversation text. null/"" clears the pin.
 */
export async function getTargetRepo(): Promise<{ target_repo: string | null }> {
  const authHeaders = await getAuthHeaders();
  const res = await fetch(`${BASE_URL}/api/settings/target-repo`, {
    headers: { ...authHeaders }
  });
  if (!res.ok) throw new Error("Failed to fetch target repo setting.");
  return res.json();
}

export async function updateTargetRepo(targetRepo: string | null): Promise<{ target_repo: string | null }> {
  const authHeaders = await getAuthHeaders();
  const res = await fetch(`${BASE_URL}/api/settings/target-repo`, {
    method: "PUT",
    headers: {
      "Content-Type": "application/json",
      ...authHeaders
    },
    body: JSON.stringify({ target_repo: targetRepo })
  });
  if (!res.ok) throw new Error("Failed to update target repo setting.");
  return res.json();
}

/**
 * Get / set the current user's target Google Doc — a generic "document SAAPP can append to
 * when asked" setting, not tied to any one purpose. Accepts either a raw Doc ID or a full
 * docs.google.com URL; the backend extracts the ID either way.
 */
export async function getTargetDoc(): Promise<{ target_doc_id: string | null }> {
  const authHeaders = await getAuthHeaders();
  const res = await fetch(`${BASE_URL}/api/settings/target-doc`, {
    headers: { ...authHeaders }
  });
  if (!res.ok) throw new Error("Failed to fetch target document setting.");
  return res.json();
}

export async function updateTargetDoc(docUrlOrId: string | null): Promise<{ target_doc_id: string | null; warning: string | null }> {
  const authHeaders = await getAuthHeaders();
  const res = await fetch(`${BASE_URL}/api/settings/target-doc`, {
    method: "PUT",
    headers: {
      "Content-Type": "application/json",
      ...authHeaders
    },
    body: JSON.stringify({ doc_url_or_id: docUrlOrId })
  });
  if (!res.ok) throw new Error("Failed to update target document setting.");
  return res.json();
}

/**
 * Get / set whether the current user has already dismissed the onboarding/help overlay —
 * drives whether it auto-shows on sign-in (see Layout.tsx).
 */
export async function getHasSeenHelp(): Promise<{ has_seen_help: boolean }> {
  const authHeaders = await getAuthHeaders();
  const res = await fetch(`${BASE_URL}/api/settings/has-seen-help`, {
    headers: { ...authHeaders }
  });
  if (!res.ok) throw new Error("Failed to fetch has-seen-help setting.");
  return res.json();
}

export async function updateHasSeenHelp(hasSeenHelp: boolean): Promise<{ has_seen_help: boolean }> {
  const authHeaders = await getAuthHeaders();
  const res = await fetch(`${BASE_URL}/api/settings/has-seen-help`, {
    method: "PUT",
    headers: {
      "Content-Type": "application/json",
      ...authHeaders
    },
    body: JSON.stringify({ has_seen_help: hasSeenHelp })
  });
  if (!res.ok) throw new Error("Failed to update has-seen-help setting.");
  return res.json();
}

/**
 * Get / set the current user's IANA timezone (e.g. "America/New_York") — used when the agent
 * schedules a Google Calendar event on the user's behalf.
 */
export async function getTimezone(): Promise<{ timezone: string }> {
  const authHeaders = await getAuthHeaders();
  const res = await fetch(`${BASE_URL}/api/settings/timezone`, {
    headers: { ...authHeaders }
  });
  if (!res.ok) throw new Error("Failed to fetch timezone setting.");
  return res.json();
}

export async function updateTimezone(timezone: string): Promise<{ timezone: string }> {
  const authHeaders = await getAuthHeaders();
  const res = await fetch(`${BASE_URL}/api/settings/timezone`, {
    method: "PUT",
    headers: {
      "Content-Type": "application/json",
      ...authHeaders
    },
    body: JSON.stringify({ timezone })
  });
  if (!res.ok) throw new Error("Failed to update timezone setting.");
  return res.json();
}

/**
 * Per-user Google Calendar connection (Integrations page) — the backend does the actual OAuth
 * code exchange server-side; the frontend only ever sees an authorization URL to navigate to and
 * a plain connected/disconnected status afterward.
 */
export interface CalendarConnectionStatus {
  connected: boolean;
  account_email: string | null;
  scopes: string[];
  connected_at: string | null;
}

export async function getCalendarStatus(): Promise<CalendarConnectionStatus> {
  const authHeaders = await getAuthHeaders();
  const res = await fetch(`${BASE_URL}/api/calendar/status`, {
    headers: { ...authHeaders }
  });
  if (!res.ok) throw new Error("Failed to fetch Google Calendar connection status.");
  return res.json();
}

export async function startCalendarConnection(returnTo: string): Promise<{ authorization_url: string }> {
  const authHeaders = await getAuthHeaders();
  const res = await fetch(`${BASE_URL}/api/calendar/connect/start?return_to=${encodeURIComponent(returnTo)}`, {
    method: "POST",
    headers: { ...authHeaders }
  });
  if (!res.ok) throw new Error("Failed to start Google Calendar connection.");
  return res.json();
}

export async function disconnectCalendar(): Promise<{ connected: boolean }> {
  const authHeaders = await getAuthHeaders();
  const res = await fetch(`${BASE_URL}/api/calendar/disconnect`, {
    method: "POST",
    headers: { ...authHeaders }
  });
  if (!res.ok) throw new Error("Failed to disconnect Google Calendar.");
  return res.json();
}

/**
 * List the current user's conversation threads
 */
export interface ConversationSummary {
  session_id: string;
  title: string;
  updated_at: string;
}

export async function listConversations(): Promise<ConversationSummary[]> {
  const authHeaders = await getAuthHeaders();
  const res = await fetch(`${BASE_URL}/api/conversations`, {
    headers: { ...authHeaders }
  });
  if (!res.ok) {
    throw new Error("Failed to list conversations.");
  }
  return res.json();
}

export interface ConversationPage {
  session_id: string;
  title: string;
  messages: any[];
  /** Index (in the full saved transcript) of the first message in this page; 0 means nothing earlier. */
  start: number;
  total: number;
}

/**
 * Fetch one page of a conversation thread — the newest `limit` messages by default, or the `limit`
 * messages just before index `before` to page backwards (pass the previous page's `start`).
 */
export async function getConversation(
  sessionId: string,
  opts: { limit?: number; before?: number } = {}
): Promise<ConversationPage> {
  const authHeaders = await getAuthHeaders();
  const params = new URLSearchParams();
  if (opts.limit !== undefined) params.set("limit", String(opts.limit));
  if (opts.before !== undefined) params.set("before", String(opts.before));
  const query = params.toString();
  const res = await fetch(`${BASE_URL}/api/conversations/${encodeURIComponent(sessionId)}${query ? `?${query}` : ""}`, {
    headers: { ...authHeaders }
  });
  if (!res.ok) {
    throw new Error("Failed to load conversation.");
  }
  return res.json();
}

/**
 * Delete one conversation thread
 */
export async function deleteConversation(sessionId: string): Promise<any> {
  const authHeaders = await getAuthHeaders();
  const res = await fetch(`${BASE_URL}/api/conversations/${encodeURIComponent(sessionId)}`, {
    method: "DELETE",
    headers: { ...authHeaders }
  });
  if (!res.ok) {
    throw new Error("Failed to delete conversation.");
  }
  return res.json();
}

/**
 * List the current user's saved memory facts
 */
export interface UserFact {
  id: string;
  username: string;
  category: string;
  fact: string;
  source: "explicit" | "inferred" | "pattern";
  confidence: number;
  effective_confidence: number;
  created_at: string;
  updated_at: string;
  active: boolean;
  goal_status: string | null;
  last_nudged_at: string | null;
}

export async function listMemoryFacts(): Promise<UserFact[]> {
  const authHeaders = await getAuthHeaders();
  const res = await fetch(`${BASE_URL}/api/memory`, {
    headers: { ...authHeaders }
  });
  if (!res.ok) {
    throw new Error("Failed to load memory.");
  }
  return res.json();
}

/**
 * Delete one memory fact
 */
export async function deleteMemoryFact(factId: string): Promise<any> {
  const authHeaders = await getAuthHeaders();
  const res = await fetch(`${BASE_URL}/api/memory/${encodeURIComponent(factId)}`, {
    method: "DELETE",
    headers: { ...authHeaders }
  });
  if (!res.ok) {
    throw new Error("Failed to delete memory fact.");
  }
  return res.json();
}

/**
 * Clear all memory facts for the current user
 */
export async function clearMemoryFacts(): Promise<any> {
  const authHeaders = await getAuthHeaders();
  const res = await fetch(`${BASE_URL}/api/memory`, {
    method: "DELETE",
    headers: { ...authHeaders }
  });
  if (!res.ok) {
    throw new Error("Failed to clear memory.");
  }
  return res.json();
}

/**
 * Connected local folder: a read-only server-side snapshot of the folder the user picked, which
 * Sonic reads instead of GitHub's copy (see localWorkspace.ts for the browser side).
 */
export interface LocalWorkspaceStatus {
  connected: boolean;
  name?: string;
  file_count?: number;
  total_bytes?: number;
  synced_at?: number;
}

export interface LocalWorkspaceSyncResult {
  accepted: number;
  rejected: { path: string; reason: string }[];
  file_count: number;
  total_bytes: number;
}

export async function getLocalWorkspaceStatus(): Promise<LocalWorkspaceStatus> {
  const authHeaders = await getAuthHeaders();
  const res = await fetch(`${BASE_URL}/api/local-workspace`, { headers: { ...authHeaders } });
  if (!res.ok) {
    throw new Error("Failed to check the connected folder.");
  }
  return res.json();
}

export async function syncLocalWorkspaceBatch(batch: {
  name: string;
  reset: boolean;
  files: { path: string; content: string }[];
  deleted: string[];
}): Promise<LocalWorkspaceSyncResult> {
  const authHeaders = await getAuthHeaders();
  const res = await fetch(`${BASE_URL}/api/local-workspace/sync`, {
    method: "POST",
    headers: { ...authHeaders },
    body: JSON.stringify(batch)
  });
  if (!res.ok) {
    throw new Error(res.status === 403 ? "Sign in to connect a local folder." : "Failed to sync the folder.");
  }
  return res.json();
}

export async function disconnectLocalWorkspace(): Promise<void> {
  const authHeaders = await getAuthHeaders();
  const res = await fetch(`${BASE_URL}/api/local-workspace`, {
    method: "DELETE",
    headers: { ...authHeaders }
  });
  if (!res.ok) {
    throw new Error("Failed to disconnect the folder.");
  }
}

/**
 * Dev utility login validator
 */
export async function verifyIdentity(username: string): Promise<boolean> {
  try {
    const authHeaders = await getAuthHeaders();
    const response = await fetch(`${BASE_URL}/api/login`, {
      method: "POST",
      headers: { 
        "Content-Type": "application/json",
        ...authHeaders 
      },
      body: JSON.stringify({ username }),
    });
    return response.ok;
  } catch (error) {
    console.error("Identity transmission subsystem error:", error);
    return false;
  }
}

/**
 * File attachment helper for live chats
 */
export async function uploadAttachment(username: string, sessionId: string, file: File): Promise<any> {
  const formData = new FormData();
  formData.append("username", username);
  formData.append("session_id", sessionId);
  formData.append("file", file);

  const authHeaders = await getAuthHeaders();
  const res = await fetch(`${BASE_URL}/api/upload-attachment`, {
    method: "POST",
    headers: { ...authHeaders },
    body: formData,
  });

  if (!res.ok) {
    throw new Error("Attachment upload failed.");
  }

  return res.json();
}

/**
 * Send streaming chat events
 */
/**
 * Queues a message into the turn Sonic is currently working on, to be applied at its next step.
 * Anything other than "queued" means it could not be applied (no running turn, the work phase is
 * already over, or the queue is full); the caller then sends it as a normal follow-up instead.
 */
export async function steerChat(sessionId: string, message: string): Promise<{ status: string }> {
  const authHeaders = await getAuthHeaders();
  const res = await fetch(`${BASE_URL}/api/chat/steer`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders },
    body: JSON.stringify({ session_id: sessionId, message }),
  });
  if (!res.ok) {
    throw new Error("Failed to send steering message.");
  }
  return res.json();
}

export async function sendChatMessage(
  username: string,
  question: string,
  attachments: { filename: string; content: string }[],
  borderScope: string,
  session_id: string,
  onTokenReceived: (token: string) => void,
  onTraceReceived?: (payload: any) => void,
  signal?: AbortSignal
): Promise<void> {
  const authHeaders = await getAuthHeaders();
  const response = await fetch(`${BASE_URL}/api/chat`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...authHeaders
    },
    body: JSON.stringify({
      username,
      question,
      affiliate: borderScope,
      attachments,
      session_id,
    }),
    signal,
  });

  if (!response.ok) {
    throw new Error("Failed to initialize communication stream.");
  }

  const reader = response.body?.getReader();
  const decoder = new TextDecoder();

  if (!reader) {
    throw new Error("ReadableStream is unsupported.");
  }

  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";

    for (const line of lines) {
      const trimmed = line.trim();
      if (trimmed.startsWith("data: ")) {
        try {
          const payload = JSON.parse(trimmed.substring(6));
          if (payload.event === "trace" && onTraceReceived) {
            onTraceReceived(payload);
          }
        } catch {
          // ignore non-JSON chunks
        }

        onTokenReceived(trimmed);
      }
    }
  }

  const trimmed = buffer.trim();
  if (trimmed.startsWith("data: ")) {
    try {
      const payload = JSON.parse(trimmed.substring(6));
      if (payload.event === "trace" && onTraceReceived) {
        onTraceReceived(payload);
      }
    } catch {
      // ignore non-JSON chunks
    }

    onTokenReceived(trimmed);
  }
}

/**
 * Static Task Board Object Endpoint Exporter
 */
export const api = {
  
  getMe: async (username: string) => {

    const authHeaders = await getAuthHeaders();
    const response = await fetch(`${BASE_URL}/api/me`, {
      method: "GET",
      headers: {
        "Content-Type": "application/json",
        ...authHeaders
      }
    });
    if (!response.ok) {
      throw new Error(`Failed to fetch /api/me Status: ${response.status}`);
    }
    return response.json();
  },
  logout: async () => {
  // 1. Clear guest token
  localStorage.removeItem('guest_token');

  // 2. Clear Clerk session
  const clerk = (window as any).Clerk;
  if (clerk) {
    // This signs the user out of Clerk and triggers a redirect to your login/home
    await clerk.signOut();
  }
  
  // Optional: Redirect the user to the landing page immediately
  window.location.href = "/";
},
  getAffiliates,
  getGitHubToken,
  updateGitHubToken,
  getUserGroups,
  getIngestedDocuments,
  uploadDocuments,
  deleteDocument,
  verifyIdentity,
  uploadAttachment,
  sendChatMessage,
  steerChat,
  listConversations,
  getConversation,
  deleteConversation,
  listMemoryFacts,
  deleteMemoryFact,
  clearMemoryFacts,
  getRagMode,
  updateRagMode,
  getDeepThinking,
  updateDeepThinking,
  getTargetRepo,
  updateTargetRepo,
  getTargetDoc,
  updateTargetDoc,
  getHasSeenHelp,
  updateHasSeenHelp,
  getTimezone,
  updateTimezone,
  getCalendarStatus,
  startCalendarConnection,
  disconnectCalendar
};
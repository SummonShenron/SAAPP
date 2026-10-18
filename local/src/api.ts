// src/api.ts
import type { SessionCheck } from './utils/authReady';
import { ChatSendError, kindForStatus, safeDetail, toChatSendError } from './chatErrors';

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
const VERIFY_TIMEOUT_MS = 25_000; // one try at confirming the session may wait this long on a server that is still waking up

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
 * One try at confirming this browser is fully signed in: for a Clerk user, Clerk has loaded and has a session that can hand over a token;
 * for anyone, the server has accepted those credentials on a cheap authenticated call. 'retry' means "not yet" (Clerk still loading, no
 * token yet, the server still waking up or unreachable), 'denied' means Clerk has no session or the server refused the token. The page waits
 * on this before it lets anyone send anything (components/Layout.tsx, hooks/useAuthReady.ts), because a message sent before it is true goes
 * out with no usable login token and is refused.
 */
export async function verifySession(): Promise<SessionCheck> {
  const principal = getEffectivePrincipal();
  const headers: Record<string, string> = { "Content-Type": "application/json", "X-Principal": principal, "x-user-id": principal };
  if (isGuestPrincipal(principal)) {
    headers["Authorization"] = `Bearer ${principal === 'guest_bty' ? 'guest-bty-token' : 'guest-sandbox-token'}`;
  } else {
    const clerk = (window as any).Clerk;
    if (!clerk?.loaded) return 'retry'; // the script has not finished starting (slow on a phone)
    if (!clerk.session) return 'denied'; // loaded, and nobody is signed in
    const token = await clerk.session.getToken();
    if (!token) return 'retry';
    headers["Authorization"] = `Bearer ${token}`;
  }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), VERIFY_TIMEOUT_MS); // a sleeping server can hold a request open for a long time
  try {
    const res = await fetch(`${BASE_URL}/api/affiliates?username=${encodeURIComponent(principal)}`, { headers, signal: controller.signal });
    if (res.ok) return 'ready';
    return res.status === 401 || res.status === 403 ? 'denied' : 'retry';
  } catch {
    return 'retry';
  } finally {
    clearTimeout(timer);
  }
}

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
 * Whether Sonic may start a conversation with something anchored to what you told it (off until turned on), and the
 * call that asks it, once you open an existing conversation, whether it has anything to say first.
 */
export async function getProactiveOpening(): Promise<{ enabled: boolean }> {
  const authHeaders = await getAuthHeaders();
  const res = await fetch(`${BASE_URL}/api/settings/proactive-opening`, { headers: { ...authHeaders } });
  if (!res.ok) throw new Error("Failed to fetch the message-first setting.");
  return res.json();
}

export async function updateProactiveOpening(enabled: boolean): Promise<{ enabled: boolean }> {
  const authHeaders = await getAuthHeaders();
  const res = await fetch(`${BASE_URL}/api/settings/proactive-opening`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", ...authHeaders },
    body: JSON.stringify({ enabled })
  });
  if (!res.ok) throw new Error("Failed to update the message-first setting.");
  return res.json();
}

/**
 * Admin only: whether Sonic tells you about new production incidents (from errAgent) when you open a conversation. `available`
 * is false for everyone else and where errAgent reads aren't configured, so the menu item can stay hidden.
 */
export async function getOpsDigest(): Promise<{ enabled: boolean; available: boolean }> {
  const authHeaders = await getAuthHeaders();
  const res = await fetch(`${BASE_URL}/api/settings/ops-digest`, { headers: { ...authHeaders } });
  if (!res.ok) throw new Error("Failed to fetch the incident digest setting.");
  return res.json();
}

export async function updateOpsDigest(enabled: boolean): Promise<{ enabled: boolean; available: boolean }> {
  const authHeaders = await getAuthHeaders();
  const res = await fetch(`${BASE_URL}/api/settings/ops-digest`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", ...authHeaders },
    body: JSON.stringify({ enabled })
  });
  if (!res.ok) throw new Error("Failed to update the incident digest setting.");
  return res.json();
}

export interface OpeningMessage {
  type: 'ai';
  content: string;
  sent_at?: string;
  initiated: true;
}

export async function requestConversationOpening(sessionId: string): Promise<{ message: OpeningMessage | null }> {
  const authHeaders = await getAuthHeaders();
  const res = await fetch(`${BASE_URL}/api/chat/opening`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders },
    body: JSON.stringify({ session_id: sessionId })
  });
  if (!res.ok) throw new Error("Failed to check for an opening message.");
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
/** What a person tells Sonic about themselves (the "About you" card on the Account page; backend/utils/profile_utils.py). */
export interface UserProfile {
  name: string;
  pronouns: string;
  work?: string;
  interests: string[];
  style: { length: string; tone: string; humor: string };
  avoid: string[];
  offers: boolean;
}

/** The server refused a value: which field, and the reason to show next to it. */
export class ProfileSaveError extends Error {
  readonly field: string;
  constructor(field: string, message: string) {
    super(message);
    this.name = 'ProfileSaveError';
    this.field = field;
  }
}

export async function getProfile(): Promise<UserProfile> {
  const authHeaders = await getAuthHeaders();
  const res = await fetch(`${BASE_URL}/api/settings/profile`, { headers: { ...authHeaders } });
  if (!res.ok) throw new Error("Failed to fetch your profile.");
  return res.json();
}

export async function updateProfile(profile: UserProfile): Promise<UserProfile> {
  const authHeaders = await getAuthHeaders();
  const res = await fetch(`${BASE_URL}/api/settings/profile`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", ...authHeaders },
    body: JSON.stringify(profile),
  });
  if (res.status === 422) {
    let detail: { field?: string; message?: string } = {};
    try { detail = (await res.json())?.detail ?? {}; } catch { /* no JSON body */ }
    throw new ProfileSaveError(detail.field ?? '', detail.message ?? "That can't be saved.");
  }
  if (res.status === 403) throw new Error("Sign in to keep a profile.");
  if (!res.ok) throw new Error("Could not save your profile. Try again.");
  return res.json();
}

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

export interface SonicProfile {
  tagline: string;
  into: string[];
  prefers: string[];
  wont: string[];
  voice: string;
  humor: string;
  interests: string[];
  note: string;
}

/** Sonic's character profile, for the help panel's About card. */
export async function getSonicProfile(): Promise<SonicProfile> {
  const authHeaders = await getAuthHeaders();
  const res = await fetch(`${BASE_URL}/api/sonic-profile`, { headers: { ...authHeaders } });
  if (!res.ok) throw new Error("Failed to fetch Sonic's profile.");
  return res.json();
}

// ---- Admin: Sonic Health (Global_Admins only; the server enforces it) ----

export interface ObservationEvidence {
  rate?: number | null;
  previous_rate?: number | null;
  denominator?: number | null;
  [key: string]: unknown;
}

export interface ObservationView {
  key: string;
  state: 'pending' | 'live' | 'expired' | 'retired';
  applies_to: string[];
  approved_text: string | null;
  approved_evidence: ObservationEvidence | null;
  approved_at: string | null;
  approved_by: string | null;
  proposed_text: string | null;
  proposed_evidence: ObservationEvidence | null;
  last_confirmed_at: string | null;
  expires_at: string | null;
}

export interface CountersSummary {
  days: number;
  totals: Record<string, number>;
  previous_totals: Record<string, number>;
  rates: Record<string, number | null>;
  previous_rates: Record<string, number | null>;
  change: Record<string, number | null>;
}

export interface SafetySummary {
  days: number;
  risk_raised: number;
  people_with_risk_raised: number;
  by_level: Record<string, number>;
  by_detector: Record<string, number>;
  per_day: Record<string, number>;
  risk_turns: number;
  reply_revised: number;
  revised_rate: number | null;
  line_in_reply: number;
  line_rate: number | null;
  last_resort_line: number;
  last_resort_rate: number | null;
  outage_fallbacks: number;
  ladder: Record<string, Record<string, number>>;
}

/** 'five' is the five-hour window that starts at the first use; 'my_day' / 'my_five' are one person's own limits. */
export type BudgetScope = 'day' | 'month' | 'five' | 'my_day' | 'my_five';

export interface BudgetDetail {
  scope: BudgetScope;
  unit: 'usd' | 'tokens';
  spent: number;
  limit: number;
  fraction: number;
}

/**
 * Where the usage budget stands (backend/utils/budget_guard.py). Everyone gets `level`, `message`, `paused` and `resets_at`; only an
 * admin also gets the amounts and percentages. `level` is "off" when no budget is configured.
 */
export interface BudgetView {
  level: 'off' | 'ok' | 'notice' | 'throttled' | 'blocked';
  message: string;
  paused: string[];
  resets_at: string | null;
  fraction?: number;
  scope?: BudgetScope;
  unit?: 'usd' | 'tokens';
  spent?: number;
  limit?: number;
  detail?: BudgetDetail[];
  unpriced_tokens?: number;
  /** Everyone: the limit in force is this person's own, not the shared one. */
  own_limit?: boolean;
  /** Admins only: the shared budget has paused chat and +25% is on offer. */
  can_extend?: boolean;
  /** Admins only: how far the block line has already been raised (0.25 = +25%). */
  extended_by?: number;
}

export async function getBudget(): Promise<BudgetView> {
  const authHeaders = await getAuthHeaders();
  const res = await fetch(`${BASE_URL}/api/budget`, { headers: { ...authHeaders } });
  if (!res.ok) throw new Error("Failed to fetch the usage budget.");
  return res.json();
}

/** Admins only: raises the block line of the shared budget that paused chat by another 25%, until that budget resets. Returns the new state. */
export async function extendBudget(): Promise<BudgetView> {
  const authHeaders = await getAuthHeaders();
  const res = await fetch(`${BASE_URL}/api/budget/extend`, { method: "POST", headers: { ...authHeaders } });
  if (!res.ok) {
    let detail = '';
    try { detail = (await res.json())?.detail ?? ''; } catch { /* no JSON body */ }
    throw new Error(typeof detail === 'string' && detail ? detail : `Could not extend the budget (${res.status}).`);
  }
  return res.json();
}

export interface UsageRow {
  calls: number;
  input: number;
  output: number;
  /** Thinking tokens: a part of `output`, billed as output. */
  reasoning: number;
  /** Cached input tokens: a part of `input`, billed at a discount. */
  cached: number;
  tokens: number;
  share: number | null;
  cache_rate: number | null;
  thinking_rate: number | null;
  /** Estimated USD, or null when no price is configured for the model. */
  cost?: number | null;
  model?: string;
  label?: string;
}

/** Calls answered by a different model than the one the code asked for: "rerouted" (Google answering an older name with a newer
 * model) or "fallback" (the configured fallback answered because the primary failed). */
export interface UsageRoute {
  requested: string;
  served: string;
  kind: 'rerouted' | 'fallback';
  calls: number;
}

/** Averages for one kind of chat turn (backend/utils/llm_turns.py). Null when there were no turns of that kind. */
export interface TurnKindRow {
  turns: number;
  avg_calls: number | null;
  avg_input: number | null;
  avg_output: number | null;
  avg_reasoning: number | null;
}

/** How many model calls one chat turn makes, and how deep mode was switched on. Counts only. */
export interface TurnsSummary {
  turns: number;
  overall: TurnKindRow;
  plain: TurnKindRow;
  tool: TurnKindRow;
  /** Turns deep mode ran on; they are also counted in `tool`. */
  deep: TurnKindRow;
  histogram: { bucket: 'c1' | 'c2' | 'c3_4' | 'c5_8' | 'c9_plus'; turns: number }[];
  max_calls: number;
  /** What switched deep mode on: the user's setting, the audit-style wording of the message alone, or both. */
  deep_sources: { setting: number; audit: number; both: number };
  deep_share: number | null;
  /** Deep turns nobody asked for with the setting (older days only: the audit wording used to switch deep mode on). */
  deep_unasked: number;
  deep_suppressed: number;
  /** Turns whose wording was audit-style: a wider step budget on the normal model. */
  audit_wording_turns: number;
  /** Why turns went to the tool agent, biggest first: turns that had the reason, and turns that had it as their only reason. */
  routing: { reason: string; turns: number; alone: number }[];
  native_turns: number;
  blocked_turns: number;
  error_turns: number;
  /** Which tools the tool agent called, most-called first: calls, the turns that used it, and calls that came back as an error. */
  tools: { tool: string; calls: number; turns: number; errors: number }[];
  tool_calls_total: number;
  tool_errors_total: number;
}

/** One billing day for the Sonic Health chart. `cost` is null when no price is configured. */
export interface UsageDay {
  day: string;
  calls: number;
  input: number;
  output: number;
  tokens: number;
  cost: number | null;
  unpriced_tokens: number;
  turns: number;
  tool_turns: number;
  /** The part of `calls`, `tokens` and `cost` that came from a run of the evals (they are counted in the totals, so the app's own use is the difference). */
  eval_calls?: number;
  eval_tokens?: number;
  eval_cost?: number | null;
}

export interface UsageSummary {
  total: UsageRow;
  by_model: UsageRow[];
  by_label: UsageRow[];
  estimated_cost: number | null;
  unpriced_models: string[];
  priced: boolean;
  routes?: UsageRoute[];
  turns?: TurnsSummary;
  /** One row per billing day of the period, oldest first. */
  daily?: UsageDay[];
}

/** Feedback as the page counts it (counts only): see backend/utils/feedback_stats.py. */
export interface FeedbackSummary {
  period: {
    positive: number; negative: number; user_positive: number; user_negative: number; evaluator_negative: number;
    people: number; negative_share: number | null;
  };
  by_tag: { tag: string; count: number }[];
  daily: { day: string; positive: number; negative: number }[];
  /** Everything stored, which is what can still be shown to the model on a later question. */
  stored: {
    total: number; positive: number; negative: number; matchable: number; unmatchable: number; people: number;
    positives_old_rule_misread: number;
  };
  applied: { turns: number; share: number | null };
}

export interface GraphAffiliateRow {
  affiliate: string;
  documents: number;
  chunks: number;
  edges: number;
  documents_without_edges: number;
  missing: string[];
}

/** The knowledge graph retrieval reads, per knowledge base. */
export interface GraphSummary {
  edges_total: number;
  documents_total: number;
  documents_without_edges: number;
  by_affiliate: GraphAffiliateRow[];
}

/** One scheduled routine, its last run and when it is next due (backend/utils/routines.py). */
export interface RoutineRow {
  id: string;
  label: string;
  does: string;
  every_hours: number;
  uses_model: boolean;
  last: { status: 'ok' | 'attention' | 'failed' | 'skipped'; started_at: string; counts: Record<string, number>; result: string } | null;
  next_due_at: string | null;
}

export interface SonicHealth {
  days: number;
  generated_at: string;
  safety: SafetySummary;
  counters: CountersSummary;
  observations: ObservationView[];
  usage?: UsageSummary;
  budget?: BudgetView;
  /** null when that part could not be read. */
  feedback?: FeedbackSummary | null;
  graph?: GraphSummary | null;
  routines?: RoutineRow[] | null;
}

export interface ReflectionResult {
  proposed: string[];
  confirmed: string[];
  unsupported: string[];
}

async function adminRequest<T>(path: string, method: 'GET' | 'POST' | 'DELETE', body?: unknown): Promise<T> {
  const authHeaders = await getAuthHeaders();
  const res = await fetch(`${BASE_URL}${path}`, {
    method,
    headers: { ...authHeaders, ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}) },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (res.status === 403) throw new Error('Admins only.');
  if (!res.ok) {
    let detail = '';
    try { detail = (await res.json())?.detail ?? ''; } catch { /* no JSON body */ }
    throw new Error(typeof detail === 'string' && detail ? detail : `Request failed (${res.status}).`);
  }
  return res.json();
}

export function getSonicHealth(days: number): Promise<SonicHealth> {
  return adminRequest<SonicHealth>(`/api/admin/sonic-health?days=${encodeURIComponent(days)}`, 'GET');
}

export function approveObservation(key: string): Promise<{ observations: ObservationView[] }> {
  return adminRequest(`/api/admin/observations/${encodeURIComponent(key)}/approve`, 'POST');
}

export function retireObservation(key: string): Promise<{ observations: ObservationView[] }> {
  return adminRequest(`/api/admin/observations/${encodeURIComponent(key)}/retire`, 'POST');
}

export function runObservationReflection(days: number): Promise<{ result: ReflectionResult; observations: ObservationView[] }> {
  return adminRequest('/api/admin/observations/reflect', 'POST', { days });
}

export interface ShelfItem {
  key: string;
  url: string;
  domain: string;
  title: string;
  summary: string;
  topic: string;
  read_at: string;
  status: 'live' | 'removed';
  offered: number;
}

export interface ReadingShelf {
  items: ShelfItem[];
  blocked_domains: string[];
  last_attempt_at: string | null;
  refresh_days: number;
  expires_after_days: number;
  topics: string[];
  offered_total: number;
  /** Only on a "search now": what that one search did. */
  result?: { status: 'added' | 'nothing' | 'skipped' | 'error'; topic?: string; domain?: string; reasons?: Record<string, number> };
}

export function getReadingShelf(): Promise<ReadingShelf> {
  return adminRequest<ReadingShelf>('/api/admin/reading-shelf', 'GET');
}

export function removeShelfItem(key: string, blockSource: boolean): Promise<ReadingShelf> {
  return adminRequest(`/api/admin/reading-shelf/${encodeURIComponent(key)}/remove`, 'POST', { block_source: blockSource });
}

export function unblockShelfSource(domain: string): Promise<ReadingShelf> {
  return adminRequest(`/api/admin/reading-shelf/blocked/${encodeURIComponent(domain)}`, 'DELETE');
}

export function refreshReadingShelf(): Promise<ReadingShelf> {
  return adminRequest('/api/admin/reading-shelf/refresh', 'POST');
}

export interface OpsSuggestion {
  key: string;
  kind: 'recurring' | 'stale' | 'review';
  title: string;
  detail: string;
  /** A ready-made question for the chat box, or null when there is nothing to ask Sonic (the review happens on the page). */
  prompt: string | null;
}

export interface OpsSuggestions {
  suggestions: OpsSuggestion[];
  /** Whether errAgent could be read: "unavailable" means incident suggestions are missing, not that there are none. */
  incidents: 'ok' | 'not_configured' | 'unavailable';
  generated_at: string;
}

export function getOpsSuggestions(): Promise<OpsSuggestions> {
  return adminRequest<OpsSuggestions>('/api/admin/ops-suggestions', 'GET');
}

export function decideOpsSuggestion(key: string, decision: 'dismiss' | 'snooze'): Promise<{ hidden_until: string }> {
  return adminRequest(`/api/admin/ops-suggestions/${encodeURIComponent(key)}/${decision}`, 'POST');
}

/** Welcome-screen questions built from this person's own state (documents, integrations, repo) for one scope. */
export async function getExampleQuestions(affiliate: string): Promise<string[]> {
  return (await fetchExamplePayload(affiliate)).questions;
}

/** The labelled welcome cards. A server from before the cards existed sends only the plain list, which becomes unlabelled cards. */
export async function getExampleCards(affiliate: string): Promise<ExampleCard[]> {
  const { questions, cards } = await fetchExamplePayload(affiliate);
  return cards.length ? cards : questions.map(text => ({ text }));
}

export interface ExampleCard { text: string; label?: string; action?: 'attach'; prompt?: string }

async function fetchExamplePayload(affiliate: string): Promise<{ questions: string[]; cards: ExampleCard[] }> {
  const authHeaders = await getAuthHeaders();
  const res = await fetch(`${BASE_URL}/api/example-questions?affiliate=${encodeURIComponent(affiliate)}`, {
    headers: { ...authHeaders }
  });
  if (!res.ok) throw new Error("Failed to fetch example questions.");
  const data = await res.json();
  const questions: string[] = Array.isArray(data?.questions) ? data.questions.filter((q: unknown) => typeof q === "string") : [];
  const cards = Array.isArray(data?.cards)
    ? data.cards
        .filter((c: unknown): c is { text: string; label?: unknown; action?: unknown; prompt?: unknown } => !!c && typeof (c as { text?: unknown }).text === "string")
        .map((c: { text: string; label?: unknown; action?: unknown; prompt?: unknown }): ExampleCard => {
          const card: ExampleCard = { text: c.text };
          if (typeof c.label === "string" && c.label) card.label = c.label;
          if (c.action === "attach") {
            card.action = "attach";
            if (typeof c.prompt === "string" && c.prompt) card.prompt = c.prompt;
          }
          return card;
        })
    : [];
  return { questions, cards };
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
  source: "explicit" | "inferred" | "pattern" | "user_edit" | "safety_support";
  confidence: number;
  effective_confidence: number;
  created_at: string;
  updated_at: string;
  active: boolean;
  goal_status: string | null;
  last_nudged_at: string | null;
  /** The notes this one is bound to (about the same thing, like an employer and a project there). They are recalled together. */
  related_facts?: { id: string; fact: string }[];
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
 * Rewrite one saved note (and optionally its category). Resolves to the note as the list shows it; throws an Error carrying the server's
 * plain-language reason when the text can't be saved (empty, too long, couldn't be embedded).
 */
export async function editMemoryFact(factId: string, fact: string, category?: string): Promise<UserFact> {
  const authHeaders = await getAuthHeaders();
  const res = await fetch(`${BASE_URL}/api/memory/${encodeURIComponent(factId)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", ...authHeaders },
    body: JSON.stringify(category ? { fact, category } : { fact })
  });
  if (!res.ok) {
    let reason = "Failed to save that memory.";
    try {
      const body = await res.json();
      if (res.status === 400 && typeof body?.detail === "string") reason = body.detail;
    } catch { /* keep the generic message */ }
    throw new Error(reason);
  }
  return res.json();
}

/** Says two notes do not belong together: the link between them is removed (both ways). */
export async function deleteMemoryLink(factId: string, otherId: string): Promise<void> {
  const authHeaders = await getAuthHeaders();
  const res = await fetch(`${BASE_URL}/api/memory/${encodeURIComponent(factId)}/links/${encodeURIComponent(otherId)}`, {
    method: "DELETE",
    headers: { ...authHeaders }
  });
  if (!res.ok) {
    throw new Error("Failed to remove that link.");
  }
}

/**
 * Things the user said are coming up, which Sonic may ask about once afterwards
 */
export interface OpenLoop {
  id: string;
  text: string;
  due_date: string; // YYYY-MM-DD, the user's own calendar day
  kind: string;
  status: string;
}

export async function listOpenLoops(): Promise<OpenLoop[]> {
  const authHeaders = await getAuthHeaders();
  const res = await fetch(`${BASE_URL}/api/open-loops`, { headers: { ...authHeaders } });
  if (!res.ok) {
    throw new Error("Failed to load upcoming items.");
  }
  return res.json();
}

export async function deleteOpenLoop(loopId: string): Promise<any> {
  const authHeaders = await getAuthHeaders();
  const res = await fetch(`${BASE_URL}/api/open-loops/${encodeURIComponent(loopId)}`, {
    method: "DELETE",
    headers: { ...authHeaders }
  });
  if (!res.ok) {
    throw new Error("Failed to remove that upcoming item.");
  }
  return res.json();
}

/** What Sonic keeps besides the facts the Memory page lists: notes from past conversations, and archived copies of notes and facts. */
export interface MemoryStored {
  notes: number;
  archived: number;
}

export async function getMemoryStored(): Promise<MemoryStored> {
  const authHeaders = await getAuthHeaders();
  const res = await fetch(`${BASE_URL}/api/memory/stored`, {
    headers: { ...authHeaders }
  });
  if (!res.ok) {
    throw new Error("Failed to load what else Sonic keeps.");
  }
  return res.json();
}

/**
 * Clear everything Sonic remembers about the current user: facts, notes from past conversations, upcoming items and archived copies
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
  files: { path: string; content?: string; content_base64?: string }[];
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

/**
 * The real .docx for a Word document Sonic proposed. Asked for only when the person presses Apply; the browser then writes the bytes into their folder.
 * Throws an Error with the server's plain reason when the description is not valid.
 */
export async function renderLocalDocx(document: { title: string; blocks: unknown[] }): Promise<ArrayBuffer> {
  const authHeaders = await getAuthHeaders();
  const res = await fetch(`${BASE_URL}/api/local-edits/render-docx`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders },
    body: JSON.stringify({ document })
  });
  if (!res.ok) {
    let reason = "The Word document could not be built.";
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") reason = body.detail;
    } catch { /* keep the generic message */ }
    throw new Error(reason);
  }
  return res.arrayBuffer();
}

/**
 * An edited COPY of a Word document: the document's current bytes (base64) and the edits Sonic proposed go to the server, which edits, checks that nothing but the
 * named paragraphs changed, and returns the copy's bytes. Throws an Error with the server's plain reason when it can't.
 */
export async function editLocalDocx(docxBase64: string, wordEdits: unknown[]): Promise<ArrayBuffer> {
  const authHeaders = await getAuthHeaders();
  const res = await fetch(`${BASE_URL}/api/local-edits/edit-docx`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders },
    body: JSON.stringify({ docx_base64: docxBase64, word_edits: wordEdits })
  });
  if (!res.ok) {
    let reason = "The document could not be edited.";
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") reason = body.detail;
    } catch { /* keep the generic message */ }
    throw new Error(reason);
  }
  return res.arrayBuffer();
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
  // A deliberate Stop surfaces as an AbortError and is the caller's to handle; every other failure is a ChatSendError that says what is
  // actually known (see chatErrors.ts), so the page never has to guess a cause.
  const isStop = (err: unknown) => err instanceof DOMException && err.name === 'AbortError';
  let response: Response;
  try {
    response = await fetch(`${BASE_URL}/api/chat`, {
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
  } catch (err) {
    throw isStop(err) ? err : toChatSendError(err);
  }

  if (!response.ok) {
    let body: unknown;
    try { body = await response.json(); } catch { /* not JSON (a proxy's error page, say) */ }
    throw new ChatSendError(kindForStatus(response.status), { status: response.status, detail: safeDetail(response.status, body) });
  }

  const reader = response.body?.getReader();
  const decoder = new TextDecoder();

  if (!reader) {
    throw new ChatSendError('unknown', { detail: 'the browser cannot read a streamed response' });
  }

  let buffer = "";

  while (true) {
    let chunk: ReadableStreamReadResult<Uint8Array>;
    try {
      chunk = await reader.read();
    } catch (err) {
      throw isStop(err) ? err : new ChatSendError('interrupted');
    }
    const { value, done } = chunk;
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
  editMemoryFact,
  deleteMemoryLink,
  clearMemoryFacts,
  getMemoryStored,
  listOpenLoops,
  deleteOpenLoop,
  getRagMode,
  updateRagMode,
  getDeepThinking,
  updateDeepThinking,
  getProactiveOpening,
  updateProactiveOpening,
  getOpsDigest,
  updateOpsDigest,
  getBudget,
  extendBudget,
  verifySession,
  requestConversationOpening,
  getTargetRepo,
  updateTargetRepo,
  getTargetDoc,
  updateTargetDoc,
  getHasSeenHelp,
  updateHasSeenHelp,
  getProfile,
  updateProfile,
  getTimezone,
  updateTimezone,
  getCalendarStatus,
  startCalendarConnection,
  disconnectCalendar,
  getSonicHealth,
  approveObservation,
  retireObservation,
  runObservationReflection,
  getReadingShelf,
  removeShelfItem,
  unblockShelfSource,
  refreshReadingShelf,
  getOpsSuggestions,
  decideOpsSuggestion
};
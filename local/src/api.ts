// src/api.ts
import type { SessionCheck } from './utils/authReady';
import { ChatSendError, kindForStatus, safeDetail, toChatSendError } from './chatErrors';
import { SyncRequestError } from './localWorkspaceCore';

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
 * Which model does the tool agent's reasoning steps (reading code, searching, writing the code in its answer). The reply itself is written by Gemini either way,
 * so "claude" is the hybrid. `available` is false when Claude isn't set up on the server or for guests, and then the setting isn't offered.
 */
export type ToolModel = 'gemini' | 'claude';
export interface ToolModelSetting {
  tool_model: ToolModel;
  /** The person's own choice, or null when they never chose (the server's default applies). */
  choice: ToolModel | null;
  default: ToolModel;
  available: boolean;
}

export async function getToolModel(): Promise<ToolModelSetting> {
  const authHeaders = await getAuthHeaders();
  const res = await fetch(`${BASE_URL}/api/settings/tool-model`, { headers: { ...authHeaders } });
  if (!res.ok) throw new Error("Failed to fetch the code model setting.");
  return res.json();
}

export async function updateToolModel(toolModel: ToolModel): Promise<ToolModelSetting> {
  const authHeaders = await getAuthHeaders();
  const res = await fetch(`${BASE_URL}/api/settings/tool-model`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", ...authHeaders },
    body: JSON.stringify({ tool_model: toolModel })
  });
  if (!res.ok) throw new Error("Failed to update the code model setting.");
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
  style: { length: string; tone: string; humor: string; questions?: string };
  avoid: string[];
  offers: boolean;
  /** How often Sonic may bring something up on its own, from none to more (backend/utils/reach.py). `offers` is kept in step with it. */
  reach?: 'none' | 'low' | 'default' | 'more';
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
  /** Everyone with a limit of their own: how far along that limit is (0 to over 1), which one it is, and the level it is at. A percentage only: never an amount, never the shared budget. */
  own_fraction?: number;
  own_scope?: 'my_day' | 'my_five';
  /** When that own limit starts over (an ISO time). */
  own_resets_at?: string;
  own_level?: 'ok' | 'notice' | 'throttled' | 'blocked';
  /** Admins only: the shared budget has paused chat and +25% is on offer. */
  can_extend?: boolean;
  /** Admins only: how far the block line has already been raised (0.25 = +25%). */
  extended_by?: number;
  /** A person with a limit of their own who has reached the notice line may ask an admin for +25% on it. */
  can_request_increase?: boolean;
  /** Their own limit is currently raised by this much (0.25 = +25%) until the window or day ends. */
  own_extended_by?: number;
  /** Where their request for more stands: waiting for an admin, or declined for this period. */
  increase_request?: 'pending' | 'declined' | null;
}

export interface BudgetIncreaseRequest {
  id: string;
  user: string;
  scope: 'my_day' | 'my_five';
  status: 'pending' | 'approved' | 'declined';
  requested_at: string;
  until: string;
  unit?: 'usd' | 'tokens';
  spent?: number;
  limit?: number;
  fraction?: number;
  decided_by?: string;
  decided_at?: string;
  extra?: number;
}

export interface BudgetRequests {
  pending: BudgetIncreaseRequest[];
  recent: BudgetIncreaseRequest[];
}

/** Asks the admins to raise this person's own limit by 25% for the window or day that is furthest along. Returns the new budget view. */
export async function requestBudgetIncrease(): Promise<BudgetView> {
  const authHeaders = await getAuthHeaders();
  const res = await fetch(`${BASE_URL}/api/budget/request-increase`, { method: "POST", headers: { ...authHeaders } });
  if (!res.ok) {
    let detail = '';
    try { detail = (await res.json())?.detail ?? ''; } catch { /* no JSON body */ }
    throw new Error(typeof detail === 'string' && detail ? detail : `Could not send the request (${res.status}).`);
  }
  return res.json();
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
  /** `no_tools` / `alone_no_tools`: of those turns (and of the ones with this as their only reason), how many called no tool at all. */
  routing: { reason: string; turns: number; alone: number; no_tools?: number; alone_no_tools?: number }[];
  native_turns: number;
  /** How the tool agent's step loop went: turns that ran it, steps used on average, how they spread, and how many ran into the last step they were allowed (so the step budget may be too tight). */
  loop?: {
    turns: number;
    avg_steps: number | null;
    histogram: { bucket: string; turns: number }[];
    cap_hits: number;
    cap_hit_share: number | null;
    /** How tool turns ended: by the halfway step, later, or at the last step with a draft kept / with nothing written. */
    exits?: { early: number; late: number; cap_draft: number; cap_no_draft: number };
    /** Turns that reached a checkpoint (long budgets only), and whether they answered there or chose to keep working. */
    checkpoints?: { turns: number; answered: number; continued: number; acted?: number };
    /** Turns where the halfway nudge was shown, and how many of those ended up with a draft. */
    nudged?: number;
    nudged_drafted?: number;
    deep_turns: number;
    deep_cap_hits: number;
    deep_cap_hit_share: number | null;
  };
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

/** One account's cost over the period, for the Sonic Health "Cost by person" table (counts and dollars only): see backend/utils/user_usage.py. */
export interface UserCostRow {
  user: string;
  /** "evals" is the runs of the evals and tests, listed beside the people but not a person; they have no turns. */
  kind?: 'person' | 'evals';
  cost: number;
  tokens: number;
  turns: number;
  calls: number;
  cost_per_turn: number | null;
  cost_per_active_day: number | null;
  active_days: number;
  share: number | null;
  /** One figure per billing day of the period, oldest first. */
  per_day: number[];
}

export interface UsersSummary {
  days: string[];
  people: number;
  /** False when no dollars were counted at all (no prices set): the page shows tokens instead. */
  priced: boolean;
  cost: number;
  tokens: number;
  rows: UserCostRow[];
  /** The accounts below the biggest ones shown. */
  others: { people: number; cost: number; tokens: number } | null;
  /** What the evals and tests cost in the period (also listed as a row), or null when none ran. */
  evals?: { cost: number; tokens: number; calls: number } | null;
  /** What the usage page estimates for the whole period, minus what is tied to accounts and to the evals: background jobs and anything with no account. */
  unattributed_cost?: number | null;
  /** For every day: who used it and what each cost against the day's whole total (the dashboard graph and the Usage table); null when no dollars were counted. */
  daily_people?: UsersDays | null;
}

export interface DayPerson {
  user: string;
  /** True for the admin looking at the page. */
  you: boolean;
  cost: number;
  tokens: number;
  calls: number;
  turns: number;
  /** Their share of the whole day's cost, or null when the day cost nothing. */
  share: number | null;
}

export interface DayBreakdown {
  day: string;
  /** The whole day's estimated cost (the usage page's own figure). */
  total: number;
  /** Everyone who used the day, biggest first (at most 60). */
  people: DayPerson[];
  more_people: number;
  /** The runs of the evals and tests that day; null when there were none. */
  evals: { cost: number; tokens: number; calls: number; share: number | null } | null;
  /** What no account owns (background jobs): the rest of the day's total; null when there is none. */
  background: { cost: number; share: number | null } | null;
}

export interface UsersDays {
  days: string[];
  /** The viewing admin's account id, or null. */
  viewer: string | null;
  by_day: DayBreakdown[];
}

/** One of Sonic's goals, how it is going, and whether its lever is approved and acting (backend/utils/goals.py). Counts only. */
export interface GoalView {
  id: string;
  title: string;
  statement: string;
  signal: string;
  /** null for a goal that is only measured: it has no lever, so nothing to approve. */
  lever: string | null;
  guardrail: string | null;
  measure_only: boolean;
  rate_label: string;
  sample_label: string;
  state: 'on_track' | 'drifting' | 'unproven' | 'not_enough_data';
  numbers: {
    taken: number;
    passed: number;
    sample: number;
    rate: number | null;
    previous_sample: number;
    previous_rate: number | null;
    made: number;
    held_back: number;
    people: { measured: number; stretched: number; any: number };
    /** Exact, admin-only sentences about the week's counts. */
    details: string[];
    /** Why a good-looking share is not being called on track (the numbers behind it cannot tell), in words. Empty when nothing is in the way. */
    unproven?: string[];
  };
  target_rate: number;
  min_sample: number;
  approval: { approved: boolean; by: string | null; at: string | null; stale: boolean };
  guardrail_tripped: boolean;
  lever_applies: boolean;
  revision: number;
}

/** What a "Source: Memory" citation opens: the notes that matched and the saved facts that relate (backend/utils/memory_sources.py). */
export interface MemorySources {
  notes: Array<{ text: string; when: string | null }>;
  facts: Array<{ id: string; text: string; category: string }>;
}

export interface SonicHealth {
  days: number;
  generated_at: string;
  safety: SafetySummary;
  counters: CountersSummary;
  observations: ObservationView[];
  usage?: UsageSummary;
  /** What each account cost; null when it could not be read. */
  users?: UsersSummary | null;
  budget?: BudgetView;
  /** null when that part could not be read. */
  feedback?: FeedbackSummary | null;
  graph?: GraphSummary | null;
  routines?: RoutineRow[] | null;
  /** Sonic's goals; null when they could not be read. */
  goals?: GoalView[] | null;
}

export interface ReflectionResult {
  proposed: string[];
  confirmed: string[];
  unsupported: string[];
}

async function adminRequest<T>(path: string, method: 'GET' | 'POST' | 'PUT' | 'DELETE', body?: unknown): Promise<T> {
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

export function getBudgetRequests(): Promise<BudgetRequests> {
  return adminRequest<BudgetRequests>('/api/admin/budget/requests', 'GET');
}

export function decideBudgetRequest(id: string, decision: 'approve' | 'decline'): Promise<BudgetIncreaseRequest> {
  return adminRequest<BudgetIncreaseRequest>(`/api/admin/budget/requests/${encodeURIComponent(id)}/${decision}`, 'POST');
}

/** What is using the server's memory (backend/utils/server_memory.py). Every field can be null where the server cannot tell (a developer's machine). */
export interface ServerMemory {
  container: { limit_mb: number | null; used_mb: number | null; anon_mb: number | null; file_cache_mb: number | null; inactive_cache_mb: number | null; working_set_mb: number | null };
  process: { rss_mb: number | null; peak_rss_mb: number | null };
  held: { conversations_in_memory: number; messages_in_memory: number; folder_people: number; folder_files: number; folder_mb: number | null; temp_filesystem: string | null; temp_is_memory: boolean | null };
  sandbox: { headroom_now_mb: number | null; headroom_without_cache_mb: number | null; need_warm_mb: number; need_cold_mb: number; compiled_cache_present: boolean; would_run_now: boolean | null; would_run_without_cache: boolean | null };
  reclaimable_share_of_used: number | null;
}

export function getServerMemory(): Promise<ServerMemory> {
  return adminRequest<ServerMemory>('/api/admin/server-memory', 'GET');
}

export function getSonicHealth(days: number): Promise<SonicHealth> {
  return adminRequest<SonicHealth>(`/api/admin/sonic-health?days=${encodeURIComponent(days)}`, 'GET');
}

/** Approves (or withdraws approval of) the current revision of one of Sonic's goals. Until approved, a goal is only measured. */
export function setGoalApproval(goalId: string, approved: boolean): Promise<{ goal: string; approval: GoalView['approval'] }> {
  return adminRequest(`/api/admin/goals/${encodeURIComponent(goalId)}/approval`, 'PUT', { approved });
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
  /** What a system like Sonic takes from the piece: pending until an admin approves it, and only an approved one is ever said. */
  reflection?: { status: '' | 'pending' | 'approved' | 'none' | 'removed'; text: string };
}

export interface ReadingShelf {
  items: ShelfItem[];
  blocked_domains: string[];
  last_attempt_at: string | null;
  refresh_days: number;
  expires_after_days: number;
  topics: string[];
  offered_total: number;
  reflections_pending?: number;
  reflections_missing?: number;
  /** Only on a "search now": what that one search did. */
  result?: { status: 'added' | 'nothing' | 'skipped' | 'error'; topic?: string; domain?: string; reasons?: Record<string, number> };
}

/** What any signed-in person may see of Sonic's reading shelf: live items only, no admin fields. */
export interface PublicShelfItem {
  url: string;
  domain: string;
  title: string;
  summary: string;
  topic: string;
  read_at: string;
  /** An admin-approved takeaway, or an empty string. */
  reflection?: string;
}

export const getPublicReadingShelf = (): Promise<{ items: PublicShelfItem[]; topics: string[] }> =>
  taskRequest('/api/reading-shelf', {}, 'Failed to load what Sonic has been reading.');

export function getReadingShelf(): Promise<ReadingShelf> {
  return adminRequest<ReadingShelf>('/api/admin/reading-shelf', 'GET');
}

export function removeShelfItem(key: string, blockSource: boolean): Promise<ReadingShelf> {
  return adminRequest(`/api/admin/reading-shelf/${encodeURIComponent(key)}/remove`, 'POST', { block_source: blockSource });
}

export function unblockShelfSource(domain: string): Promise<ReadingShelf> {
  return adminRequest(`/api/admin/reading-shelf/blocked/${encodeURIComponent(domain)}`, 'DELETE');
}

export function writeShelfReflections(): Promise<ReadingShelf> {
  return adminRequest('/api/admin/reading-shelf/reflect', 'POST');
}

export function decideShelfReflection(key: string, action: 'approve' | 'edit' | 'remove', text?: string): Promise<ReadingShelf> {
  return adminRequest(`/api/admin/reading-shelf/${encodeURIComponent(key)}/reflection`, 'POST', { action, text });
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

export async function getMicrosoftStatus(): Promise<CalendarConnectionStatus> {
  const authHeaders = await getAuthHeaders();
  const res = await fetch(`${BASE_URL}/api/microsoft/status`, { headers: { ...authHeaders } });
  if (!res.ok) throw new Error("Failed to fetch Microsoft connection status.");
  return res.json();
}

export async function startMicrosoftConnection(returnTo: string): Promise<{ authorization_url: string }> {
  const authHeaders = await getAuthHeaders();
  const res = await fetch(`${BASE_URL}/api/microsoft/connect/start?return_to=${encodeURIComponent(returnTo)}`, {
    method: "POST",
    headers: { ...authHeaders }
  });
  if (res.status === 503) throw new Error("Microsoft isn't set up on this server yet.");
  if (!res.ok) throw new Error("Failed to start Microsoft connection.");
  return res.json();
}

export async function disconnectMicrosoft(): Promise<{ connected: boolean }> {
  const authHeaders = await getAuthHeaders();
  const res = await fetch(`${BASE_URL}/api/microsoft/disconnect`, {
    method: "POST",
    headers: { ...authHeaders }
  });
  if (!res.ok) throw new Error("Failed to disconnect Microsoft.");
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
  /** Which part of their life the note is about; null for one Sonic has not sorted yet. */
  domain?: MemoryDomain | null;
  /** The person set the domain themselves, so Sonic never changes it. */
  domain_locked?: boolean;
  /** Names this note shares with linked notes that are written two ways ("Ashlyn" / "Ashlynn"): Sonic treats them as one person and writes `shown_as`, unless the person says otherwise. */
  name_variants?: { names: string[]; shown_as: string }[];
}

export type MemoryDomain = "work" | "own" | "personal" | "general";

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

/** Files a note under another part of the person's life (work, their own projects, personal, general). Only the label changes: the wording stays as it is. */
export async function setMemoryDomain(factId: string, domain: MemoryDomain): Promise<UserFact> {
  const authHeaders = await getAuthHeaders();
  const res = await fetch(`${BASE_URL}/api/memory/${encodeURIComponent(factId)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", ...authHeaders },
    body: JSON.stringify({ domain })
  });
  if (!res.ok) {
    let reason = "Failed to change that.";
    try {
      const body = await res.json();
      if (res.status === 400 && typeof body?.detail === "string") reason = body.detail;
    } catch { /* keep the generic message */ }
    throw new Error(reason);
  }
  return res.json();
}

/** Says two spellings of a name are different people (or, with `different` false, takes that back): Sonic then never treats them as one. */
export async function setNameAlias(a: string, b: string, different = true): Promise<void> {
  const authHeaders = await getAuthHeaders();
  const res = await fetch(`${BASE_URL}/api/memory/name-aliases`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders },
    body: JSON.stringify({ a, b, different })
  });
  if (!res.ok) {
    let reason = "Failed to save that.";
    try {
      const body = await res.json();
      if (res.status === 400 && typeof body?.detail === "string") reason = body.detail;
    } catch { /* keep the generic message */ }
    throw new Error(reason);
  }
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
/** One detail of something coming up, with where Sonic got it. A detail that was never said is absent, which the page shows as "not known". */
export interface OpenLoopField {
  value: string | null;
  source: 'user' | 'assistant_confirmed' | 'calendar' | 'user_edit';
  /** How the detail is said ("7 PM (they said 7; PM assumed for a social plan)"). */
  display: string;
}

export interface OpenLoop {
  id: string;
  text: string;
  due_date: string; // YYYY-MM-DD, the user's own calendar day
  kind: string;
  status: string;
  fields?: { time?: OpenLoopField; with?: OpenLoopField; where?: OpenLoopField };
  corrected_at?: string | null;
  confirmed_at?: string | null;
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

/** What the person may change about something coming up. An empty string for `time`, `with` or `where` clears it back to "not known"; `status` is "cancelled" or "open" (restore). */
export interface OpenLoopChanges {
  time?: string;
  with?: string;
  where?: string;
  due_date?: string;
  status?: 'cancelled' | 'open';
  confirm?: boolean;
}

/** Corrects something coming up. The server says why when it cannot keep a value (a street address, a link, "7" with no AM or PM, a day out of range). */
export async function updateOpenLoop(loopId: string, changes: OpenLoopChanges): Promise<OpenLoop> {
  const authHeaders = await getAuthHeaders();
  const res = await fetch(`${BASE_URL}/api/open-loops/${encodeURIComponent(loopId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json", ...authHeaders },
    body: JSON.stringify(changes)
  });
  if (!res.ok) {
    let reason = "Failed to save that change.";
    try {
      const body = await res.json();
      if (res.status === 400 && typeof body?.detail === "string") reason = body.detail;
    } catch { /* keep the generic message */ }
    throw new Error(reason);
  }
  return res.json();
}

export type TaskKind = 'research' | 'pr_review' | 'code_audit';

/** A task the person set up for Sonic to run on a schedule (a web search, or a read-only look at their own repository), and what it has found. */
export interface UserTask {
  id: string;
  kind: TaskKind;
  repo: string;
  title: string;
  topic: string;
  /** More searches this one task runs in the same run (up to 3), e.g. other job titles. */
  extra_topics: string[];
  details: string;
  cadence: 'daily' | 'weekly';
  hour: number;
  weekday: number | null;
  /** 0 = at the time chosen; 2 or 3 = sometime in that many hours from it, at a quiet moment. */
  window_hours: number;
  sources: string[];
  location: string;
  schedule: string;
  status: 'active' | 'paused';
  paused_because: string | null;
  next_run_at: string | null;
  last_run_at: string | null;
  last_status: string | null;
  waiting: number;
}

export interface TaskSpec {
  kind?: TaskKind;
  repo?: string;
  title: string;
  topic: string;
  extra_topics?: string[];
  details: string;
  cadence: 'daily' | 'weekly';
  hour: number;
  weekday: number;
  window_hours?: number;
  sources: string[];
  location: string;
}

export interface TaskResultItem {
  title: string;
  url: string;
  domain: string;
  note: string;
}

export interface TaskRun {
  id: string;
  task_id: string;
  status: 'ok' | 'nothing_new' | 'failed';
  trigger: 'scheduled' | 'manual';
  started_at: string | null;
  headline: string;
  items: TaskResultItem[];
  seen: boolean;
  /** Why the run landed when it did, written by the server from what it checked ("Ran at its scheduled time.", "Waited for you to be away for a while, ..."). */
  reason?: string;
  window?: { starts_at: string; ends_at: string } | null;
}

export interface TasksResponse {
  tasks: UserTask[];
  limits: {
    max_tasks: number; runs_per_day: number; runs_today: number; sources: string[]; source_labels: Record<string, string>;
    kinds?: TaskKind[]; kind_labels?: Record<string, string>; github_connected?: boolean;
  };
}

async function taskRequest<T>(path: string, init: RequestInit, fallback: string): Promise<T> {
  const authHeaders = await getAuthHeaders();
  const res = await fetch(`${BASE_URL}${path}`, {
    ...init,
    headers: { ...(init.body ? { 'Content-Type': 'application/json' } : {}), ...authHeaders },
  });
  if (!res.ok) {
    throw new Error(await readErrorDetail(res, fallback));
  }
  return res.json();
}

export const listTasks = (): Promise<TasksResponse> => taskRequest('/api/tasks', {}, 'Failed to load your tasks.');
export const createTask = (spec: TaskSpec): Promise<UserTask> =>
  taskRequest('/api/tasks', { method: 'POST', body: JSON.stringify(spec) }, 'Failed to create that task.');
export const updateTask = (id: string, changes: Partial<TaskSpec> & { status?: 'active' | 'paused' }): Promise<UserTask> =>
  taskRequest(`/api/tasks/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify(changes) }, 'Failed to update that task.');
export const deleteTask = (id: string): Promise<{ status: string }> =>
  taskRequest(`/api/tasks/${encodeURIComponent(id)}`, { method: 'DELETE' }, 'Failed to delete that task.');
export const listTaskRuns = (id: string): Promise<{ runs: TaskRun[] }> =>
  taskRequest(`/api/tasks/${encodeURIComponent(id)}/runs`, {}, 'Failed to load what that task found.');
export const markTaskSeen = (id: string): Promise<{ status: string }> =>
  taskRequest(`/api/tasks/${encodeURIComponent(id)}/seen`, { method: 'POST' }, 'Failed to update that task.');
export const runTaskNow = (id: string): Promise<{ status: string }> =>
  taskRequest(`/api/tasks/${encodeURIComponent(id)}/run`, { method: 'POST' }, 'Failed to start that task.');

/** A standing meeting the person has with Sonic (a weekly check-in), run as an agenda inside a chat. */
export interface Meeting {
  id: string;
  name: string;
  match: string;
  purpose: string;
  agenda: string[];
  cadence: 'none' | 'daily' | 'weekly';
  /** How firmly Sonic may push in this meeting; supportive is the original behaviour. */
  coaching: 'supportive' | 'balanced' | 'challenging';
  weekday: number | null;
  hour: number | null;
  minute: number | null;
  schedule: string;
  next_at: string | null;
  due_today: boolean;
  notes: number;
  last_held: string | null;
}

export interface MeetingSpec {
  name: string;
  match: string;
  purpose: string;
  agenda: string[];
  coaching?: 'supportive' | 'balanced' | 'challenging';
  cadence: 'none' | 'daily' | 'weekly';
  weekday: number;
  time: string; // HH:MM, used when there is a schedule
}

export interface MeetingNote {
  id: string;
  meeting_id: string;
  held_at: string | null;
  summary: string;
  decisions: string[];
  action_items: string[];
  automatic: boolean;
}

export interface MeetingsState {
  meetings: { id: string; name: string; due_today: boolean; schedule: string }[];
  active: { run_id: string; meeting_id: string; name: string } | null;
}

/** What the browser needs to bring a commit Sonic made on GitHub into the person's own repository (backend/services/git_import.py). */
export const getLocalGitBundle = (repo: string, commit: string, compareWith?: string | null): Promise<import('./localGit/importCommit').CommitBundle> =>
  taskRequest('/api/local-git/bundle', { method: 'POST', body: JSON.stringify({ repo, commit, compare_with: compareWith ?? null }) }, 'Failed to read that commit from GitHub.');

/** One line of a conversation's working notes. `by` says who wrote it; what the person wrote or edited is never changed by Sonic. */
export interface WorkingNoteItem { id: string; text: string; evidence: string; by: 'model' | 'person'; }
export interface WorkingNotes {
  goal: WorkingNoteItem | null;
  sections: { kind: string; label: string; items: WorkingNoteItem[] }[];
  empty: boolean;
}

export const getWorkingNotes = (sessionId: string): Promise<WorkingNotes> =>
  taskRequest(`/api/working-notes?session_id=${encodeURIComponent(sessionId)}`, {}, 'Failed to load the working notes.');
/** Edit one item (text), delete it (text null), or add one (itemId "new" with a kind). Returns the notes as they are now. */
export const editWorkingNote = (sessionId: string, itemId: string, text: string | null, kind?: string): Promise<WorkingNotes> =>
  taskRequest(`/api/working-notes?session_id=${encodeURIComponent(sessionId)}`, { method: 'PATCH', body: JSON.stringify({ item_id: itemId, text, kind }) }, 'Failed to change that note.');
export const clearWorkingNotes = (sessionId: string): Promise<{ status: string }> =>
  taskRequest(`/api/working-notes?session_id=${encodeURIComponent(sessionId)}`, { method: 'DELETE' }, 'Failed to clear the working notes.');

/** Plan mode (backend/utils/code_plan.py): a plan the person approves before Sonic edits their folder. */
export interface CodePlanStep { id: string; text: string; files: string[]; status: 'todo' | 'done'; }
export interface CodePlan {
  state: 'proposed' | 'approved' | 'done';
  goal: string;
  files: { path: string; why: string; new?: boolean }[];
  steps: CodePlanStep[];
  tests: string[];
  unknowns: string[];
  revision: number;
}
export const getPlanMode = (): Promise<{ plan_mode: boolean }> => taskRequest('/api/settings/plan-mode', {}, 'Failed to load the plan setting.');
export const updatePlanMode = (planMode: boolean): Promise<{ plan_mode: boolean }> =>
  taskRequest('/api/settings/plan-mode', { method: 'PUT', body: JSON.stringify({ plan_mode: planMode }) }, 'Failed to update the plan setting.');
export const getCodePlan = (sessionId: string): Promise<{ plan: CodePlan | null }> =>
  taskRequest(`/api/code-plan?session_id=${encodeURIComponent(sessionId)}`, {}, 'Failed to load the plan.');
export const actOnCodePlan = (sessionId: string, action: 'approve' | 'cancel' | 'edit_step' | 'remove_step', stepId?: string, text?: string): Promise<{ plan: CodePlan | null }> =>
  taskRequest(`/api/code-plan?session_id=${encodeURIComponent(sessionId)}`, { method: 'PATCH', body: JSON.stringify({ action, step_id: stepId, text }) }, 'Failed to change the plan.');

export const listMeetings = (): Promise<{ meetings: Meeting[]; limits: { max_meetings: number } }> => taskRequest('/api/meetings', {}, 'Failed to load your meetings.');
export const getMeetingsState = (sessionId: string): Promise<MeetingsState> =>
  taskRequest(`/api/meetings/state?session_id=${encodeURIComponent(sessionId)}`, {}, 'Failed to load your meetings.');
export const createMeeting = (spec: MeetingSpec): Promise<Meeting> =>
  taskRequest('/api/meetings', { method: 'POST', body: JSON.stringify(spec) }, 'Failed to create that meeting.');
export const updateMeeting = (id: string, changes: Partial<MeetingSpec>): Promise<Meeting> =>
  taskRequest(`/api/meetings/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify(changes) }, 'Failed to update that meeting.');
export const deleteMeeting = (id: string): Promise<{ status: string }> =>
  taskRequest(`/api/meetings/${encodeURIComponent(id)}`, { method: 'DELETE' }, 'Failed to delete that meeting.');
export const listMeetingNotes = (id: string): Promise<{ notes: MeetingNote[] }> =>
  taskRequest(`/api/meetings/${encodeURIComponent(id)}/notes`, {}, 'Failed to load the notes.');
export const updateMeetingNote = (noteId: string, changes: Partial<Pick<MeetingNote, 'summary' | 'decisions' | 'action_items'>>): Promise<MeetingNote> =>
  taskRequest(`/api/meetings/notes/${encodeURIComponent(noteId)}`, { method: 'PATCH', body: JSON.stringify(changes) }, 'Failed to update that note.');
export const deleteMeetingNote = (noteId: string): Promise<{ status: string }> =>
  taskRequest(`/api/meetings/notes/${encodeURIComponent(noteId)}`, { method: 'DELETE' }, 'Failed to delete that note.');
export const startMeeting = (id: string, sessionId: string): Promise<{ run_id: string; meeting_id: string }> =>
  taskRequest(`/api/meetings/${encodeURIComponent(id)}/start`, { method: 'POST', body: JSON.stringify({ session_id: sessionId }) }, 'Failed to start the meeting.');
export const endMeeting = (runId: string): Promise<{ status: string; note: MeetingNote | null; reason: string | null }> =>
  taskRequest(`/api/meetings/runs/${encodeURIComponent(runId)}/end`, { method: 'POST' }, 'Failed to end the meeting.');

/** What the person logs between office hours (backend/utils/progress_notes.py): Sonic lists the ones no meeting has covered yet when office hours starts. */
export type ProgressLabel = 'note' | 'win' | 'blocker';
export interface ProgressNote {
  id: string;
  meeting_id: string | null;
  text: string;
  label: ProgressLabel;
  day: string;
  at: string;
  source: 'chat' | 'page';
  /** True once that meeting's notes covered it. */
  covered: boolean;
}

/** Progress notes belong to one meeting each: these list and add to a single meeting's. */
export const listProgressNotes = (meetingId: string): Promise<{ notes: ProgressNote[]; limits: { max_text: number; max_per_day: number } }> =>
  taskRequest(`/api/meetings/${encodeURIComponent(meetingId)}/progress-notes`, {}, 'Failed to load your progress notes.');
export const addProgressNote = (meetingId: string, text: string, label: ProgressLabel): Promise<ProgressNote> =>
  taskRequest(`/api/meetings/${encodeURIComponent(meetingId)}/progress-notes`, { method: 'POST', body: JSON.stringify({ text, label }) }, 'Failed to save that note.');
export const updateProgressNote = (id: string, changes: Partial<Pick<ProgressNote, 'text' | 'label'>>): Promise<ProgressNote> =>
  taskRequest(`/api/progress-notes/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify(changes) }, 'Failed to update that note.');
export const deleteProgressNote = (id: string): Promise<{ status: string }> =>
  taskRequest(`/api/progress-notes/${encodeURIComponent(id)}`, { method: 'DELETE' }, 'Failed to delete that note.');

/** The person's three switches for calendar heads-ups (backend/utils/calendar_headsups.py), and what Sonic would mention. */
export interface HeadsUpSettings {
  enabled: boolean;
  include_health: boolean;
  discreet: boolean;
}

export interface HeadsUpItem {
  title: string;
  when: string;
  kind: string;
}

export const getCalendarHeadsUps = (): Promise<{ settings: HeadsUpSettings; connected: boolean }> => taskRequest('/api/calendar-headsups', {}, 'Failed to load these settings.');
export const putCalendarHeadsUps = (changes: Partial<HeadsUpSettings>): Promise<{ settings: HeadsUpSettings }> =>
  taskRequest('/api/calendar-headsups', { method: 'PUT', body: JSON.stringify(changes) }, 'Failed to save that.');
export const previewCalendarHeadsUps = (): Promise<{ items: HeadsUpItem[] }> => taskRequest('/api/calendar-headsups/preview', {}, 'Failed to look at your calendar.');

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

/** One batch of a folder upload. A FULL upload carries an `upload_id` (the server stages it and swaps it in on `commit`, so a failed upload never damages the working copy);
 *  a small delta of what changed has neither. A failed request throws a SyncRequestError carrying the HTTP status (none when the network dropped), which decides whether to retry. */
export async function syncLocalWorkspaceBatch(batch: {
  name: string;
  reset: boolean;
  files: { path: string; content?: string; text_b64?: string; content_base64?: string }[];
  deleted: string[];
  upload_id?: string;
  commit?: boolean;
}): Promise<LocalWorkspaceSyncResult> {
  const authHeaders = await getAuthHeaders();
  const step = batch.commit ? 'finishing' : `sending ${batch.files.length} file(s)`;
  let res: Response;
  try {
    res = await fetch(`${BASE_URL}/api/local-workspace/sync`, {
      method: "POST",
      headers: { ...authHeaders },
      body: JSON.stringify(batch)
    });
  } catch {
    // fetch rejects when the connection drops, the server is down or restarting, or the request is blocked: no answer at all, so no status.
    throw new SyncRequestError(`The browser got no usable answer while ${step} the folder upload (the network dropped, or something in front of the server refused it).`);
  }
  if (!res.ok) {
    const message = res.status === 403 ? "Sign in to connect a local folder."
      : res.status === 409 ? "The upload was replaced by a newer one."
      : res.status === 413 ? "The folder upload was too large for the server."
      : `The server refused the folder upload (HTTP ${res.status}) while ${step}.`;
    throw new SyncRequestError(message, res.status);
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

/** Tells the waiting turn what the person decided on a proposed folder change. Best effort: nothing breaks if no turn is waiting any more. */
export async function reportEditDecision(proposalId: string, outcome: 'applied' | 'declined' | 'undone', reason = ''): Promise<void> {
  try {
    const authHeaders = await getAuthHeaders();
    await fetch(`${BASE_URL}/api/local-edits/${encodeURIComponent(proposalId)}/decision`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders },
      body: JSON.stringify({ outcome, reason }),
    });
  } catch {
    // The folder change itself does not depend on this; Sonic's wait simply times out.
  }
}

export async function sendChatMessage(
  username: string,
  question: string,
  attachments: { filename: string; content: string }[],
  borderScope: string,
  session_id: string,
  onTokenReceived: (token: string) => void,
  onTraceReceived?: (payload: any) => void,
  signal?: AbortSignal,
  folderExpected = false,
  editDecisions = false,
  gitBranch: string | null = null
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
        // The browser has a local folder connected: a code answer that finds none on the server says so, instead of quietly using GitHub.
        folder_expected: folderExpected,
        // This page reports Apply / Decline on a proposed folder change, so Sonic can wait for it and carry on in the same turn.
        edit_decisions: editDecisions,
        // The git branch the connected folder is on (read from .git/HEAD), so a commit goes where the person is working.
        git_branch: gitBranch,
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
  setMemoryDomain,
  setNameAlias,
  deleteMemoryLink,
  clearMemoryFacts,
  getMemoryStored,
  listOpenLoops,
  deleteOpenLoop,
  updateOpenLoop,
  listTasks,
  createTask,
  updateTask,
  deleteTask,
  listTaskRuns,
  markTaskSeen,
  runTaskNow,
  listMeetings,
  getMeetingsState,
  getWorkingNotes,
  getPlanMode,
  updatePlanMode,
  getCodePlan,
  actOnCodePlan,
  getLocalGitBundle,
  editWorkingNote,
  clearWorkingNotes,
  createMeeting,
  updateMeeting,
  deleteMeeting,
  listMeetingNotes,
  updateMeetingNote,
  deleteMeetingNote,
  startMeeting,
  endMeeting,
  listProgressNotes,
  addProgressNote,
  updateProgressNote,
  deleteProgressNote,
  getCalendarHeadsUps,
  putCalendarHeadsUps,
  previewCalendarHeadsUps,
  getRagMode,
  updateRagMode,
  getDeepThinking,
  updateDeepThinking,
  getToolModel,
  updateToolModel,
  getProactiveOpening,
  updateProactiveOpening,
  getOpsDigest,
  updateOpsDigest,
  getBudget,
  extendBudget,
  requestBudgetIncrease,
  getBudgetRequests,
  decideBudgetRequest,
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
  getMicrosoftStatus,
  startMicrosoftConnection,
  disconnectMicrosoft,
  getSonicHealth,
  getServerMemory,
  setGoalApproval,
  approveObservation,
  retireObservation,
  runObservationReflection,
  getPublicReadingShelf,
  reportEditDecision,
  getReadingShelf,
  removeShelfItem,
  unblockShelfSource,
  refreshReadingShelf,
  writeShelfReflections,
  decideShelfReflection,
  getOpsSuggestions,
  decideOpsSuggestion
};
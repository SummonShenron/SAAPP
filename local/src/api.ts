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

function isGuestPrincipal(principal: string | null | undefined): boolean {
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
    token = await window.Clerk?.session?.getToken();
  }

  if (!token) {
    token = localStorage.getItem("guest_token");
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
/**
 * Check PAAPP admin access (calls backend endpoint).
 */
export async function isPaappAdmin(clerkId: string): Promise<boolean> {
  try {
    const authHeaders = await getAuthHeaders();
    // Corrected path: Removed the redundant ${BASE_URL}
    const res = await fetch(
      `${BASE_URL}/admin/paapp?clerk_id=${encodeURIComponent(clerkId)}`, 
      { headers: { ...authHeaders } }
    );
    if (!res.ok) return false;
    const data = await res.json();
    return Boolean(data.allowed);
  } catch (err) {
    console.error("isPaappAdmin check failed", err);
    return false;
  }
}
/**
 * Fetch accessible workspace claims
 */
export async function getAffiliates(username: string): Promise<string[]> {
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

/**
 * Fetch one conversation thread's full message history
 */
export async function getConversation(sessionId: string): Promise<{ session_id: string; title: string; messages: any[] }> {
  const authHeaders = await getAuthHeaders();
  const res = await fetch(`${BASE_URL}/api/conversations/${encodeURIComponent(sessionId)}`, {
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
export async function sendChatMessage(
  username: string,
  question: string,
  attachments: { filename: string; content: string }[],
  borderScope: string,
  session_id: string,
  onTokenReceived: (token: string) => void,
  onTraceReceived?: (payload: any) => void
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
  getTasks: async () => {
    const authHeaders = await getAuthHeaders();
    const response = await fetch(`${BASE_URL}/api/tasks`, {
      headers: { ...authHeaders }
    });
    if (!response.ok) throw new Error("Failed to fetch tasks");
    return response.json();
  },
  
  updateTask: async (taskId: string, updates: any, username: string) => {
    const authHeaders = await getAuthHeaders();
    console.log("DEBUG: Sending Auth Headers:", authHeaders);
    const response = await fetch(`${BASE_URL}/api/tasks/${taskId}`, {
      method: "PUT",
      headers: {
        "Content-Type": "application/json",
        ...authHeaders
      },
      body: JSON.stringify(updates),
    });
    if (!response.ok) throw new Error("Failed to update task on backend");
    return response.json();
  },
  
  createTask: async (task: any, username: string) => {
    const authHeaders = await getAuthHeaders();
    const response = await fetch(`${BASE_URL}/api/tasks`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...authHeaders
      },
      body: JSON.stringify(task),
    });
    if (!response.ok) throw new Error("Failed to save task");
    return response.json();
  },

  deleteTask: async (taskId: string, username: string) => {
    const authHeaders = await getAuthHeaders();
    const response = await fetch(`${BASE_URL}/api/tasks/${taskId}`, {
      method: "DELETE",
      headers: { ...authHeaders }
    });
    if (!response.ok) throw new Error("Failed to delete task");
    return response.json();
  },
  
  getInsights(username: string) {
    return getAuthHeaders().then(authHeaders => {
      // Changed from a relative URL to absolute URL to avoid production routing bugs
      return fetch(`${BASE_URL}/api/insights?username=${username}`, {
        headers: { ...authHeaders }
      }).then(r => r.json());
    });
  },
  
  getAffiliates,
  getUserGroups,
  getIngestedDocuments,
  uploadDocuments,
  deleteDocument,
  verifyIdentity,
  isPaappAdmin,
  uploadAttachment,
  sendChatMessage,
  listConversations,
  getConversation,
  deleteConversation,
  getRagMode,
  updateRagMode,
  getDeepThinking,
  updateDeepThinking
};
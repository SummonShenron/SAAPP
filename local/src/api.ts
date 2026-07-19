// src/api.ts

// 1. Automatically adapt the backend URL based on development or production hosting
const BASE_URL = import.meta.env.VITE_API_BASE || "https://saapp.onrender.com";

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
async function getAuthHeaders(): Promise<Record<string, string>> {
  const headers: Record<string, string> = {};
  
  const guestToken = localStorage.getItem('guest_token');
  if (guestToken) {
    headers["Authorization"] = `Bearer ${guestToken}`;
  } else {
    const clerk = (window as any).Clerk;
    if (clerk && clerk.session) {
      // UPDATED: Now requests your specific JWT template
      const clerkToken = await clerk.session.getToken({ template: "email" });
      
      console.log("DEBUG: Clerk Token retrieved:", clerkToken ? "Yes" : "No");
      
      if (clerkToken) {
        headers["Authorization"] = `Bearer ${clerkToken}`;
      }
    } else {
      console.log("DEBUG: Clerk session not found.");
    }
  }
  
  return headers;
}

/**
 * Fetch current principal from backend.
 */
export async function getMe(usernameHint?: string): Promise<MeResponse> {
  const authHeaders = await getAuthHeaders();
  
  // Backwards compatibility for dev fallback logic
  const hint = usernameHint || (typeof window !== "undefined" ? (window as any).CURRENT_USER : undefined);
  if (hint) authHeaders["x-user-id"] = hint;

  const res = await fetch(`${BASE_URL}/api/me`, { headers: authHeaders });
  if (!res.ok) {
    throw new Error("Failed to fetch /api/me");
  }
  return res.json();
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
  // Note: We omit "Content-Type" so the browser can calculate its multipart boundaries
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
 * Save the current conversation state
 */
export async function saveConversation(title: string, messages: any[]): Promise<any> {
  const authHeaders = await getAuthHeaders();
  const res = await fetch(`${BASE_URL}/api/saved-conversations`, {
    method: "POST",
    headers: { 
      "Content-Type": "application/json",
      ...authHeaders 
    },
    body: JSON.stringify({ title, messages })
  });

  if (!res.ok) {
    throw new Error("Failed to save conversation.");
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
  onTokenReceived: (token: string) => void
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
        onTokenReceived(trimmed);
      }
    }
  }

  const trimmed = buffer.trim();
  if (trimmed.startsWith("data: ")) {
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
  saveConversation
};
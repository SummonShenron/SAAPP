// src/api.ts
const BASE_URL = "http://127.0.0.1:8000";

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
 * Fetch current principal from backend.
 * Optionally pass a username hint for dev flows where the backend expects x-user-id.
 */
// api.ts
export async function getMe(usernameHint?: string): Promise<MeResponse> {
  // Prefer explicit hint, then global window.CURRENT_USER
  const hint = usernameHint || (typeof window !== "undefined" ? (window as any).CURRENT_USER : undefined);
  const headers: Record<string, string> = {};
  if (hint) headers["x-user-id"] = hint;

  const res = await fetch(`${BASE_URL}/api/me`, { headers });
  if (!res.ok) {
    throw new Error("Failed to fetch /api/me");
  }
  return res.json();
}


/**
 * Check PAAPP admin access (calls backend endpoint).
 */
export async function isPaappAdmin(username: string): Promise<boolean> {
  try {
    const res = await fetch(
      `${BASE_URL}/api/is-paapp-admin?username=${encodeURIComponent(username)}`,
      { headers: { "x-user-id": username } }
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
 * Other API helpers
 */
export async function getAffiliates(username: string): Promise<string[]> {
  const response = await fetch(
    `${BASE_URL}/api/affiliates?username=${encodeURIComponent(username)}`,
    { headers: { "x-user-id": username } }
  );

  if (!response.ok) {
    throw new Error("Could not load secure workspace claims.");
  }
  const data = await response.json();
  return data.accessible_affiliates;
}

export async function getUserGroups(username: string): Promise<string[]> {
  const headers: Record<string,string> = {};
  if (username) headers["x-user-id"] = username;
  const res = await fetch(`${BASE_URL}/api/user/groups?username=${encodeURIComponent(username)}`, {
    headers
  });
  if (!res.ok) {
    throw new Error("Failed to retrieve directory authorization groups.");
  }
  const data = await res.json();
  // ensure we return an array
  return Array.isArray(data) ? data : (data.groups || []);
}


export async function getIngestedDocuments(username: string, affiliate: string): Promise<any[]> {
  const res = await fetch(
    `${BASE_URL}/api/documents?affiliate=${encodeURIComponent(affiliate)}`,
    { headers: { "x-user-id": username } }
  );
  if (!res.ok) throw new Error("Failed to fetch indexed document manifest.");
  return res.json();
}

export async function uploadDocuments(username: string, affiliate: string, files: FileList): Promise<any> {
  const formData = new FormData();
  Array.from(files).forEach((file) => formData.append("files", file));

  const res = await fetch(
    `${BASE_URL}/api/upload?affiliate=${encodeURIComponent(affiliate)}`,
    {
      method: "POST",
      headers: { "x-user-id": username },
      body: formData,
    }
  );

  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || "Upload pipeline execution failed.");
  }
  return res.json();
}

export async function deleteDocument(username: string, affiliate: string, docId: string): Promise<any> {
  const res = await fetch(
    `${BASE_URL}/api/documents/${docId}?affiliate=${encodeURIComponent(affiliate)}`,
    {
      method: "DELETE",
      headers: { "x-user-id": username },
    }
  );
  if (!res.ok) throw new Error("Failed to purge document from vector space.");
  return res.json();
}

export async function verifyIdentity(username: string): Promise<boolean> {
  try {
    const response = await fetch(`${BASE_URL}/api/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username }),
    });
    return response.ok;
  } catch (error) {
    console.error("Identity transmission subsystem error:", error);
    return false;
  }
}

export async function uploadAttachment(username: string, sessionId: string, file: File): Promise<any> {
  const formData = new FormData();
  formData.append("username", username);
  formData.append("session_id", sessionId);
  formData.append("file", file);

  const res = await fetch(`${BASE_URL}/api/upload-attachment`, {
    method: "POST",
    body: formData,
  });

  if (!res.ok) {
    throw new Error("Attachment upload failed.");
  }

  return res.json();
}

/**
 * sendChatMessage streams tokens via a server-sent stream; onTokenReceived is called with each "data: ..." chunk.
 */
export async function sendChatMessage(
  username: string,
  question: string,
  attachments: { filename: string; content: string }[],
  borderScope: string,
  session_id: string,
  onTokenReceived: (token: string) => void
): Promise<void> {
  const response = await fetch(`${BASE_URL}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
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

export const api = {
  
  getMe: async (username: string) => {
    const response = await fetch("http://127.0.0.1:8000/api/me", {
      method: "GET",
      headers: {
        "Content-Type": "application/json",
        "x-user-id": username // This must match the backend alias exactly!
      }
    });
    if (!response.ok) {
      throw new Error(`Failed to fetch /api/me Status: ${response.status}`);
    }
    return response.json();
  },
  getTasks: async () => {
    const response = await fetch(`${BASE_URL}/api/tasks`);
    if (!response.ok) throw new Error("Failed to fetch tasks");
    return response.json();
  },
  updateTask: async (taskId: string, updates: any, username: string) => {
    const response = await fetch(`${BASE_URL}/api/tasks/${taskId}`, {
      method: "PUT",
      headers: {
        "Content-Type": "application/json",
        "x-user-id": username
      },
      body: JSON.stringify(updates),
    });
    if (!response.ok) throw new Error("Failed to update task on backend");
    return response.json();
  },
  createTask: async (task: any, username: string) => {
    const response = await fetch(`${BASE_URL}/api/tasks`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "x-user-id": username
      },
      body: JSON.stringify(task),
    });
    if (!response.ok) throw new Error("Failed to save task");
    return response.json();
  },

  deleteTask: async (taskId: string, username: string) => {
    const response = await fetch(`${BASE_URL}/api/tasks/${taskId}`, {
      method: "DELETE",
      headers: {
        "x-user-id": username
      }
    });
    if (!response.ok) throw new Error("Failed to delete task");
    return response.json();
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
};

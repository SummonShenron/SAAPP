const BASE_URL = 'http://127.0.0.1:8000';

export interface ChatResponse {
  user: string;
  email: string;
  answer: string;
}

async function isPaappAdmin(username: string): Promise<boolean> {
  const res = await fetch(
    `${BASE_URL}/api/is-paapp-admin?username=${encodeURIComponent(username)}`,
    {
      headers: { "x-user-id": username }
    }
  );

  if (!res.ok) return false;

  const data = await res.json();
  return data.allowed;
}

export const api = {
  async getAffiliates(username: string): Promise<string[]> {
  const response = await fetch(
    `${BASE_URL}/api/affiliates?username=${encodeURIComponent(username)}`,
    { headers: { "x-user-id": username } }
  );

    if (!response.ok) {
      throw new Error("Could not load secure workspace claims.");
    }
    const data = await response.json();
    return data.accessible_affiliates;
  },

  async getUserGroups(username: string): Promise<string[]> {
    const res = await fetch(
      `${BASE_URL}/api/user/groups?username=${encodeURIComponent(username)}`,
      { headers: { 'x-user-id': username } }
    );
    if (!res.ok) throw new Error("Failed to retrieve directory authorization groups.");
    return res.json();
  },

  async getIngestedDocuments(username: string, affiliate: string): Promise<any[]> {
    const res = await fetch(
      `${BASE_URL}/api/documents?affiliate=${encodeURIComponent(affiliate)}`,
      { headers: { 'x-user-id': username } }
    );
    if (!res.ok) throw new Error("Failed to fetch indexed document manifest.");
    return res.json();
  },

  async uploadDocuments(username: string, affiliate: string, files: FileList): Promise<any> {
    const formData = new FormData();
    Array.from(files).forEach((file) => formData.append("files", file));

    const res = await fetch(
      `${BASE_URL}/api/upload?affiliate=${encodeURIComponent(affiliate)}`,
      {
        method: "POST",
        headers: { 'x-user-id': username },
        body: formData,
      }
    );

    if (!res.ok) {
      const errorData = await res.json();
      throw new Error(errorData.detail || "Upload pipeline execution failed.");
    }
    return res.json();
  },

  async deleteDocument(username: string, affiliate: string, docId: string): Promise<any> {
    const res = await fetch(
      `${BASE_URL}/api/documents/${docId}?affiliate=${encodeURIComponent(affiliate)}`,
      {
        method: "DELETE",
        headers: { 'x-user-id': username }
      }
    );
    if (!res.ok) throw new Error("Failed to purge document from vector space.");
    return res.json();
  },

  async verifyIdentity(username: string): Promise<boolean> {
    try {
      const response = await fetch(`${BASE_URL}/api/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username }),
      });
      return response.ok;
    } catch (error) {
      console.error("Identity transmission subsystem error:", error);
      return false;
    }
  },

  isPaappAdmin,

  async uploadAttachment(username: string, sessionId: string, file: File): Promise<any> {
    const formData = new FormData();
    formData.append("username", username);
    formData.append("session_id", sessionId);
    formData.append("file", file);

    const res = await fetch(`${BASE_URL}/api/upload-attachment`, {
      method: "POST",
      body: formData
    });

    if (!res.ok) {
      throw new Error("Attachment upload failed.");
    }

    return res.json();
  },

  async sendChatMessage(
    username: string,
    question: string,
    attachments: { filename: string; content: string }[],
    borderScope: string,
    session_id: string,                     // ← NEW ARGUMENT
    onTokenReceived: (token: string) => void
  ): Promise<void> {

    const response = await fetch(`${BASE_URL}/api/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        username,
        question,
        affiliate: borderScope,
        attachments,
        session_id
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
};

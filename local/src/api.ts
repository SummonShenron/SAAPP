const BASE_URL = 'http://192.168.1.10:8000';

export interface ChatResponse {
  user: string;
  email: string;
  answer: string;
}

export const api = {
  /**
   * Hits GET /api/affiliates?username=... using URL search query parameters
   */
  async getAffiliates(username: string): Promise<string[]> {
    const response = await fetch(`${BASE_URL}/api/affiliates?username=${encodeURIComponent(username)}`);
    if (!response.ok) {
      throw new Error("Could not load secure workspace claims.");
    }
    const data = await response.json();
    return data.accessible_affiliates;
  },

  async getUserGroups(username: string): Promise<string[]> {
    const res = await fetch(`${BASE_URL}/api/user/groups?username=${encodeURIComponent(username)}`, {
      headers: { 'x-user-id': username }
    });
    if (!res.ok) throw new Error("Failed to retrieve directory authorization groups.");
    return res.json();
  },

  async getIngestedDocuments(username: string, affiliate: string): Promise<any[]> {
    const res = await fetch(`${BASE_URL}/api/documents?affiliate=${encodeURIComponent(affiliate)}`, {
      headers: { 'x-user-id': username }
    });
    if (!res.ok) throw new Error("Failed to fetch indexed document manifest.");
    return res.json();
  },

  async uploadDocuments(username: string, affiliate: string, files: FileList): Promise<any> {
    const formData = new FormData();
    Array.from(files).forEach((file) => {
      formData.append("files", file);
    });

    const res = await fetch(`${BASE_URL}/api/upload?affiliate=${encodeURIComponent(affiliate)}`, {
      method: "POST",
      headers: { 'x-user-id': username }, // Let FastAPI handle identity tracking
      body: formData,
    });
    if (!res.ok) {
      const errorData = await res.json();
      throw new Error(errorData.detail || "Upload pipeline execution failed.");
    }
    return res.json();
  },

  async deleteDocument(username: string, affiliate: string, docId: string): Promise<any> {
    const res = await fetch(`${BASE_URL}/api/documents/${docId}?affiliate=${encodeURIComponent(affiliate)}`, {
      method: "DELETE",
      headers: { 'x-user-id': username }
    });
    if (!res.ok) throw new Error("Failed to purge document from vector space.");
    return res.json();
  },

/**
   * Post identity parameters to backend to confirm authorization validity.
   */
  async verifyIdentity(username: string): Promise<boolean> {
    try {
      const response = await fetch(`${BASE_URL}/api/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username }),
      });
      return response.ok; // Returns true on 200, false on 401/error
    } catch (error) {
      console.error("Identity transmission subsystem error:", error);
      return false;
    }
  },

  /**
   * Hits POST /api/chat to run security matching metrics against LLM
   */
  async sendChatMessage(
    username: string, 
    question: string, 
    borderScope: string,
    onTokenReceived: (token: string) => void
  ): Promise<void> {
    const response = await fetch(`${BASE_URL}/api/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, question, affiliate: borderScope }),
    });

    if (!response.ok) {
      throw new Error("Failed to initialize communication stream.");
    }

    const reader = response.body?.getReader();
    const decoder = new TextDecoder();

    if (!reader) {
      throw new Error("ReadableStream is completely unsupported on this client client platform.");
    }

    // Read the network buffer sequentially chunk-by-chunk
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      
      const textChunk = decoder.decode(value, { stream: true });
      onTokenReceived(textChunk); // Push the new character directly to the UI
    }
  }
};
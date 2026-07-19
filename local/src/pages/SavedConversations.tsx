import React, { useEffect, useState } from "react";
import './__styles__/SavedConversations.css';
import { useAuth } from '@clerk/clerk-react';

interface SavedMessage {
  type: "human" | "ai" | "system";
  content: string;
}

interface SavedConversationsPageProps {
  username: string;
}

export const SavedConversationsPage: React.FC<SavedConversationsPageProps> = ({ username }) => {
  const { getToken } = useAuth();
  const [titles, setTitles] = useState<string[]>([]);
  const [selectedTitle, setSelectedTitle] = useState<string | null>(null);
  const [messages, setMessages] = useState<SavedMessage[]>([]);
  const BASE_URL = "https://saapp.onrender.com/";
  const fetchWithAuth = async (url: string) => {
    const token = await getToken(); // Get the real Clerk token
    return fetch(url, {
      headers: { 
        "Authorization": `Bearer ${token}`, // Use standard Bearer auth
        "Content-Type": "application/json" 
      }
    });
  };

  useEffect(() => {
    fetchWithAuth(`${BASE_URL}api/saved-conversations`)
      .then(res => res.json())
      .then(data => setTitles(data.titles || []))
      .catch(() => setTitles([]));
  }, []); // No dependency on username needed anymore

  const loadConversation = (title: string) => {
    setSelectedTitle(title);
    fetchWithAuth(`${BASE_URL}api/saved-conversations/${title}`)
      .then(res => res.json())
      .then(data => setMessages(data.messages || []))
      .catch(() => setMessages([]));
  };

  return (
    <div className="saved-conversations-container">
    {/* LEFT SIDEBAR */}
    <aside className="saved-conversations-sidebar">
        <h3 className="sidebar-title">Saved Conversations</h3>
        <div className="conversation-list">
        {Array.isArray(titles) && titles.map(t => (
            <div
            key={t}
            className={`conversation-item ${selectedTitle === t ? "active" : ""}`}
            onClick={() => loadConversation(t)}
            >
            <div className="conversation-title">{t}</div>
            </div>
        ))}
        </div>
    </aside>

    {/* RIGHT PANEL */}
    <main className="saved-conversations-viewer">
        <h3 className="viewer-title">
            {selectedTitle || "Select a conversation"}
        </h3>

        <div className="saved-chat-wrapper">
            <div className="messages-container">
            {Array.isArray(messages) && messages.map((msg, idx) => (
                <div key={idx} className={`message-bubble ${msg.type}`}>
                <div className="message-content">{msg.content}</div>
                </div>
            ))}
            </div>
        </div>
        </main>
    </div>
  );
};
import React, { useEffect, useState } from "react";
import './__styles__/SavedConversations.css';
interface SavedMessage {
  type: "human" | "ai" | "system";
  content: string;
}

interface SavedConversationsPageProps {
  username: string;
}

export const SavedConversationsPage: React.FC<SavedConversationsPageProps> = ({ username }) => {
  const [titles, setTitles] = useState<string[]>([]);
  const [selectedTitle, setSelectedTitle] = useState<string | null>(null);
  const [messages, setMessages] = useState<SavedMessage[]>([]);

  useEffect(() => {
    fetch(`/api/saved-conversations?username=${username}`)
      .then(res => res.json())
      .then((data: { titles: string[] }) => setTitles(data.titles));
  }, [username]);

  const loadConversation = (title: string) => {
    setSelectedTitle(title);
    console.log("Fetching:", `/api/saved-conversations?username=${username}`);
    fetch(`/api/saved-conversations/${title}?username=${username}`)
      .then(res => res.json())
      .then((data: { title: string; messages: SavedMessage[] }) => {
        setMessages(data.messages);
      });
  };

  return (
    <div className="saved-conversations-container">
    {/* LEFT SIDEBAR */}
    <aside className="saved-conversations-sidebar">
        <h3 className="sidebar-title">Saved Conversations</h3>

        <div className="conversation-list">
        {titles.map(t => (
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
            {messages.map((msg, idx) => (
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

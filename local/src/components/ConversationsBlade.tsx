import React, { useEffect, useState } from "react";
import "./__styles__/ConversationsBlade.css";
import { api } from "../api";
import type { ConversationSummary } from "../api";

interface ConversationsBladeProps {
  activeSessionId: string;
  onSelect: (sessionId: string) => void;
  onNew: () => void;
}

function formatRelativeTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const diffMin = Math.round((Date.now() - date.getTime()) / 60000);
  if (diffMin < 1) return "just now";
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.round(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  const diffDay = Math.round(diffHr / 24);
  return `${diffDay}d ago`;
}

const ConversationsBlade: React.FC<ConversationsBladeProps> = ({ activeSessionId, onSelect, onNew }) => {
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    api.listConversations()
      .then((data: ConversationSummary[]) => { if (!cancelled) setConversations(data); })
      .catch(() => { if (!cancelled) setConversations([]); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  const handleDelete = async (e: React.MouseEvent, sessionId: string) => {
    e.stopPropagation();
    try {
      await api.deleteConversation(sessionId);
      setConversations(prev => prev.filter(c => c.session_id !== sessionId));
    } catch (err) {
      console.error("Failed to delete conversation:", err);
    }
  };

  return (
    <div className="conversations-panel">
      <div className="conversations-header">
        <h2 className="conversations-title">Conversations</h2>
        <button type="button" className="conversations-new-btn" onClick={onNew} title="Start a new conversation">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <line x1="12" y1="5" x2="12" y2="19" />
            <line x1="5" y1="12" x2="19" y2="12" />
          </svg>
          New
        </button>
      </div>

      <div className="conversations-list">
        {loading && <div className="conversations-empty">Loading…</div>}
        {!loading && conversations.length === 0 && (
          <div className="conversations-empty">No conversations yet.</div>
        )}
        {!loading && conversations.map(c => (
          <div
            key={c.session_id}
            className={`conversation-row ${c.session_id === activeSessionId ? "active" : ""}`}
            onClick={() => onSelect(c.session_id)}
          >
            <div className="conversation-row-main">
              <div className="conversation-row-title">{c.title}</div>
              <div className="conversation-row-time">{formatRelativeTime(c.updated_at)}</div>
            </div>
            <button
              type="button"
              className="conversation-delete-btn"
              title="Delete conversation"
              onClick={(e) => handleDelete(e, c.session_id)}
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="3 6 5 6 21 6" />
                <path d="M19 6l-2 14H7L5 6" />
                <line x1="10" y1="11" x2="10" y2="17" />
                <line x1="14" y1="11" x2="14" y2="17" />
              </svg>
            </button>
          </div>
        ))}
      </div>
    </div>
  );
};

export default ConversationsBlade;

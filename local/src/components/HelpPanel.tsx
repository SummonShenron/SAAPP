import React, { useState } from "react";
import { useNavigate } from "react-router-dom";
import "./__styles__/HelpPanel.css"
import { getEffectivePrincipal, isGuestPrincipal } from "../api";
import { CONTENT } from "./helpContent";
import { DEVELOPER_GROUP, groupedTopics } from "./helpTopics";

interface HelpPanelProps {
  onClose?: () => void;
}

/**
 * The help overlay. The topics and their order are in helpTopics.ts and the words are in helpContent.tsx; this is only the shell. A guest sees the
 * topics that apply to a guest, and the developer topics are folded away until someone asks for them, so a person who only wants to know how to
 * read a letter never meets a list of technical terms.
 */
const HelpPanel: React.FC<HelpPanelProps> = ({ onClose }) => {
  const navigate = useNavigate();
  const guest = isGuestPrincipal(getEffectivePrincipal());
  const groups = groupedTopics(guest);
  const [activeId, setActiveId] = useState(groups[0].topics[0].id);
  const [developerOpen, setDeveloperOpen] = useState(false);

  const active = groups.flatMap(g => g.topics).find(t => t.id === activeId) ?? groups[0].topics[0];
  const showDeveloper = developerOpen || active.group === DEVELOPER_GROUP;
  const ctx = { guest, go: (path: string) => { onClose?.(); navigate(path); } };
  const render = CONTENT[active.id];

  return (
    <div className="help-panel">
      {onClose && (
        <button className="help-panel-close-btn" onClick={onClose} aria-label="Close help panel">
          ✕
        </button>
      )}
      <div className="help-panel-header">
        <h2 className="help-title">Welcome to Sonic Assistant</h2>
        <p className="help-subtitle">
          {guest
            ? "A personal AI assistant that answers questions, reads what you attach, looks things up, and asks before it changes anything. Here's what it can do."
            : "Sonic answers questions, reads letters, bills and spreadsheets you attach, looks things up, helps with your email and calendar, and remembers what matters to you. It asks before it changes anything. Pick a topic to see how."}
        </p>
      </div>

      <div className="help-panel-body">
        <nav className="help-nav" aria-label="Help topics">
          {groups.map(({ group, topics }) => {
            const isDeveloper = group === DEVELOPER_GROUP;
            if (isDeveloper && !showDeveloper) {
              return (
                <button key={group} type="button" className="help-nav-item help-nav-more" aria-expanded={false} onClick={() => setDeveloperOpen(true)}>
                  {group} ▸
                </button>
              );
            }
            return (
              <React.Fragment key={group}>
                <div className="help-nav-group">{group}</div>
                {topics.map((t) => (
                  <button
                    key={t.id}
                    type="button"
                    className={`help-nav-item ${t.id === active.id ? "active" : ""}`}
                    aria-current={t.id === active.id ? "page" : undefined}
                    onClick={() => setActiveId(t.id)}
                  >
                    {t.title}
                  </button>
                ))}
              </React.Fragment>
            );
          })}
        </nav>
        <div className="help-content">
          <h3 className="help-content-title">{active.title}</h3>
          {render ? render(ctx) : null}
        </div>
      </div>
    </div>
  );
};

export default HelpPanel;

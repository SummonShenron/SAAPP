import React, { useState } from "react";
import "./__styles__/HelpPanel.css"

interface HelpPanelProps {
  onClose?: () => void;
}

interface HelpTopic {
  id: string;
  title: string;
  content: React.ReactNode;
}

const SECTIONS: HelpTopic[] = [
  {
    id: "overview",
    title: "What kind of assistant is this?",
    content: (
      <>
        <p>Two things at once, depending on what you ask:</p>
        <ul>
          <li><strong>A knowledge-base assistant</strong> — ask about indexed documents/policies and it answers strictly from what's retrieved (or draws on general knowledge too, if you enable Open-Ended Answers).</li>
          <li><strong>A personal agent</strong> — it remembers durable facts about you across conversations (preferences, your role, ongoing projects), can research and act on real GitHub repos, run sandboxed code, search the web, and ask you a clarifying question instead of guessing when something's genuinely ambiguous.</li>
        </ul>
        <p>It automatically figures out which mode a question needs — you don't have to tell it.</p>
      </>
    ),
  },
    {
    id: "personal_kb",
    title: "Your Personal Knowledge Base",
    content: (
      <>
        <p>Every user gets their own private, personal knowledge base upon sign-up.</p>
        <ul>
          <li><strong>Private & Secure:</strong> Your documents and data are isolated to your account.</li>
          <li><strong>Context-Aware:</strong> The assistant uses your personal knowledge base to provide answers tailored to your specific documents and history.</li>
          <li><strong>Easy Management:</strong> You can upload, manage, and query your documents directly through the interface.</li>
        </ul>
      </>
    ),
  },
  {
    id: "memory",
    title: "Memory & Personalization",
    content: (
      <>
        <p>The assistant saves durable facts about you as you talk — your name, role, preferences, ongoing projects — and brings the relevant ones into future conversations automatically.</p>
        <ul>
          <li>Say things like "remember that I prefer dark mode" or just mention something durable in passing ("my name is Jack")</li>
          <li>Ask "what do you remember about me?" to see what's saved</li>
          <li>Facts decay in relevance over time if never reinforced, so stale info naturally fades rather than sticking forever</li>
          <li>Your core identity facts (name, pronouns, role) are always available; everything else surfaces only when actually relevant to what you're asking</li>
        </ul>
      </>
    ),
  },
  {
    id: "modes",
    title: "Response Modes (chat footer toggles)",
    content: (
      <>
        <p>Three settings next to the message box change how it investigates and answers:</p>
        <ul>
          <li><strong>Open-Ended Answers</strong> — off (strict) means it only answers from your indexed knowledge base; on lets it also draw on general knowledge when the knowledge base doesn't have the answer.</li>
          <li><strong>Deep Thinking</strong> — lets it take more investigation steps, reconsider a premature answer more than once, and reason more carefully at each step. Slower, but more thorough for complex or multi-part questions.</li>
          <li><strong>Target Repo</strong> (the code icon) — pin a specific GitHub "owner/repo" so it always knows which repo you mean, instead of guessing from context every time. Persists across conversations until you change or clear it; mentioning a different repo by name in a message always overrides the pin for that message.</li>
        </ul>
      </>
    ),
  },
  {
    id: "github",
    title: "GitHub Repository Tools",
    content: (
      <>
        <p>Ask it to look at real code in a GitHub repo — it can chain multiple lookups in one turn to actually verify an answer instead of guessing:</p>
        <ul>
          <li>List a repo's file structure, read specific files, diff two branches, or list recent commits</li>
          <li>"What does agent_workflow.py do?", "what changed in the last PR?", "find the file that handles X"</li>
          <li>It's honest about what it actually checked — if a lookup fails or comes back empty, it says so rather than inventing an answer</li>
        </ul>
      </>
    ),
  },
  {
    id: "sandbox",
    title: "Code Execution (Sandbox)",
    content: (
      <>
        <p>It can write and run small, self-contained Python snippets to check its own logic before answering — genuinely executed in an isolated sandbox, not just written out.</p>
        <ul>
          <li>Useful for calculations, verifying a regex or algorithm, or checking a proposed fix against a quick test case</li>
          <li>Fully isolated: no filesystem, network, or access to your real project — only a small set of safe standard-library modules</li>
          <li>It can't run your actual application (that needs real dependencies/database access) — only self-contained logic it can fully reproduce in the snippet</li>
        </ul>
      </>
    ),
  },
  {
    id: "prs",
    title: "Pull Requests & Issues (Human-Approved)",
    content: (
      <>
        <p>It can draft a GitHub Pull Request or Issue for you — but never submits anything without your explicit approval.</p>
        <ul>
          <li>"Create a PR to merge feature/x into main" or "open an issue about the login bug"</li>
          <li>It drafts a title/description and shows you an approval card first</li>
          <li>Reply "approve" (or similar) to actually submit it, or "reject"/"cancel" to drop it — nothing is written to GitHub until you say so</li>
        </ul>
      </>
    ),
  },
  {
    id: "clarify",
    title: "Clarifying Questions",
    content: (
      <>
        <p>When a request is genuinely ambiguous — two repos it could mean, "that file" with nothing identifying which one — it pauses and asks you directly instead of guessing and risking a wrong answer.</p>
        <ul>
          <li>Just answer its question normally; it picks up exactly where it left off, including anything it had already checked</li>
        </ul>
      </>
    ),
  },
  {
    id: "websearch",
    title: "Web Search",
    content: (
      <p>When your question needs current information the knowledge base or a repo can't provide, it can search the web and cite what it finds.</p>
    ),
  },
  {
    id: "conversations",
    title: "Conversations",
    content: (
      <>
        <p>Click the conversations icon in the chat toolbar to open the conversation switcher.</p>
        <ul>
          <li>Use the "+" button to start a new conversation</li>
          <li>Click any conversation in the list to switch to it</li>
          <li>Each conversation keeps its own full message history</li>
        </ul>
        <p>Conversations are stored per-user and isolated by identity.</p>
      </>
    ),
  },
  {
    id: "attachments",
    title: "Attachments",
    content: (
      <>
        <p>SAAPP can ingest and summarize attachments in chat.</p>
        <ul>
          <li>Automatic text extraction</li>
          <li>Summary injection into workflow</li>
          <li>Priority metadata for relevance</li>
        </ul>
      </>
    ),
  },
  {
    id: "selfservice",
    title: "SelfService Document Viewer",
    content: (
      <>
        <p>Browse indexed documents by affiliate.</p>
        <ul>
          <li>Affiliate-aware document access</li>
          <li>PDF manifest generation</li>
          <li>Safe fallback for empty folders</li>
          <li>No crashes on missing directories</li>
        </ul>
      </>
    ),
  },
  {
    id: "identity",
    title: "Identity & Permissions",
    content: (
      <>
        <p>SAAPP uses unified identity across all modules.</p>
        <ul>
          <li>principal (frontend identity)</li>
          <li>x-user-id (backend identity)</li>
          <li>directory.json group claims</li>
          <li>affiliate scoping</li>
        </ul>
        <p>Identity ensures correct permissions and isolated data.</p>
      </>
    ),
  },
  {
    id: "architecture",
    title: "How It's Built (Multi-Agent Workflow)",
    content: (
      <>
        <p>SAAPP is powered by a LangGraph-based multi-agent pipeline that figures out what a request actually needs before responding.</p>
        <ul>
          <li>Coordinator — plans which agent(s) a request needs, in sequence</li>
          <li>Reasoner — an LLM classification pass over intent (retrieval, memory, tools, writes, ...)</li>
          <li>Retriever + Formatter — the knowledge-base RAG path</li>
          <li>Tool Agent — one unified research loop covering GitHub, the code sandbox, and web search, able to chain multiple actions per turn</li>
          <li>Memory — passive fact save/recall woven into every relevant turn</li>
        </ul>
      </>
    ),
  },
  {
    id: "known-issues",
    title: "Current Known Issues",
    content: (
      <>
        <p>Current issues the team is aware of:</p>
        <ul>
          <li>Continues to hallucinate occasionally when providing code solutions</li>
        </ul>
      </>
    ),
  },
];

const HelpPanel: React.FC<HelpPanelProps> = ({ onClose }) => {
  const [activeId, setActiveId] = useState(SECTIONS[0].id);
  const active = SECTIONS.find((s) => s.id === activeId) ?? SECTIONS[0];

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
          This started as a RAG chatbot over your knowledge base — it's grown into your personal
          agent too. It remembers things about you, can look things up and act on your GitHub
          repos with your approval, run real code to check its own answers, and hold a real
          back-and-forth instead of just answering once and stopping. Here's what it can actually
          do today.
        </p>
      </div>

      <div className="help-panel-body">
        <nav className="help-nav">
          {SECTIONS.map((s) => (
            <button
              key={s.id}
              className={`help-nav-item ${s.id === activeId ? "active" : ""}`}
              onClick={() => setActiveId(s.id)}
            >
              {s.title}
            </button>
          ))}
        </nav>
        <div className="help-content">
          <h3 className="help-content-title">{active.title}</h3>
          {active.content}
        </div>
      </div>
    </div>
  );
};

export default HelpPanel;

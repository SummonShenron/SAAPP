import React, { useState } from "react";
import "./__styles__/HelpPanel.css"

interface SectionProps {
  title: string;
  children: React.ReactNode;
}

const HelpSection: React.FC<SectionProps> = ({ title, children }) => {
  const [open, setOpen] = useState(false);

  return (
    <div className="help-section">
      <button
        className="help-section-header"
        onClick={() => setOpen(!open)}
      >
        <span>{title}</span>
        <span>{open ? "▲" : "▼"}</span>
      </button>

      {open && <div className="help-section-body">{children}</div>}
    </div>
  );
};

const HelpPanel: React.FC = () => {
  return (
    <div className="help-panel">
      <h2 className="help-title">SAAPP Help & Commands</h2>
      <p className="help-subtitle">
        Learn how to use SAAPP’s full suite of tools, commands, and workflows.
      </p>

      {/* Conversations */}
      <HelpSection title="Conversations">
        <p>SAAPP supports saving, loading, and listing conversations.</p>
        <pre className="help-code">
        {`save conversation <title>
        load conversation <title>
        list conversations`}
        </pre>
        <p>Examples:</p>
        <ul>
          <li>save conversation Project Alpha</li>
          <li>load conversation Yesterday Notes</li>
          <li>list conversations</li>
        </ul>
        <p>Conversations are stored per-user and isolated by identity.</p>
      </HelpSection>

      {/* PAAPP Commands */}
      <HelpSection title="PAAPP Commands">
        <p>SAAPP can log time, create calendar events, and store notes.</p>
        <pre className="help-code">
        {`log <duration> <activity>
        create calendar event <details>
        remember <note>`}
        </pre>
        <p>Examples:</p>
        <ul>
          <li>log 1 hour coding</li>
          <li>log 30 minutes meetings</li>
          <li>create calendar event Team Sync at 3pm</li>
          <li>remember server migration Friday</li>
        </ul>
      </HelpSection>

      {/* Attachments */}
      <HelpSection title="Attachments">
        <p>SAAPP can ingest and summarize attachments in chat.</p>
        <ul>
          <li>Automatic text extraction</li>
          <li>Summary injection into workflow</li>
          <li>Priority metadata for relevance</li>
        </ul>
      </HelpSection>
      
      {/* SelfService */}
      <HelpSection title="SelfService Document Viewer">
        <p>Browse indexed documents by affiliate.</p>
        <ul>
          <li>Affiliate-aware document access</li>
          <li>PDF manifest generation</li>
          <li>Safe fallback for empty folders</li>
          <li>No crashes on missing directories</li>
        </ul>
      </HelpSection>

      {/* Calendar */}
      <HelpSection title="Calendar Module">
        <p>The Calendar page allows you to create and manage events.</p>
        <ul>
          <li>Create events with title, date, start/end time</li>
          <li>Edit existing events</li>
          <li>View affiliate-specific calendars</li>
          <li>Automatic time normalization</li>
        </ul>
      </HelpSection>

      {/* Logs */}
      <HelpSection title="Log Module">
        <p>Track time spent on tasks using the Log page.</p>
        <ul>
          <li>Create logs with duration and description</li>
          <li>Daily and weekly summaries</li>
          <li>Automatic duration calculation</li>
        </ul>
      </HelpSection>

      {/* Taskboard */}
      <HelpSection title="Taskboard">
        <p>Browse, move, and create tasks on the Team Taskboard.</p>
        <ul>
          <li>Must be a member of Taskboard_Admins to create/delete tasks</li>
          <li>3 Lane board: Backlog, In Progress and Completed</li>
          <li>Reorder tasks basedon priority</li>
        </ul>
      </HelpSection>
      {/* Taskboard */}
      <HelpSection title="Insights">
        <p>Users can both view (via insights tab) and query the model to recieve personalized insight metrics.</p>
        <ul>
          <li>Ask questions like "how many hours did I spend debugging last week?</li>
          <li>View the Insights tab to see customized insights and accomplishments</li>
          <li>Helpful for calculating time, usage, and schedules</li>
        </ul>
      </HelpSection>
      {/* Identity */}
      <HelpSection title="Identity & Permissions">
        <p>SAAPP uses unified identity across all modules.</p>
        <ul>
          <li>principal (frontend identity)</li>
          <li>x-user-id (backend identity)</li>
          <li>directory.json group claims</li>
          <li>affiliate scoping</li>
        </ul>
        <p>Identity ensures correct permissions and isolated data.</p>
      </HelpSection>

      {/* Multi-Agent Workflow */}
      <HelpSection title="Multi-Agent Workflow">
        <p>SAAPP is powered by two internal agent pipelines that work together to understand your requests and produce intelligent responses.</p>

        <h4>Knowledge & Conversation Pipeline</h4>
        <ul>
          <li>Coordinator</li>
          <li>Reasoner</li>
          <li>Retriever</li>
          <li>Formatter</li>
          <li>Memory</li>
          <li>Decision Boundary</li>
        </ul>
        <p>
          These agents handle normal chat, knowledge-base retrieval, document attachments,
          and PAAPP tool interactions.
        </p>

        <h4>Insight Pipeline (Activity Analytics)</h4>
        <ul>
          <li>Snapshot</li>
          <li>Classifier</li>
          <li>Pattern Detector</li>
          <li>Trend Analyzer</li>
          <li>Insight Query</li>
          <li>Insight Formatter</li>
        </ul>
        <p>
          This pipeline activates when you ask questions about your activity history,
          such as weekly summaries, productivity trends, streaks, or category insights.
        </p>
      </HelpSection>

    </div>
  );
};

export default HelpPanel;

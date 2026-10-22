import React, { lazy, Suspense, useEffect, useMemo, useState, useRef } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { useAffiliateScope } from '../hooks/useAffiliateScope';
import { useLocalWorkspace } from '../hooks/useLocalWorkspace';
import LocalEditCard from '../components/LocalEditCard';
import LocalFolderConsent from '../components/LocalFolderConsent';
import { hasLocalFolderConsent, recordLocalFolderConsent } from '../localWorkspace';
import type { ApplyResult, UndoResult } from '../localEdits';
import type { EditProposal } from '../localEditsCore';
import ConversationsBlade from '../components/ConversationsBlade';
import MiniPatchy, { type PatchyEnergy } from '../components/MiniPatchy';
import PatchyHero from '../components/PatchyHero';
import { TURN_SENT_STATUS, budgetFromLevel, lookForNode } from '../components/patchyLook';
import OptionWheel, { type OptionWheelItem } from '../components/OptionWheel';
import { getWelcomeCards } from '../utils/Example_List';
import type { WelcomeCard } from '../welcomeCards';
import { HEIC_NOTICE, isUnreadableHeic, shrinkImageFile } from '../utils/imageShrink';
import MeetingControl from '../components/MeetingControl';
import { api, BASE_URL, getAuthHeaders, getEffectivePrincipal, isGuestPrincipal, type BudgetView, type KnowledgeBase } from '../api';
import BudgetBanner from '../components/BudgetBanner';
import UsageRing from '../components/UsageRing';
import TracePanel, { type TraceStep } from '../components/TracePanel';
import { mergeFacts, recordFacts, toolStepLabel, type TraceFactsById } from '../components/traceFacts';
import { useMediaQuery } from '../hooks/useMediaQuery';
import LogoMark from '../components/LogoMark';
import InputGlow from '../components/InputGlow';
import Icon from '../components/icons';
import { BRAND_NAME, BRAND_TAGLINE } from '../components/brand';
import { shell } from '../components/shellStore';
import ReactMarkdown from 'react-markdown';
import '../components/__styles__/ReplyText.css';
import remarkGfm from 'remark-gfm';
import { CODE_BOX_STYLE } from '../components/codeBoxStyle';
import { useAuth } from '@clerk/clerk-react';
import { describeChatError, toChatSendError } from '../chatErrors';

interface MessageImage {
  filename: string;
  dataUrl?: string; // instant local preview for the message just sent (no round-trip needed)
  fileId?: string;  // durable GridFS reference, used to fetch the image on reload / another device
  generated?: string; // set when Sonic made this picture (a card with the person's words on their photo): shown large, with a Download button
}

interface Message {
  id: string;
  sender: 'user' | 'ai' | 'system';
  text: string;
  feedback?: 'like' | 'dislike' | null;
  images?: MessageImage[];
  liveBrowserUrl?: string; // view-only browserless.io LiveURL — see browser_live_view handling
  editProposal?: EditProposal; // diff card for edits proposed to the connected local folder
  // A message sent while Sonic was already working, meant to change what it does next.
  steer?: 'queued' | 'applied' | 'deferred' | 'dropped';
  // Sonic wrote this on its own when the conversation was opened (the "message first" setting), not in reply to anything.
  initiated?: boolean;
  // Set on a reply that is really a failure notice (see chatErrors.ts): the technical line for the "Details" disclosure, whether sending again is
  // worth offering, and what to resend. `userMessageId` is the message that failed, which a retry replaces instead of repeating.
  failure?: { details: string; retryable: boolean; retryText: string; userMessageId: string };
  // One or two short lines the server sends after a reply when something coming up was saved or changed ("Noted: you have a date today with Ashlyn at Tidos, time not known."),
  // so the person can see what was kept and say if it is wrong. Not part of the conversation text and not kept when the page is reloaded.
  noted?: string[];
}

const STEER_STATUS_LABEL: Record<NonNullable<Message['steer']>, string> = {
  queued: "waiting for Sonic's next step",
  applied: 'taken into account',
  deferred: 'will send when this finishes',
  dropped: 'not applied',
};

const IMAGE_EXTENSION_MIME: Record<string, string> = {
  png: 'image/png',
  jpg: 'image/jpeg',
  jpeg: 'image/jpeg',
  gif: 'image/gif',
  webp: 'image/webp',
  bmp: 'image/bmp',
};

function isImageFilename(filename: string): boolean {
  const ext = filename.toLowerCase().split('.').pop() || '';
  return ext in IMAGE_EXTENSION_MIME;
}

function AttachmentImage({ fileId, filename, generated }: { fileId: string; filename: string; generated?: boolean }) {
  const [blobUrl, setBlobUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let objectUrl: string | null = null;

    (async () => {
      try {
        const headers = await getAuthHeaders();
        const response = await fetch(`${BASE_URL}/api/attachments/image/${fileId}`, { headers });
        if (!response.ok) throw new Error(`status ${response.status}`);
        const blob = await response.blob();
        objectUrl = URL.createObjectURL(blob);
        if (!cancelled) setBlobUrl(objectUrl);
      } catch (e) {
        console.error(`Failed to load attachment image ${filename}:`, e);
        if (!cancelled) setFailed(true);
      }
    })();

    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [fileId]);

  // A photo is removed some weeks after it was sent (or when its conversation is deleted); the conversation itself stays.
  if (failed) return <div className="message-attachment-image-loading">This photo is no longer stored.</div>;
  if (!blobUrl) return <div className="message-attachment-image-loading">Loading image…</div>;
  if (generated) {
    return (
      <div className="message-card">
        <img src={blobUrl} alt={filename} className="message-card-image" />
        <a className="message-image-download" href={blobUrl} download={filename}>Download</a>
      </div>
    );
  }
  return <img src={blobUrl} alt={filename} className="message-attachment-image" />;
}

function mapServerMessage(m: any): Message {
  const attachmentImages: MessageImage[] = Array.isArray(m.attachments)
    ? m.attachments.map((a: any) => ({ filename: a.filename, fileId: a.gridfs_id }))
    : [];
  // kb_images (persisted on the AI message when a retrieved KB doc had a renderable image)
  // already uses the {filename, fileId} shape directly — see collect_kb_images in app_utils.py.
  const kbImages: MessageImage[] = Array.isArray(m.kb_images) ? m.kb_images : [];
  const images = [...attachmentImages, ...kbImages];

  return {
    id: crypto.randomUUID(),
    sender: m.type === 'human' ? 'user' : 'ai',
    text: m.content,
    images: images.length > 0 ? images : undefined,
    initiated: m.initiated === true ? true : undefined,
  };
}

// Only the newest page of a conversation is fetched and rendered up front — every message is a full
// ReactMarkdown + syntax-highlighter render, so a long thread used to freeze the page on load.
// Older pages come in on demand. The localStorage copy (rewritten as the reply streams) is capped
// to the same window; the server keeps the full transcript.
const HISTORY_PAGE_SIZE = 100;
const LOCAL_CACHE_MESSAGE_CAP = 100;
const LOCAL_CACHE_WRITE_DEBOUNCE_MS = 400;

interface ChatMessageListProps {
  messages: Message[];
  earlierCount: number;
  loadingEarlier: boolean;
  loadEarlierRef: React.MutableRefObject<() => void>;
  hasChatted: boolean;
  loading: boolean;
  agentStatus: string;
  latestStepTitle: string;
  theme: "sonic" | "shadow";
  isEmbedded: boolean;
  chatWindowRef: React.RefObject<HTMLDivElement | null>;
  messagesEndRef: React.RefObject<HTMLDivElement | null>;
  attachmentsRef: React.MutableRefObject<{ filename: string; content: string }[]>;
  handleSendMessageRef: React.MutableRefObject<(text: string, attachments: { filename: string; content: string }[], replaceIds?: string[]) => void>;
  handleFeedbackRef: React.MutableRefObject<(messageId: string, choice: 'like' | 'dislike') => void>;
  getTokenRef: React.MutableRefObject<(() => Promise<string | null>) | undefined>;
  canEditLocal: boolean;
  localEditActionsRef: React.MutableRefObject<{
    apply: (proposal: EditProposal) => Promise<ApplyResult>;
    undo: (proposalId: string) => Promise<UndoResult>;
  }>;
}

const HighlightedCode = lazy(() => import('../components/HighlightedCode'));

// Fenced code blocks only (react-markdown routes inline `code` spans through a separate,
// much simpler branch below) — syntax-highlighted, with a copy button, matching the app's
// existing dark slate palette instead of the highlighter's own default background.
function CodeBlock({ language, code }: { language: string; code: string }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch (err) {
      console.error('Copy failed:', err);
    }
  };

  return (
    <div style={{ position: 'relative', margin: '0.5rem 0' }}>
      <button
        type="button"
        onClick={handleCopy}
        style={{
          position: 'absolute',
          top: '6px',
          right: '6px',
          zIndex: 1,
          padding: '2px 8px',
          borderRadius: '6px',
          background: 'rgba(51, 65, 85, 0.9)',
          border: '1px solid #475569',
          color: '#cbd5e1',
          fontSize: '0.7rem',
          cursor: 'pointer',
        }}
      >
        {copied ? 'Copied!' : 'Copy'}
      </button>
      {/* The highlighter is a large file, fetched the first time a reply has code in it; the plain code shows at once, in the same box, until it arrives. */}
      <Suspense
        fallback={
          <pre style={{ ...CODE_BOX_STYLE, padding: '1em', overflowX: 'auto', color: '#d4d4d4', fontFamily: 'Consolas, Monaco, monospace' }}>{code}</pre>
        }
      >
        <HighlightedCode language={language} code={code} />
      </Suspense>
    </div>
  );
}

// Memoized so typing in the message input (which only changes unrelated `input` state in the
// parent) doesn't force every past message to re-render and re-parse through ReactMarkdown on
// every keystroke — that cost scales with conversation length and was the actual cause of chat
// lag getting worse over a long conversation. Handlers are taken as refs (kept fresh by the
// parent every render — see the useEffect next to handleFeedback's definition) rather than as
// direct function props, so a skipped re-render here never risks calling a stale closure over
// sessionId/principal/etc — the same class of bug fixed earlier for attachmentsRef.
const ChatMessageList = React.memo(function ChatMessageList({
  messages, earlierCount, loadingEarlier, loadEarlierRef, hasChatted, loading, agentStatus, latestStepTitle, theme, isEmbedded,
  chatWindowRef, messagesEndRef, attachmentsRef, handleSendMessageRef, handleFeedbackRef, getTokenRef,
  canEditLocal, localEditActionsRef,
}: ChatMessageListProps) {
  if (!hasChatted) return null;

  return (
    <div
      className="chat-window"
      ref={chatWindowRef}
      // Standalone: it fills whatever the page column leaves above the input (components/__styles__/AppShell.css). The embedded widget keeps its fixed cap.
      style={isEmbedded ? undefined : { minHeight: 0, overflowY: 'auto' }}
    >
      {earlierCount > 0 && (
        <button
          type="button"
          onClick={() => loadEarlierRef.current()}
          disabled={loadingEarlier}
          style={{
            alignSelf: 'center',
            margin: '0.25rem auto 0.75rem',
            padding: '4px 14px',
            borderRadius: '999px',
            background: 'var(--bg-surface)',
            border: '1px solid var(--border-color)',
            color: 'var(--text-main)',
            fontSize: '0.8rem',
            cursor: loadingEarlier ? 'default' : 'pointer',
          }}
        >
          {loadingEarlier ? 'Loading…' : `Load earlier messages (${earlierCount} more)`}
        </button>
      )}
      {messages
        .filter(msg => !(hasChatted && msg.sender === 'system'))
        .map(msg => {
          // Extract follow-up tag and strip it from the displayed message text
          const { cleanContent, followUp } = msg.sender === 'ai'
            ? parseFollowUp(msg.text)
            : { cleanContent: msg.text, followUp: null };

          return (
            <div key={msg.id} className={`message-bubble ${msg.sender}`}>
              <div className="message-sender">
                {msg.steer ? `STEERING · ${STEER_STATUS_LABEL[msg.steer]}` : msg.sender.toUpperCase()}
              </div>
              {msg.initiated && (
                <div className="message-initiated-note">Sonic started this. You can turn that off in the settings menu.</div>
              )}
              {msg.images && msg.images.length > 0 && (
                <div className="message-attachment-images">
                  {msg.images.map((img, idx) =>
                    img.dataUrl ? (
                      <img key={idx} src={img.dataUrl} alt={img.filename} className="message-attachment-image" />
                    ) : img.fileId ? (
                      <AttachmentImage key={idx} fileId={img.fileId} filename={img.filename} generated={!!img.generated} />
                    ) : null
                  )}
                </div>
              )}
              {msg.liveBrowserUrl && (
                <div className="message-live-browser">
                  <div className="message-live-browser-label">🔴 Watching live — view only</div>
                  <iframe
                    src={msg.liveBrowserUrl}
                    title="Live browser session"
                    className="message-live-browser-frame"
                    sandbox="allow-scripts allow-same-origin"
                  />
                </div>
              )}
              <div className="message-text">
                <ReactMarkdown
                  remarkPlugins={[remarkGfm]}
                  components={{
                    // A language-less fenced block (bare ```) gets no className at all —
                    // identical to a true inline `code` span — so neither className nor
                    // content (a single-line fenced block has no internal newline either)
                    // reliably tells them apart from inside `code` itself. `pre`, though, is
                    // structurally guaranteed by the CommonMark AST to appear only for a
                    // fenced block and never for an inline span (verified directly), so it
                    // does the block/inline split instead, reaching into the `code` element
                    // it wraps (already rendered by the branch below) for the language + text.
                    code: ({ children, ...rest }: any) => (
                      <code {...rest} className="reply-code">
                        {children}
                      </code>
                    ),
                    pre: ({ children }: any) => {
                      const codeEl = children as { props?: { className?: string; children?: React.ReactNode } };
                      const match = /language-(\w+)/.exec(codeEl?.props?.className || '');
                      const raw = String(codeEl?.props?.children ?? '').replace(/\n$/, '');
                      return <CodeBlock language={match ? match[1] : 'text'} code={raw} />;
                    },
                    a: ({ href, children, node, ...rest }: any) => {
                      const finalHref = href?.startsWith('/')
                        ? `${BASE_URL.replace(/\/$/, '')}${href}`
                        : href;
                      const isDownloadLink = finalHref?.includes('/api/documents/download/');
                      const normalizedLabel = React.Children.toArray(children)
                        .map((child) => (typeof child === 'string' ? child : ''))
                        .join(' ')
                        .replace(/\s+/g, ' ')
                        .trim();

                      const handleClick = async (e: React.MouseEvent) => {
                        if (isDownloadLink && finalHref) {
                          e.preventDefault();
                          const newTab = window.open('', '_blank');
                          if (newTab) {
                            newTab.document.write('<html><body style="background: #121824; color: #fff; font-family: sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0;"><h3>Loading secure document preview...</h3></body></html>');
                          }
                          try {
                            let token = null;
                            const isGuest = ['guest', 'guest_bty'].includes(localStorage.getItem('principal') || '');
                            if (isGuest) {
                              token = localStorage.getItem('guest_token');
                            } else if (getTokenRef.current) {
                              token = await getTokenRef.current();
                            }
                            const response = await fetch(finalHref, {
                              headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}) },
                            });
                            if (!response.ok) throw new Error(`Server responded with status ${response.status}`);
                            const blob = await response.blob();
                            const blobUrl = window.URL.createObjectURL(blob);
                            if (newTab) newTab.location.href = blobUrl;
                            else window.open(blobUrl, '_blank');
                          } catch (err) {
                            console.error("Document preview failed:", err);
                            if (newTab) newTab.close();
                            alert("Failed to load document. Please check your session.");
                          }
                        }
                      };

                      return (
                        <a
                          {...rest}
                          href={finalHref}
                          onClick={handleClick}
                          target="_blank"
                          rel="noopener noreferrer"
                          className={isDownloadLink ? 'citation-link citation-link-document' : 'citation-link'}
                          style={{ cursor: 'pointer' }}
                        >
                          {normalizedLabel || children}
                        </a>
                      );
                    },
                  }}
                >
                  {cleanContent}
                </ReactMarkdown>
              </div>

              {msg.failure && (
                <div className="message-failure">
                  {msg.failure.retryable && (
                    <button
                      type="button"
                      className="message-failure-retry"
                      disabled={loading}
                      onClick={() => handleSendMessageRef.current(msg.failure!.retryText, attachmentsRef.current, [msg.failure!.userMessageId, msg.id])}
                    >
                      Try again
                    </button>
                  )}
                  <details className="message-failure-details">
                    <summary>Details</summary>
                    <code>{msg.failure.details}</code>
                  </details>
                </div>
              )}

              {msg.editProposal && (
                <LocalEditCard
                  proposal={msg.editProposal}
                  canApply={canEditLocal}
                  onApply={proposal => localEditActionsRef.current.apply(proposal)}
                  onUndo={proposalId => localEditActionsRef.current.undo(proposalId)}
                />
              )}

              {msg.noted && msg.noted.length > 0 && (
                <div className="message-noted" role="status">
                  {msg.noted.map((line, index) => <div key={index}>{line}</div>)}
                </div>
              )}

              {/* Clickable Follow-Up Suggestion Button */}
              {msg.sender === 'ai' && followUp && !loading && (
                <div className="follow-up-container" style={{ marginTop: '10px' }}>
                  <button
                    type="button"
                    onClick={() => handleSendMessageRef.current(followUp, attachmentsRef.current)}
                    className="follow-up-btn"
                    style={{
                      background: 'var(--follow-up-bg)',
                      border: '1px solid rgba(6, 182, 212, 0.4)',
                      color: '#22d3ee',
                      borderRadius: '16px',
                      padding: '6px 14px',
                      fontSize: '0.85rem',
                      cursor: 'pointer',
                      display: 'inline-flex',
                      alignItems: 'center',
                      gap: '6px',
                      transition: 'all 0.2s ease',
                    }}
                  >
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#fb923c" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" style={{ flex: 'none' }}>
                      <line x1="12" y1="6" x2="12" y2="3.6" />
                      <circle cx="12" cy="2.6" r="1.6" fill="#fb923c" stroke="none" />
                      <rect x="1.2" y="11" width="2" height="4.5" rx="1" fill="#fb923c" stroke="none" />
                      <rect x="20.8" y="11" width="2" height="4.5" rx="1" fill="#fb923c" stroke="none" />
                      <rect x="4" y="6" width="16" height="14" rx="4" />
                      <rect x="6.8" y="8.6" width="10.4" height="8.8" rx="2.2" fill="#fb923c" fillOpacity="0.15" stroke="none" />
                      <circle cx="9.5" cy="12.2" r="1.3" fill="#fb923c" stroke="none" />
                      <circle cx="14.5" cy="12.2" r="1.3" fill="#fb923c" stroke="none" />
                      <path d="M10 15.2 Q12 16.9 14 15.2" strokeWidth="1.4" />
                    </svg>
                    <span>{followUp}</span>
                  </button>
                </div>
              )}

              {/* Feedback Buttons */}
              {msg.sender === 'ai' && !loading && msg.text && (
                <div className="feedback-actions" style={{ display: 'flex', gap: '6px', marginTop: '8px' }}>
                  <button
                    type="button"
                    onClick={() => handleFeedbackRef.current(msg.id, 'like')}
                    className={`circle-icon-button ${msg.feedback === 'like' ? 'active' : ''}`}
                    title="Helpful"
                    style={msg.feedback === 'like' ? { color: '#22c55e', borderColor: '#22c55e', background: '#22c55e' } : {}}
                  >
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z" />
                    </svg>
                  </button>

                  <button
                    type="button"
                    onClick={() => handleFeedbackRef.current(msg.id, 'dislike')}
                    className={`circle-icon-button ${msg.feedback === 'dislike' ? 'active' : ''}`}
                    title="Not helpful"
                    style={msg.feedback === 'dislike' ? { color: '#ef4444', borderColor: '#ef4444' } : {}}
                  >
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M12 21.23l-7.78-7.78a5.5 5.5 0 0 1 0-7.78L12 5.67l1.06-1.06a5.5 5.5 0 0 1 7.78 7.78L12 21.23z" />
                      <path d="M12 5.67v15.56" />
                    </svg>
                  </button>
                </div>
              )}
            </div>
          );
        })}

      {/* Live Node Steps & Spinning Animation Container */}
      {loading && (
        isEmbedded ? (
          <div className="bty-loader-container">
            <div className="bty-loader-dots">
              <span></span>
              <span></span>
              <span></span>
            </div>
            <div className="loading-text">
              {latestStepTitle || "Thinking..."}
            </div>
          </div>
        ) : (
          <div className="sonic-loader-container">
            <div className="sonic-ring-loader"></div>
            {latestStepTitle && <span className="agent-status-text">{latestStepTitle}</span>}
          </div>
        )
      )}
      <div ref={messagesEndRef} />
      
    </div>
  );
});

interface ChatPageProps {
  theme: "sonic" | "shadow";
  toggleTheme: () => void;
}

const parseFollowUp = (content: string) => {
  const match = content.match(/<<<FOLLOW_UP:\s*(.*?)>>>/s);
  if (!match) return { cleanContent: content, followUp: null };

  const cleanContent = content.replace(/<<<FOLLOW_UP:\s*.*?>>>/s, '').trim();
  const followUp = match[1].trim();
  return { cleanContent, followUp };
};

const getNodeLabel = (nodeName: string): string => {
  switch (nodeName) {
    case 'coordinator_node': return 'Analyzing intent and planning route...';
    case 'reasoner_node': return 'Evaluating request context...';
    case 'memory_node': return 'Updating memory and preferences...';
    case 'retrieve_node': return 'GraphRAG Retrieval in progress...';
    case 'grade_documents_node': return 'Evaluating document relevance...';
    case 'rewrite_query_node': return 'Refining search parameters...';
    case 'summarizer_node': return 'Summarizing retrieved documents...';
    case 'formatter_node': return 'Writing the answer...';
    case 'conversational_node': return 'Generating response...';
    case 'generate_node': return 'Collecting rings and generating tokens...';
    case 'web_search_node': return 'Searching the web for real-time info...';
    case 'code_interpreter_node': return 'Executing query in code interpreter...';
    case 'github_search': return 'Searching GitHub repositories...';
    case 'pr_summary': return 'Analyzing pull request changes...';
    case 'draft_pr_node': return 'Drafting pull request...';
    case 'execute_pr_node': return 'Executing pull request action...';
    case 'reward_evaluator': return 'Reconsidering that answer...';
    default: return nodeName ? `Processing step: ${nodeName}` : 'Collecting rings and tokens...';
  }
};

export const ChatPage: React.FC<ChatPageProps> = ({ theme, toggleTheme }) => {
  const hashSearch = window.location.hash.includes('?') ? window.location.hash.split('?')[1] : '';
  const searchParams = new URLSearchParams(window.location.search || hashSearch);
  const isEmbedded = searchParams.get('mode') === 'embed';
  const [tracePanelPos, setTracePanelPos] = useState({ x: 0, y: 0 });
  const [isDraggingTrace, setIsDraggingTrace] = useState(false);
  const traceDragRef = useRef({ startX: 0, startY: 0, initialX: 0, initialY: 0 });
  // Trace is available by default in standalone mode, but starts hidden when embedded.

  const [showTracePanel, setShowTracePanel] = useState(() => {
    if (isEmbedded) return false;
    if (typeof window !== 'undefined') {
      const saved = localStorage.getItem('showTracePanel');
      if (saved !== null) return JSON.parse(saved);
    }
    // The trace is a blade beside the conversation (it costs width), so with no saved choice it starts open only on a wide screen.
    return typeof window === 'undefined' || window.innerWidth >= 1400;
  });
  const isMobileView = useMediaQuery('(max-width: 768px)');

  const toggleTracePanel = () => {
    setShowTracePanel((prev: boolean) => {
      const next = !prev;
      localStorage.setItem('showTracePanel', JSON.stringify(next));
      return next;
    });
  };
  const [traceSteps, setTraceSteps] = useState<TraceStep[]>([]);
  // A step's facts can arrive before the step is drawn (steps are replayed with a delay by processNodeQueue), so they are kept by step id
  // and merged in when rendering rather than attached to a step that may not exist yet.
  const [traceFacts, setTraceFacts] = useState<TraceFactsById>({});
  const tracePanelSteps = useMemo(() => mergeFacts(traceSteps, traceFacts), [traceSteps, traceFacts]);
  const { isLoaded, isSignedIn, getToken } = useAuth();
  const principal = getEffectivePrincipal();
  const [selectedAffiliate, setSelectedAffiliate] = useState<string>('All');
  const [ragMode, setRagMode] = useState<string>('open');
  const [deepThinking, setDeepThinking] = useState<boolean>(false);
  const [toolModel, setToolModel] = useState<'gemini' | 'claude'>('gemini');
  const [toolModelAvailable, setToolModelAvailable] = useState<boolean>(false);
  const [proactiveOpening, setProactiveOpening] = useState<boolean>(false);
  const [opsDigest, setOpsDigest] = useState<{ enabled: boolean; available: boolean }>({ enabled: false, available: false });
  const [budget, setBudget] = useState<BudgetView | null>(null);
  const openingRequestedRef = useRef<string | null>(null);
  const [allowedAffiliates, setAllowedAffiliates] = useState<KnowledgeBase[]>([]);
  const { fetchingScope } = useAffiliateScope({
    enabled: !isEmbedded,
    selectedAffiliate,
    setSelectedAffiliate,
    setAllowedAffiliates,
  });
  // Connected local folder: Sonic reads this instead of GitHub's copy. Not offered to guests (every
  // guest shares one identity) or in embed mode.
  const workspace = useLocalWorkspace({ enabled: !isEmbedded && !isGuestPrincipal(principal) });
  const [agentStatus, setAgentStatus] = useState<string>('');
  const [agentPath, setAgentPath] = useState<string[]>([]);
  const [attachedFiles, setAttachedFiles] = useState<File[]>([]);
  const [attachNotice, setAttachNotice] = useState('');   // why a file was not attached (a photo format the browser cannot read)
  const embedVisitorParam = searchParams.get('visitor_id') || searchParams.get('user_id') || searchParams.get('uid');
  const shouldResetEmbedChat = ['1', 'true', 'yes'].includes(
    (searchParams.get('new_user') || searchParams.get('fresh') || searchParams.get('reset') || '').toLowerCase()
  );
  const embedAffiliate = 'Affiliate_D';
  const [embedVisitorId] = useState<string>(() => {
    if (!isEmbedded) return '';
    const explicitVisitor = (embedVisitorParam || '').trim();
    if (explicitVisitor) {
      sessionStorage.setItem('bty-embed-visitor-id', explicitVisitor);
      return explicitVisitor;
    }
    const existingVisitor = sessionStorage.getItem('bty-embed-visitor-id');
    if (existingVisitor) return existingVisitor;
    const generatedVisitor = crypto.randomUUID();
    sessionStorage.setItem('bty-embed-visitor-id', generatedVisitor);
    return generatedVisitor;
  });
  const [sessionId, setSessionId] = useState<string>(() => {
    if (isEmbedded) {
      const explicitSession = (searchParams.get('session_id') || '').trim();
      if (explicitSession) {
        sessionStorage.setItem('bty-embed-session-id', explicitSession);
        return explicitSession;
      }
      const existingSession = sessionStorage.getItem('bty-embed-session-id');
      if (existingSession) return existingSession;
      const generatedSession = crypto.randomUUID();
      sessionStorage.setItem('bty-embed-session-id', generatedSession);
      return generatedSession;
    }
    // Non-embedded: persist the active conversation id across reloads instead of
    // minting a fresh throwaway one on every page load.
    const conversationKey = `conversation-id-${principal}`;
    const existingConversation = localStorage.getItem(conversationKey);
    if (existingConversation) return existingConversation;
    const generatedConversation = crypto.randomUUID();
    localStorage.setItem(conversationKey, generatedConversation);
    return generatedConversation;
  });

  const chatStorageKey = isEmbedded
    ? `chat-messages-${principal}-${embedVisitorId}`
    : `chat-messages-${principal}-${sessionId}`;

  useEffect(() => {
    if (isEmbedded) {
      setSelectedAffiliate(embedAffiliate);
    }
  }, [isEmbedded]);
  // const BASE_URL = "https://saapp.onrender.com/";
  const [feedbackModal, setFeedbackModal] = useState<{ isOpen: boolean; messageId: string | null }>({
    isOpen: false,
    messageId: null,
  });
  const [feedbackReason, setFeedbackReason] = useState<string>('');
  const [feedbackTag, setFeedbackTag] = useState<string>('hallucination');
  const [showClearConfirm, setShowClearConfirm] = useState(false);
  const [showFolderConsent, setShowFolderConsent] = useState(false);
  const [conversationsRefreshKey, setConversationsRefreshKey] = useState(0);
  const [isMobileTraceOpen, setIsMobileTraceOpen] = useState(false);
  const [latestStepTitle, setLatestStepTitle] = useState("");
  const nodeQueueRef = useRef<{ node: string; detail?: string; batchIndex?: number; batchSize?: number; stepId?: string; tool?: string; subject?: string }[]>([]);
  const isProcessingQueue = useRef<boolean>(false);
  // Cheap alternative to full mid-thought steering (see docs/coding-agent-roadmap.md): lets the
  // user cut a turn short the moment it's clearly going down the wrong path, rather than waiting
  // out the whole response. Deliberately just "stop" rather than "inject a correction mid-loop" —
  // the real conversation-history threading added earlier this session means a follow-up message
  // still lands with genuine context about what was interrupted, without needing any new
  // concurrency/signaling infrastructure between an in-flight request and a new one.
  const abortControllerRef = useRef<AbortController | null>(null);
  // Messages sent to steer the turn that is running. `applied` flips when the backend reports it
  // folded them into the agent's work; whatever is still unapplied when the turn ends is re-sent
  // as an ordinary message so a steer is never lost.
  const steerLogRef = useRef<{ id: string; text: string; applied: boolean }[]>([]);
  const pendingResendRef = useRef<{ id: string; text: string }[]>([]);
  const [resendTick, setResendTick] = useState(0);
  // Bumped each time a turn finishes normally, so the pixel Patchy by the input can hop.
  const [patchyCheer, setPatchyCheer] = useState(0);
  // Bumped when a turn fails (his face turns sorry instead of celebrating), and when a message is steered into a running turn (he nods).
  const [patchyFault, setPatchyFault] = useState(0);
  const [patchySteer, setPatchySteer] = useState(0);
  // Whether the person is engaged with the message box (clicked into it or typed, and hasn't sent or clicked away): Patchy turns to watch it, and reads
  // along once there is text in it. Plain focus doesn't count: the page focuses the box itself on load, and it stays focused between turns.
  const [inputEngaged, setInputEngaged] = useState(false);
  // Patchy is on by default; the "..." menu can hide him. Remembered per browser (storage can be
  // blocked, so a failed read/write just means the default / a non-sticky choice).
  const [showPatchy, setShowPatchy] = useState<boolean>(() => {
    try {
      return localStorage.getItem('showPatchy') !== 'false';
    } catch {
      return true;
    }
  });
  // The energy ceiling the backend says is in force for this conversation (it follows the user's own
  // emotional state); Patchy's mood mirrors it. Resets when the conversation does.
  const [patchyEnergy, setPatchyEnergy] = useState<PatchyEnergy>('open');
  // The usage budget as Patchy shows it: a sweat drop at the notice level, heavy lids when throttled, asleep when chat is paused.
  const patchyBudget = budgetFromLevel(budget?.level);
  const togglePatchy = () => {
    setShowPatchy(prev => {
      const next = !prev;
      try { localStorage.setItem('showPatchy', String(next)); } catch { /* not persisted */ }
      return next;
    });
  };

  const [showTooltip, setShowTooltip] = useState(false);
  const tooltipRef = useRef<HTMLDivElement>(null);
  const [showOverflowWheel, setShowOverflowWheel] = useState(false);
  const [showScopePicker, setShowScopePicker] = useState(false);

  const [targetRepo, setTargetRepo] = useState<string>("");
  const [showRepoBanner, setShowRepoBanner] = useState(false);
  const [repoInput, setRepoInput] = useState("");
  const [repoSaving, setRepoSaving] = useState(false);

  const [showConversations, setShowConversations] = useState(false);
  const [conversationsClosing, setConversationsClosing] = useState(false);

  const toggleConversationsBlade = () => {
    if (showConversations) {
      setConversationsClosing(true);
      setTimeout(() => {
        setShowConversations(false);
        setConversationsClosing(false);
      }, 250);
    } else {
      setShowConversations(true);
    }
  };
  const handleTraceMouseDown = (e: React.MouseEvent) => {
  // Ignore clicks on buttons inside the header
  if ((e.target as HTMLElement).closest('.circle-icon-button')) return;

  setIsDraggingTrace(true);
  traceDragRef.current = {
    startX: e.clientX,
    startY: e.clientY,
    initialX: tracePanelPos.x,
    initialY: tracePanelPos.y,
  };
};

useEffect(() => {
  const handleMouseMove = (e: MouseEvent) => {
    if (!isDraggingTrace) return;
    
    const dx = e.clientX - traceDragRef.current.startX;
    const dy = e.clientY - traceDragRef.current.startY;
    
    setTracePanelPos({
      x: traceDragRef.current.initialX + dx,
      y: traceDragRef.current.initialY + dy,
    });
  };

  const handleMouseUp = () => {
    setIsDraggingTrace(false);
  };

  if (isDraggingTrace) {
    window.addEventListener('mousemove', handleMouseMove);
    window.addEventListener('mouseup', handleMouseUp);
  }

  return () => {
    window.removeEventListener('mousemove', handleMouseMove);
    window.removeEventListener('mouseup', handleMouseUp);
  };
}, [isDraggingTrace]);
  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (tooltipRef.current && !tooltipRef.current.contains(event.target as Node)) {
        setShowTooltip(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const genId = () => crypto.randomUUID();
  const [uploadedFiles, setUploadedFiles] = useState<File[]>([]);
  
  const [messages, setMessages] = useState<Message[]>(() => {
    if (isEmbedded && shouldResetEmbedChat) {
      localStorage.removeItem(chatStorageKey);
    }
    const persistedHistory = localStorage.getItem(chatStorageKey);
    if (persistedHistory) {
      try {
        return JSON.parse(persistedHistory);
      } catch (e) {
        console.error("Failed to parse persisted conversation logs:", e);
      }
    }
    return [
      { id: genId(), sender: 'system', text: `What would you like to find out about, ${principal}?` }
    ];
  });

  const [hasChatted, setHasChatted] = useState<boolean>(() => {
    try {
      const persisted = localStorage.getItem(chatStorageKey);
      if (persisted) {
        const parsed: Message[] = JSON.parse(persisted);
        return parsed.some(m => m.sender === 'user');
      }
    } catch {
      // fall through
    }
    return messages.some(msg => msg.sender === 'user');
  });

  // The localStorage-hydrated state above is per-device and can go stale the moment another
  // device sends a message into the same conversation. Reconcile with the server's canonical
  // copy once we know which conversation we're on, so switching devices doesn't show an
  // outdated transcript. A 404 here just means this conversation hasn't been saved server-side
  // yet (e.g. brand new) — keep whatever's already showing locally in that case.
  // `historyStart` = how many saved messages sit before the first one loaded (0 = whole thread shown).
  const [historyStart, setHistoryStart] = useState<number>(0);
  const [loadingEarlier, setLoadingEarlier] = useState<boolean>(false);
  const skipAutoScrollRef = useRef(false);
  const sessionIdRef = useRef(sessionId);
  sessionIdRef.current = sessionId;

  useEffect(() => {
    if (!principal || !sessionId) return;
    let cancelled = false;
    api.getConversation(sessionId, { limit: HISTORY_PAGE_SIZE })
      .then((conversation) => {
        if (cancelled) return;
        const serverMessages: Message[] = (conversation.messages || [])
          .filter((m: any) => m.type !== 'system')
          .map(mapServerMessage);
        if (serverMessages.length > 0) {
          setMessages(serverMessages);
          setHistoryStart(conversation.start ?? 0);
          setHasChatted(serverMessages.some(m => m.sender === 'user'));
          // Opening an existing conversation: Sonic may have one anchored thing to say first (usually it doesn't, and
          // never unless the user turned that on). Asked once per conversation load.
          if (!isEmbedded && openingRequestedRef.current !== sessionId) {
            openingRequestedRef.current = sessionId;
            api.requestConversationOpening(sessionId)
              .then((result) => {
                if (cancelled || !result.message) return;
                setMessages(prev => [...prev, mapServerMessage(result.message)]);
              })
              .catch(() => { /* no opening message is the normal outcome */ });
          }
        }
      })
      .catch(() => {
        // Not saved server-side yet — nothing to reconcile.
      });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId, principal]);

  const [input, setInput] = useState<string>('');

  // A page that wants to hand the chat a question (the Investigate button on Sonic Health) passes it as `draft`. It only fills the
  // box: nothing is sent until the person presses send. The state is cleared so a refresh doesn't fill it again.
  const routeLocation = useLocation();
  const routeNavigate = useNavigate();
  useEffect(() => {
    const draft = (routeLocation.state as { draft?: unknown } | null)?.draft;
    if (typeof draft === 'string' && draft.trim()) {
      setInput(draft);
      routeNavigate(routeLocation.pathname, { replace: true, state: null });
    }
  }, [routeLocation.state, routeLocation.pathname, routeNavigate]);
  const meetingStartedRef = useRef<(name: string) => void>(() => {});
  const [loading, setLoading] = useState<boolean>(false);
  const [currentExampleQuestions, setCurrentExampleQuestions] = useState<WelcomeCard[]>([]);
  const [loadingCards, setLoadingCards] = useState<boolean>(false);

  // Notify the parent window (BTY SonicWidget) so mini Patchy can animate.
  // No-ops when not embedded in an iframe. '*' target is fine here because the
  // payload carries no sensitive data; the parent origin-checks on receipt.
  const hasStreamedRef = useRef<boolean>(false);
  const postPatchyStatus = (status: 'idle' | 'thinking' | 'streaming' | 'done' | 'error') => {
    if (window.parent === window) return;
    window.parent.postMessage({ source: 'sonic-assistant', patchyStatus: status }, '*');
  };
  const [attachments, setAttachments] = useState<{ filename: string; content: string }[]>([]);
  const attachmentsRef = useRef<{ filename: string; content: string }[]>([]);
  const principalKey = (localStorage.getItem('principal') || '').toLowerCase();
  const guestBtyDetected = principalKey === 'guest_bty' || !!localStorage.getItem('guest_token');
  const affiliateDActive = selectedAffiliate === 'Affiliate_D' || embedAffiliate === 'Affiliate_D';
  const chatPlaceholder = guestBtyDetected
    ? "Ask a Question..."
    : affiliateDActive
      ? 'Ask a question...'
      : 'Ask a question against your isolated data index...';

  const handleRemoveAttachment = (idx: number) => {
    setUploadedFiles(prev => prev.filter((_, i) => i !== idx));
    setAttachments(prev => prev.filter((_, i) => i !== idx));
  };

  useEffect(() => {
    attachmentsRef.current = attachments;
  }, [attachments]);

  const chatWindowRef = useRef<HTMLDivElement>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const prevMessageCountRef = useRef(0);
  const prevLoadingRef = useRef(false);
  
  useEffect(() => {
    if (!isLoaded) return;
    const isGuest = ['guest', 'guest_bty'].includes(localStorage.getItem('principal') || '');
    if (!isGuest && !isSignedIn) return;
  }, [isLoaded, isSignedIn]);

  useEffect(() => {
    const syncUserClaims = async () => {
      if (!principal) return;
      try {
        const data = await api.getAffiliates(principal);
        if (Array.isArray(data)) {
          setAllowedAffiliates(data);
        }
      } catch (err) {
        console.error("Failed to sync user claims:", err);
      }
    };
    syncUserClaims();
  }, [principal]);

  useEffect(() => {
    if (!principal) return;
    api.getRagMode()
      .then(data => setRagMode(data.rag_mode))
      .catch(err => console.error("Failed to fetch RAG mode setting:", err));
  }, [principal]);

  const handleRagModeChange = async (mode: string) => {
    setRagMode(mode);
    try {
      const result = await api.updateRagMode(mode);
      setRagMode(result.rag_mode);
    } catch (err) {
      console.error("Failed to update RAG mode setting:", err);
    }
  };

  useEffect(() => {
    if (!principal) return;
    api.getDeepThinking()
      .then(data => setDeepThinking(data.deep_thinking))
      .catch(err => console.error("Failed to fetch deep thinking setting:", err));
  }, [principal]);

  useEffect(() => {
    if (!principal) return;
    api.getToolModel()
      .then(data => { setToolModel(data.tool_model); setToolModelAvailable(data.available); })
      .catch(() => { /* guests and embeds have no such setting */ });
  }, [principal]);

  useEffect(() => {
    if (!principal) return;
    api.getProactiveOpening()
      .then(data => setProactiveOpening(data.enabled))
      .catch(() => { /* guests and embeds have no such setting */ });
  }, [principal]);

  // The usage budget status (backend/utils/budget_guard.py): read on load, pushed by the server when a turn starts under a notice,
  // and read again each time a reply finishes. Hidden in the embedded view, which keeps its own layout.
  const refreshBudget = React.useCallback(() => {
    if (!principal || isEmbedded) return;
    api.getBudget().then(setBudget).catch(() => { /* no budget endpoint here, or signed out: nothing to show */ });
  }, [principal, isEmbedded]);

  useEffect(() => {
    refreshBudget();
  }, [refreshBudget]);

  useEffect(() => {
    if (!loading) refreshBudget();
  }, [loading, refreshBudget]);

  useEffect(() => {
    if (!principal) return;
    api.getOpsDigest()
      .then(setOpsDigest)
      .catch(() => { /* not an admin, or no such setting here: the menu item stays hidden */ });
  }, [principal]);

  const handleOpsDigestChange = async (enabled: boolean) => {
    setOpsDigest(prev => ({ ...prev, enabled }));
    try {
      setOpsDigest(await api.updateOpsDigest(enabled));
    } catch (err) {
      console.error("Failed to update the incident digest setting:", err);
      setOpsDigest(prev => ({ ...prev, enabled: !enabled }));
    }
  };

  const handleProactiveOpeningChange = async (enabled: boolean) => {
    setProactiveOpening(enabled);
    try {
      const result = await api.updateProactiveOpening(enabled);
      setProactiveOpening(result.enabled);
    } catch (err) {
      console.error("Failed to update the message-first setting:", err);
      setProactiveOpening(!enabled);
    }
  };

  const handleToolModelChange = async (next: 'gemini' | 'claude') => {
    const before = toolModel;
    setToolModel(next);
    try {
      const result = await api.updateToolModel(next);
      setToolModel(result.tool_model);
    } catch (err) {
      console.error("Failed to update the code model setting:", err);
      setToolModel(before);
    }
  };

  const handleDeepThinkingChange = async (enabled: boolean) => {
    setDeepThinking(enabled);
    try {
      const result = await api.updateDeepThinking(enabled);
      setDeepThinking(result.deep_thinking);
    } catch (err) {
      console.error("Failed to update deep thinking setting:", err);
    }
  };

  useEffect(() => {
    if (!principal) return;
    api.getTargetRepo()
      .then(data => {
        const repo = data.target_repo || "";
        setTargetRepo(repo);
        setRepoInput(repo);
      })
      .catch(err => console.error("Failed to fetch target repo setting:", err));
  }, [principal]);

  const handleSaveTargetRepo = async () => {
    setRepoSaving(true);
    try {
      const result = await api.updateTargetRepo(repoInput.trim() || null);
      const saved = result.target_repo || "";
      setTargetRepo(saved);
      setRepoInput(saved);
      setShowRepoBanner(false);
    } catch (err) {
      console.error("Failed to update target repo setting:", err);
    } finally {
      setRepoSaving(false);
    }
  };

  const handleClearTargetRepo = async () => {
    setRepoSaving(true);
    try {
      await api.updateTargetRepo(null);
      setTargetRepo("");
      setRepoInput("");
    } catch (err) {
      console.error("Failed to clear target repo setting:", err);
    } finally {
      setRepoSaving(false);
    }
  };

  // Clearing a conversation's messages and deleting it from the conversations panel were always
  // the same operation as far as the backend transcript is concerned — there's no "clear but keep
  // it around" state, so this now calls the real delete endpoint instead of a separate /chat/clear
  // that just wiped the local view while leaving a dead entry behind in the conversations list.
  const confirmClearChat = async () => {
    setShowClearConfirm(false);
    const clearedSessionId = sessionId;
    try {
      await api.deleteConversation(clearedSessionId);
    } catch (e) {
      console.warn("Backend conversation delete was skipped (server offline).", e);
    }
    localStorage.removeItem(chatStorageKey);

    const newId = crypto.randomUUID();
    localStorage.setItem(`conversation-id-${principal}`, newId);
    setSessionId(newId);
    setMessages([
      { id: genId(), sender: 'system', text: `What would you like to find out about, ${principal}?` }
    ]);
    setHistoryStart(0);
    setSelectedAffiliate(isEmbedded ? embedAffiliate : 'All');
    setAgentStatus('');
    setAgentPath([]);
    setHasChatted(false);
    setPatchyEnergy('open');
    setAttachments([]);
    setAttachedFiles([]);
    setConversationsRefreshKey(k => k + 1);
    try {
      const questions = await getWelcomeCards(
      allowedAffiliates.map(a => a.id),
      isEmbedded ? embedAffiliate : 'All',
      principal,
      isEmbedded
    );
    setCurrentExampleQuestions(questions);
    } catch (error) {
      console.error("Failed to fetch dynamic example questions:", error);
    }
  };

  const startNewConversation = () => {
    const newId = crypto.randomUUID();
    localStorage.setItem(`conversation-id-${principal}`, newId);
    setSessionId(newId);
    setMessages([
      { id: genId(), sender: 'system', text: `What would you like to find out about, ${principal}?` }
    ]);
    setHistoryStart(0);
    setHasChatted(false);
    setPatchyEnergy('open');
    setAttachments([]);
    setAttachedFiles([]);
    setShowConversations(false);
  };

  const switchConversation = async (newSessionId: string) => {
    if (newSessionId === sessionId) {
      setShowConversations(false);
      return;
    }
    try {
      const conversation = await api.getConversation(newSessionId, { limit: HISTORY_PAGE_SIZE });
      const restored: Message[] = (conversation.messages || [])
        .filter((m: any) => m.type !== 'system')
        .map(mapServerMessage);
      localStorage.setItem(`conversation-id-${principal}`, newSessionId);
      setSessionId(newSessionId);
      setHistoryStart(conversation.start ?? 0);
      setMessages(restored.length ? restored : [
        { id: genId(), sender: 'system', text: `What would you like to find out about, ${principal}?` }
      ]);
      setHasChatted(restored.some(m => m.sender === 'user'));
      // The carried feeling belongs to the conversation just left; the next message re-reads it.
      setPatchyEnergy('open');
      setAttachments([]);
      setAttachedFiles([]);
    } catch (e) {
      console.error("Failed to load conversation:", e);
    } finally {
      setShowConversations(false);
    }
  };

  // The shell's rail lists the conversations and switches between them, but this page owns the open one: it hands the shell the open conversation and
  // stable ways to switch or start one (components/shellStore.ts), plus the trace button for the top bar.
  const switchRef = useRef(switchConversation);
  const newChatRef = useRef(startNewConversation);
  useEffect(() => {
    switchRef.current = switchConversation;
    newChatRef.current = startNewConversation;
  });
  useEffect(() => {
    if (isEmbedded) return;
    shell.setConversations({
      activeSessionId: sessionId,
      refreshKey: conversationsRefreshKey,
      select: (id: string) => { void switchRef.current(id); },
      newChat: () => newChatRef.current(),
    });
    return () => shell.setConversations(null);
  }, [isEmbedded, sessionId, conversationsRefreshKey]);
  useEffect(() => {
    if (isEmbedded) return;
    const traceOpen = isMobileView ? isMobileTraceOpen : showTracePanel;
    shell.setTopBarActions(
      <>
        {!isGuestPrincipal(principal) && <MeetingControl sessionId={sessionId} onStarted={(name) => meetingStartedRef.current(name)} />}
        <button
          type="button"
          className="topbar-btn"
          onClick={() => (isMobileView ? setIsMobileTraceOpen(v => !v) : toggleTracePanel())}
          aria-pressed={traceOpen}
          aria-label="Toggle execution trace"
          title={traceOpen ? 'Hide execution trace' : 'Show execution trace'}
        >
          <Icon name="microscope" size={18} />
          {loading && !traceOpen && <span className="topbar-btn-dot" aria-hidden="true" />}
        </button>
      </>
    );
    return () => shell.setTopBarActions(null);
  }, [isEmbedded, showTracePanel, isMobileView, isMobileTraceOpen, loading, sessionId, principal]);

  // Prepends the page of saved messages just before the ones on screen. The smooth scroll-to-bottom
  // that normally fires when the message count changes is skipped here, and the viewport is held
  // on the message the user was looking at so the older ones simply appear above it.
  const loadEarlierRef = useRef<() => void>(() => {});
  loadEarlierRef.current = async () => {
    if (loadingEarlier || historyStart <= 0) return;
    const requestedFor = sessionId;
    setLoadingEarlier(true);
    try {
      const page = await api.getConversation(requestedFor, { limit: HISTORY_PAGE_SIZE, before: historyStart });
      if (requestedFor !== sessionIdRef.current) return;
      const earlier: Message[] = (page.messages || [])
        .filter((m: any) => m.type !== 'system')
        .map(mapServerMessage);
      const win = chatWindowRef.current;
      const prevHeight = win?.scrollHeight ?? 0;
      const prevTop = win?.scrollTop ?? 0;
      skipAutoScrollRef.current = true;
      setMessages(prev => [...earlier, ...prev]);
      setHistoryStart(page.start);
      requestAnimationFrame(() => {
        if (win) win.scrollTop = prevTop + (win.scrollHeight - prevHeight);
      });
    } catch (e) {
      console.error("Failed to load earlier messages:", e);
    } finally {
      setLoadingEarlier(false);
    }
  };

  // The "…" button and every popover it opens (the options menu, the scope picker) sit inside this one wrapper, so a press anywhere else closes them. The button counts
  // as inside on purpose: its own click toggles the menu, and closing it here first would have that same click open it again.
  const optionsMenuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!showOverflowWheel && !showScopePicker) return;   // nothing is open, so nothing listens
    const close = () => {
      setShowOverflowWheel(false);
      setShowScopePicker(false);
    };
    const onPointerDown = (e: PointerEvent) => {
      if (!optionsMenuRef.current?.contains(e.target as Node)) close();
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') close();
    };
    document.addEventListener('pointerdown', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('pointerdown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [showOverflowWheel, showScopePicker]);

  const addAttachmentFile = async (original: File) => {
    // Shrunk first (a phone photo is megabytes). The photo then travels once, inside the chat request: a second copy used to go to
    // /api/upload-attachment on its own, with no auth header and in a shape that endpoint rejects, so it only cost the connection.
    const file = await shrinkImageFile(original);
    if (isUnreadableHeic(file)) { setAttachNotice(HEIC_NOTICE); return; }
    setAttachNotice('');
    const reader = new FileReader();
    reader.onload = () => {
      const base64 = (reader.result as string).split(",")[1];
      // All three together, so the pills and the payload stay in the same order whichever file finishes first.
      setUploadedFiles(prev => [...prev, file]);
      setAttachedFiles(prev => [...prev, file]);
      setAttachments(prev => [...prev, { filename: file.name, content: base64 }]);
    };
    reader.readAsDataURL(file);
  };

  // A card is a question to send, except one that needs something from the person first (a photo of a letter): that one opens the file picker, from
  // inside this click so a phone allows it, and leaves a question ready so that attaching is all that is left before sending.
  const chooseWelcomeCard = (card: WelcomeCard) => {
    if (card.action === 'open' && card.path) {
      routeNavigate(card.path);
      return;
    }
    if (card.action === 'attach') {
      if (card.prompt) setInput(card.prompt);
      document.getElementById('file-upload')?.click();
      return;
    }
    handleSendMessage(card.text, attachmentsRef.current);
  };

  const handleFileUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files) return;
    Array.from(files).forEach(addAttachmentFile);
  };

  const inputRef = useRef<HTMLTextAreaElement>(null);
  const handlePastedImage = (e: React.ClipboardEvent<HTMLTextAreaElement>) => {
    const items = e.clipboardData?.items;
    if (!items) return;

    Array.from(items).forEach((item) => {
      if (!item.type.startsWith("image/")) return;
      const file = item.getAsFile();
      if (!file) return;

      // Clipboard image files typically come back with a generic/blank name (e.g. "image.png")
      // regardless of actual format, so synthesize one from the MIME type instead of trusting
      // file.name — the backend's is_image_attachment() check keys off the file extension.
      const ext = item.type.split("/")[1] || "png";
      const pastedFile = new File([file], `pasted-image-${Date.now()}.${ext}`, { type: file.type });
      addAttachmentFile(pastedFile);
    });
  };

  const handleExportChat = () => {
    const transcript = messages
      .filter(msg => msg.sender !== 'system')
      .map(msg => `[${msg.sender.toUpperCase()}] (${new Date().toLocaleTimeString()})\n${msg.text}`)
      .join("\n\n----------------------------------------\n\n");
    if (!transcript.trim()) return;
    const blob = new Blob([`# Secure RAG Chat Session: ${principal}\n\n${transcript}`], { type: 'text/markdown;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `rag_chat_session_${principal}_${new Date().toISOString().slice(0, 10)}.md`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  };

  useEffect(() => {
    if (inputRef.current) {
      inputRef.current.focus();
    }
  }, []);
  // This effect runs on every streamed token, and serializing a whole long thread each time is
  // what made replies stutter. Write only the newest window, at most once per debounce interval,
  // and flush any pending write when the tab is hidden or the component unmounts so nothing is lost.
  //
  // A photo's instant preview is the whole image as base64 text (a ~1 MB phone photo is ~1.5 MB of
  // string), which on its own can exceed a phone browser's ~5 MB localStorage limit. Before the
  // write was guarded, that threw inside this effect and blanked the entire page on send, while the
  // message itself still went through. Previews are therefore never cached; the saved copy comes
  // back from the server with a durable fileId when the conversation next loads.
  useEffect(() => {
    const write = () => {
      try {
        const cacheable = messages.slice(-LOCAL_CACHE_MESSAGE_CAP).map(m =>
          m.images
            ? { ...m, images: m.images.map(img => ({ filename: img.filename, fileId: img.fileId, generated: img.generated })) }
            : m
        );
        localStorage.setItem(chatStorageKey, JSON.stringify(cacheable));
      } catch (e) {
        console.warn("Could not cache the conversation locally:", e);
      }
    };
    const timer = window.setTimeout(write, LOCAL_CACHE_WRITE_DEBOUNCE_MS);
    window.addEventListener('pagehide', write);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener('pagehide', write);
    };
  }, [messages, chatStorageKey]);

  useEffect(() => {
    if (allowedAffiliates.length === 0) return;
    const syncQuestionPool = async () => {
      setLoadingCards(true);
      const questions = await getWelcomeCards(allowedAffiliates.map(a => a.id), selectedAffiliate, principal, isEmbedded);
      setCurrentExampleQuestions(questions);
      setLoadingCards(false);
    };
    syncQuestionPool();
  }, [allowedAffiliates, selectedAffiliate, principal, isEmbedded]);

  useEffect(() => {
    // A brand-new message (user sends, or the assistant's reply bubble first appears) gets one
    // smooth scroll into view. Streaming tokens into that SAME last message only grows its height
    // without adding a new entry — re-running scrollIntoView on every token there was what caused
    // the "quick scroll through the messages" stutter (each call re-measures layout mid-animation
    // and restarts it). While streaming, just pin the container to its bottom instantly instead.
    const isNewMessage = messages.length !== prevMessageCountRef.current;
    prevMessageCountRef.current = messages.length;
    const justFinishedLoading = prevLoadingRef.current && !loading;
    prevLoadingRef.current = loading;

    // Older messages were just prepended (loadEarlier) — not a new message; keep the viewport put.
    if (skipAutoScrollRef.current) {
      skipAutoScrollRef.current = false;
      return;
    }

    if (isNewMessage) {
      messagesEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
    } else if (loading && chatWindowRef.current) {
      chatWindowRef.current.scrollTop = chatWindowRef.current.scrollHeight;
    } else if (justFinishedLoading && chatWindowRef.current) {
      // The spinner/loader row between the messages and the bottom anchor unmounts the instant
      // loading flips false, and that message's follow-up/feedback controls mount at the same
      // moment — both change the container's scrollHeight right here. Without re-pinning, the
      // browser just clamps the old (now too-large) scrollTop to the new max, which looks like
      // an unwanted scroll-up right as each response finishes.
      chatWindowRef.current.scrollTop = chatWindowRef.current.scrollHeight;
    }
  }, [messages, loading]);

  const addTraceStep = (payload: any) => {
    const title = payload?.title || payload?.message || "Agent step";
    const detail = payload?.detail || payload?.message || "";
    const id = `${title}-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
    // Prefer detail over title here too — processNodeQueue already calls setLatestStepTitle
    // with the correct detail-preferring value right before calling addTraceStep, but this call
    // used to unconditionally overwrite it back to the generic title every single time, which is
    // exactly why the spinner never showed the real per-step description despite that fix.
    setLatestStepTitle(detail || title);
    setTraceSteps(prev => [
      ...prev,
      {
        id, title, detail, status: payload?.status === "complete" ? "complete" : "active",
        stepId: payload?.stepId,
        batchIndex: payload?.batchIndex, batchSize: payload?.batchSize,
      }
    ]);
  };

  const markTraceComplete = () => {
    setTraceSteps(prev => {
      if (!prev.length) return prev;
      return prev.map((step, index) =>
        index === prev.length - 1 ? { ...step, status: "complete" } : step
      );
    });
  };

  const processNodeQueue = () => {
    if (nodeQueueRef.current.length === 0) {
      isProcessingQueue.current = false;
      return;
    }
    isProcessingQueue.current = true;
    const item = nodeQueueRef.current.shift()!;
    const friendlyLabel = toolStepLabel(item.tool, item.subject) ?? getNodeLabel(item.node);
    // The main (non-embedded) loading spinner's label — latestStepTitle below — should show the
    // backend's own per-step detail (e.g. "Synthesizing final answer for...") rather than the
    // generic friendlyLabel, since that's the actual useful, per-step text for a multi-step node
    // like tool_agent_node's ReAct loop, where no single static label fits every step.
    const displayTitle = item.detail || friendlyLabel;

    setAgentStatus(item.node);
    setAgentPath(prev => (prev.includes(item.node) ? prev : [...prev, item.node]));
    markTraceComplete();
    setLatestStepTitle(displayTitle);

    addTraceStep({
      title: friendlyLabel,
      detail: item.detail || `Node: ${item.node}`,
      status: 'active',
      batchIndex: item.batchIndex,
      batchSize: item.batchSize,
      stepId: item.stepId,
    });

    setTimeout(() => {
      processNodeQueue();
    }, 700);
  };

  const handleSendMessage = async (
    textToSend: string,
    currentAttachments: { filename: string; content: string }[],
    // A retry replaces the failed message and its failure notice rather than repeating them in the thread.
    replaceIds: string[] = []
  ) => {
    setTraceSteps([]);
    setTraceFacts({});
    if (!textToSend.trim() || loading) return;

    // Instant local preview from the base64 content already in hand — no round-trip needed.
    // The durable, cross-device version comes back later via mapServerMessage's fileId path.
    const imageAttachments: MessageImage[] = currentAttachments
      .filter(a => isImageFilename(a.filename))
      .map(a => {
        const ext = a.filename.toLowerCase().split('.').pop() || '';
        const mime = IMAGE_EXTENSION_MIME[ext] || 'application/octet-stream';
        return { filename: a.filename, dataUrl: `data:${mime};base64,${a.content}` };
      });

    const userMessageId = genId();
    const aiMessageId = genId();
    setMessages(prev => [
      ...(replaceIds.length ? prev.filter(m => !replaceIds.includes(m.id)) : prev),
      { id: userMessageId, sender: 'user', text: textToSend, images: imageAttachments.length ? imageAttachments : undefined },
      { id: aiMessageId, sender: 'ai', text: '' }
    ]);

    setHasChatted(true);
    setInput('');
    setInputEngaged(false);
    setLoading(true);
    setAgentStatus(TURN_SENT_STATUS);
    setAgentPath([]);
    hasStreamedRef.current = false;
    postPatchyStatus('thinking');

    const controller = new AbortController();
    abortControllerRef.current = controller;
    let stoppedByUser = false;
    // Set when the turn ends in an error (the stream's own error event, or the request failing), so Patchy shows it instead of cheering.
    let failed = false;

    try {
      // Pushes only what changed in the connected folder since the last message (a no-op when none
      // is connected). Never throws, so a sync problem can't block the message itself.
      await workspace.ensureSynced();
      await api.sendChatMessage(
        principal,
        textToSend,
        currentAttachments,
        selectedAffiliate,
        sessionId,
        (rawChunk) => {
          if (!rawChunk.trim()) return;
          const cleanLines = rawChunk
            .split('\n')
            .map(line => line.trim())
            .filter(line => line.startsWith('data: '));

          for (const line of cleanLines) {
            try {
              const rawJson = line.substring(6);
              const payload = JSON.parse(rawJson);

              if (payload.event === 'trace') {
                addTraceStep(payload);
              }
              if (payload.event === 'node_progress') {
                nodeQueueRef.current.push({
                  node: payload.node, detail: payload.detail,
                  batchIndex: payload.batch_index, batchSize: payload.batch_size, stepId: payload.step_id, tool: payload.tool, subject: payload.subject,
                });
                if (!isProcessingQueue.current) {
                  processNodeQueue();
                }
              }
              if (payload.event === 'trace_facts') {
                setTraceFacts(prev => recordFacts(prev, payload.step_id, payload.facts));
              }
              if (payload.event === 'budget') {
                setBudget(prev => ({ ...(payload as BudgetView), increase_request: (payload as BudgetView).can_request_increase ? prev?.increase_request : null }));
              }
              if (payload.event === 'emotion') {
                setPatchyEnergy(payload.tier === 'subdued' || payload.tier === 'easing' ? payload.tier : 'open');
              }
              if (payload.event === 'steer_applied') {
                // The agent folded this many queued steering messages into its work (oldest first).
                const count = Number(payload.count) || 0;
                const appliedIds: string[] = [];
                for (const entry of steerLogRef.current) {
                  if (appliedIds.length >= count) break;
                  if (!entry.applied) {
                    entry.applied = true;
                    appliedIds.push(entry.id);
                  }
                }
                if (appliedIds.length) {
                  setMessages(prev => prev.map(m => (appliedIds.includes(m.id) ? { ...m, steer: 'applied' } : m)));
                }
              }
              if (payload.event === 'browser_live_view' && payload.url) {
                // Attaches to the in-progress AI bubble, same target the 'token' handler below
                // appends streamed text to — the embed rides along with that same message.
                setMessages(prev => {
                  const updated = [...prev];
                  const lastIndex = updated.length - 1;
                  if (updated[lastIndex] && updated[lastIndex].sender === 'ai') {
                    updated[lastIndex] = { ...updated[lastIndex], liveBrowserUrl: payload.url };
                  }
                  return updated;
                });
              }
              if (payload.event === 'local_edit_proposal' && Array.isArray(payload.edits)) {
                // A validated set of edits to the connected local folder, shown as a diff card on
                // the in-progress AI bubble. Nothing is written until the user presses Apply.
                const proposal: EditProposal = {
                  proposal_id: payload.proposal_id,
                  summary: payload.summary ?? '',
                  folder: payload.folder ?? '',
                  files: payload.files ?? [],
                  edits: payload.edits,
                };
                setMessages(prev => {
                  const updated = [...prev];
                  const lastIndex = updated.length - 1;
                  if (updated[lastIndex] && updated[lastIndex].sender === 'ai') {
                    updated[lastIndex] = { ...updated[lastIndex], editProposal: proposal };
                  }
                  return updated;
                });
              }
              if (payload.event === 'token') {
                if (!hasStreamedRef.current) {
                  hasStreamedRef.current = true;
                  postPatchyStatus('streaming');
                }
                setMessages(prev => {
                  const updated = [...prev];
                  const lastIndex = updated.length - 1;
                  if (updated[lastIndex] && updated[lastIndex].sender === 'ai') {
                    updated[lastIndex] = {
                      ...updated[lastIndex],
                      text: (updated[lastIndex].text || '') + payload.text
                    };
                  }
                  return updated;
                });
              }
              if (payload.event === 'noted' && typeof payload.text === 'string') {
                setMessages(prev => {
                  const updated = [...prev];
                  const lastIndex = updated.length - 1;
                  if (updated[lastIndex] && updated[lastIndex].sender === 'ai') {
                    updated[lastIndex] = { ...updated[lastIndex], noted: [...(updated[lastIndex].noted ?? []), payload.text] };
                  }
                  return updated;
                });
              }
              if (payload.event === 'regenerate_reset') {
                // A retry (self-correction or rewrite fallback) is about to stream a fresh
                // response — clear the in-progress bubble so its tokens don't concatenate
                // onto the previous (discarded) attempt's text.
                setMessages(prev => {
                  const updated = [...prev];
                  const lastIndex = updated.length - 1;
                  if (updated[lastIndex] && updated[lastIndex].sender === 'ai') {
                    updated[lastIndex] = { ...updated[lastIndex], text: '' };
                  }
                  return updated;
                });
              }
              if (payload.event === 'kb_images') {
                setMessages(prev => {
                  const updated = [...prev];
                  const lastIndex = updated.length - 1;
                  if (updated[lastIndex] && updated[lastIndex].sender === 'ai') {
                    const existing = updated[lastIndex].images || [];
                    const incoming: MessageImage[] = payload.images || [];
                    const merged = [
                      ...existing,
                      ...incoming.filter(img => !existing.some(e => e.fileId === img.fileId))
                    ];
                    updated[lastIndex] = { ...updated[lastIndex], images: merged };
                  }
                  return updated;
                });
              }
              if (payload.event === 'final_generation') {
                if (!hasStreamedRef.current) {
                  hasStreamedRef.current = true;
                  postPatchyStatus('streaming');
                }
                setMessages(prev => {
                  const updated = [...prev];
                  const lastIndex = updated.length - 1;
                  if (updated[lastIndex] && updated[lastIndex].sender === 'ai') {
                    updated[lastIndex] = { ...updated[lastIndex], text: payload.text };
                  }
                  return updated;
                });
                markTraceComplete();
              }
              if (payload.event === 'error') {
                setMessages(prev => {
                  const updated = [...prev];
                  const lastIndex = updated.length - 1;
                  if (updated[lastIndex] && updated[lastIndex].sender === 'ai') {
                    updated[lastIndex] = { ...updated[lastIndex], text: `Execution Fault: ${payload.message}` };
                  }
                  return updated;
                });
                setAgentStatus('');
                failed = true;
                postPatchyStatus('error');
                addTraceStep({ title: "Execution error", detail: payload.message, status: "active" });
                markTraceComplete();
              }
            } catch (jsonErr) {
              console.warn("Skipping partial, non-JSON SSE chunk buffer:", jsonErr);
            }
          }
        },
        undefined,
        controller.signal
      );

      // Attachments are scoped to the turn that sent them (already durably stored server-side
      // via ingest_doc_to_session) — clear them so they aren't silently re-sent and reprocessed
      // on every subsequent message in this conversation.
      if (currentAttachments.length > 0) {
        setAttachments([]);
        setAttachedFiles([]);
        setUploadedFiles([]);
      }
    } catch (err) {
      if (err instanceof DOMException && err.name === 'AbortError') {
        stoppedByUser = true;
        // A deliberate Stop, not a failure — leave whatever partial text already streamed in
        // place rather than showing a scary error over it.
        setMessages(prev => {
          const updated = [...prev];
          const lastIndex = updated.length - 1;
          if (updated[lastIndex] && updated[lastIndex].sender === 'ai') {
            const existingText = updated[lastIndex].text || '';
            updated[lastIndex] = { ...updated[lastIndex], text: existingText ? `${existingText}\n\n_[Stopped]_` : '_[Stopped before responding]_' };
          }
          return updated;
        });
        postPatchyStatus('done');
      } else {
        // What went wrong, said plainly and only as far as it is known (chatErrors.ts). The raw error stays in the console and, as one
        // technical line with no message text in it, behind a "Details" disclosure on the notice.
        const sendError = toChatSendError(err);
        const view = describeChatError(sendError);
        console.error("Chat send failed:", sendError.kind, sendError.status ?? '', err);
        failed = true;
        postPatchyStatus('error');
        const notice: Message = {
          id: aiMessageId, sender: 'ai', text: view.message,
          failure: { details: view.details, retryable: view.retryable, retryText: textToSend, userMessageId },
        };
        setMessages(prev => {
          // The empty reply placeholder this turn added becomes the notice; if the answer had already started streaming, the notice follows it.
          const at = prev.findIndex(m => m.id === aiMessageId);
          if (at >= 0 && !prev[at].text) return prev.map((m, i) => (i === at ? notice : m));
          return [...prev, { ...notice, id: genId() }];
        });
      }
    } finally {
      // Steers that were queued but never applied (this turn had no work phase, or it ended first)
      // go out as ordinary messages once the turn is over. A deliberate Stop drops them instead.
      const unapplied = steerLogRef.current.filter(entry => !entry.applied);
      steerLogRef.current = [];
      if (unapplied.length) {
        if (stoppedByUser) {
          const droppedIds = unapplied.map(entry => entry.id);
          setMessages(prev => prev.map(m => (droppedIds.includes(m.id) ? { ...m, steer: 'dropped' } : m)));
        } else {
          const ids = unapplied.map(entry => entry.id);
          setMessages(prev => prev.map(m => (ids.includes(m.id) ? { ...m, steer: 'deferred' } : m)));
          pendingResendRef.current.push(...unapplied.map(({ id, text }) => ({ id, text })));
        }
      }
      if (stoppedByUser) pendingResendRef.current = [];
      abortControllerRef.current = null;
      setLoading(false);
      if (failed) setPatchyFault(c => c + 1);
      else if (!stoppedByUser) setPatchyCheer(c => c + 1);
      setAgentStatus('');
      postPatchyStatus('done');
    }
  };

  const stopGeneration = () => {
    abortControllerRef.current?.abort();
  };

  // Sends a message to the turn that is running instead of waiting for it to finish. It shows up
  // above the in-progress reply (the stream handlers only ever update the LAST message, so it must
  // not go after it) and is applied at the agent's next step.
  const handleSteer = async (text: string) => {
    const trimmed = text.trim();
    if (!trimmed) return;
    const id = genId();
    setInput('');
    setInputEngaged(false);
    setMessages(prev => {
      const last = prev.length - 1;
      const bubble: Message = { id, sender: 'user', text: trimmed, steer: 'queued' };
      return last >= 0 && prev[last].sender === 'ai'
        ? [...prev.slice(0, last), bubble, prev[last]]
        : [...prev, bubble];
    });
    steerLogRef.current.push({ id, text: trimmed, applied: false });

    let status = 'error';
    try {
      status = (await api.steerChat(sessionId, trimmed)).status;
    } catch (err) {
      console.warn("Steering request failed; it will be sent as a normal message instead.", err);
    }
    if (status === 'queued') setPatchySteer(c => c + 1);
    if (status !== 'queued') {
      // Nothing could act on it (no running turn, work phase already over, ...): send it as the next
      // message as soon as the current turn is done.
      steerLogRef.current = steerLogRef.current.filter(entry => entry.id !== id);
      setMessages(prev => prev.map(m => (m.id === id ? { ...m, steer: 'deferred' } : m)));
      pendingResendRef.current.push({ id, text: trimmed });
      setResendTick(t => t + 1);
    }
  };

  const onSubmitForm = (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim()) return;
    if (loading) {
      handleSteer(input);
      return;
    }
    handleSendMessage(input, attachmentsRef.current);
  };

 const handleFeedback = async (messageId: string, choice: 'like' | 'dislike') => {
  if (choice === 'dislike') {
    // Open modal to capture reason and tag instead of immediately submitting
    setFeedbackModal({ isOpen: true, messageId });
    return;
  }

  // Handle positive feedback immediately
  await sendFeedbackPayload(messageId, 'like', 'Positive feedback', 'positive');
};

// The Meetings page starts a meeting and opens the chat with its first line to send (`autoSend`), once, in the fresh conversation it made.
useEffect(() => {
  const autoSend = (routeLocation.state as { autoSend?: unknown } | null)?.autoSend;
  if (typeof autoSend === 'string' && autoSend.trim() && !loading) {
    routeNavigate(routeLocation.pathname, { replace: true, state: null });
    void handleSendMessageRef.current(autoSend, []);
  }
}, [routeLocation.state, routeLocation.pathname, routeNavigate, loading]);

// Refs kept in sync with the latest closures on every render, so the memoized ChatMessageList
// below can skip re-rendering on unrelated state changes (e.g. typing) without ever calling a
// stale version of these handlers — the same stale-closure fix applied earlier to attachmentsRef.
const handleSendMessageRef = useRef(handleSendMessage);
const handleFeedbackRef = useRef(handleFeedback);
const getTokenRef = useRef(getToken);
const localEditActionsRef = useRef({ apply: workspace.applyEdits, undo: workspace.undoEdits });
useEffect(() => {
  // Pressing Start office hours is the person saying "let's begin": the first line goes in for them, and Sonic opens the meeting.
  meetingStartedRef.current = (name: string) => { void handleSendMessageRef.current(`Let's start our ${name}.`, []); };
  handleSendMessageRef.current = handleSendMessage;
  handleFeedbackRef.current = handleFeedback;
  getTokenRef.current = getToken;
  localEditActionsRef.current = { apply: workspace.applyEdits, undo: workspace.undoEdits };
});

// Re-sends deferred steers one at a time, only once the previous turn has fully finished. Declared
// AFTER the effect above on purpose: effects run in declaration order, so by the time this runs the
// ref already points at the handler from this render (loading=false), never a stale closure.
useEffect(() => {
  if (loading) return;
  const next = pendingResendRef.current.shift();
  if (!next) return;
  setMessages(prev => prev.filter(m => m.id !== next.id));
  handleSendMessageRef.current(next.text, []);
}, [loading, resendTick]);

const sendFeedbackPayload = async (
  messageId: string,
  choice: 'like' | 'dislike',
  reason: string,
  tag: string
) => {
  let token: string | null = null;
  const isGuest = ['guest', 'guest_bty'].includes(localStorage.getItem('principal') || '');
  if (isGuest) {
    token = localStorage.getItem('guest_token');
  } else if (getToken) {
    token = await getToken();
  }

  const msgIndex = messages.findIndex(m => m.id === messageId);
  const targetMsg = messages[msgIndex];
  const userPromptMsg = messages.slice(0, msgIndex).reverse().find(m => m.sender === 'user');

  // Update local UI feedback state
  setMessages(prev =>
    prev.map(m => (m.id === messageId ? { ...m, feedback: choice } : m))
  );

  // 4. Send the complete payload matching app.py's FeedbackPayload schema
  try {
    await fetch(`${BASE_URL}/api/chat/feedback`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {})
      },
      body: JSON.stringify({
        username: principal,
        session_id: sessionId,
        message_id: messageId,
        feedback: choice,
        rating: choice,
        choice: choice,
        user_prompt: userPromptMsg ? userPromptMsg.text : "",
        bad_response: targetMsg ? targetMsg.text : "",
        reason: reason,
        tag: tag
      }),
    });
  } catch (err) {
    console.error("Feedback submission failed:", err);
  }
};

const handleSubmitNegativeFeedback = async (e: React.FormEvent) => {
  e.preventDefault();
  if (!feedbackModal.messageId) return;

  await sendFeedbackPayload(
    feedbackModal.messageId,
    'dislike',
    feedbackReason || 'No specific reason provided.',
    feedbackTag
  );

  // Reset modal state
  setFeedbackModal({ isOpen: false, messageId: null });
  setFeedbackReason('');
  setFeedbackTag('hallucination');
};

  // Secondary footer actions, tucked behind the "..." overflow button and
  // surfaced through the OptionWheel popover instead of their own icons.
  const baseOverflowItems: OptionWheelItem[] = [
    {
      label: 'Help',
      icon: (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="12" cy="12" r="9" />
          <line x1="12" y1="8" x2="12" y2="12" />
          <line x1="12" y1="16" x2="12.01" y2="16" />
        </svg>
      )
    },
    {
      label: targetRepo ? `Repo: ${targetRepo}` : 'Target Repo',
      icon: (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <polyline points="16 18 22 12 16 6" />
          <polyline points="8 6 2 12 8 18" />
        </svg>
      )
    },
    {
      label: 'Conversations',
      icon: (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
        </svg>
      )
    },
    {
      label: 'Trace',
      icon: (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M4 5h16" />
          <path d="M7 10h10" />
          <path d="M10 15h4" />
        </svg>
      )
    },
    {
      label: 'Export',
      disabled: loading || !hasChatted,
      icon: (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
          <polyline points="7 10 12 15 17 10" />
          <line x1="12" y1="15" x2="12" y2="3" />
        </svg>
      )
    },
    {
      label: 'Delete',
      disabled: loading,
      icon: (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <polyline points="3 6 5 6 21 6" />
          <path d="M19 6l-2 14H7L5 6" />
          <line x1="10" y1="11" x2="10" y2="17" />
          <line x1="14" y1="11" x2="14" y2="17" />
        </svg>
      )
    }
  ];

  const runBaseOverflowAction = (index: number) => {
    switch (index) {
      case 0:
        setShowTooltip(v => !v);
        break;
      case 1:
        setShowRepoBanner(v => !v);
        break;
      case 2:
        toggleConversationsBlade();
        break;
      case 3:
        if (!isEmbedded && isMobileView) setIsMobileTraceOpen(v => !v);
        else toggleTracePanel();
        break;
      case 4:
        if (!loading && hasChatted) handleExportChat();
        break;
      case 5:
        if (!loading) setShowClearConfirm(true);
        break;
      default:
        break;
    }
    setShowOverflowWheel(false);
  };

  // Workspace settings that used to live in the always-visible <Filters /> row. They sit at the
  // top of the same overflow menu; entries pair each menu item with its own handler so the
  // combined list below never depends on hardcoded indexes (the row is hidden in embed mode, so
  // the number of entries varies). keepOpen lets the on/off toggles be tapped without the menu
  // closing each time.
  const settingsGlyph = (children: React.ReactNode) => (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      {children}
    </svg>
  );
  const settingsLocked = loading || principal === 'guest_bty';
  // Patchy isn't offered to the BTY guest at all (no mascot, no menu toggle).
  const patchyAllowed = !isEmbedded && principal !== 'guest_bty';
  const scopeLabel = allowedAffiliates.find(a => a.id === selectedAffiliate)?.display_name
    || (selectedAffiliate !== 'All' ? selectedAffiliate : 'None');
  const folderGlyph = settingsGlyph(<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />);
  const workspaceEntries: { item: OptionWheelItem; run: () => void; keepOpen?: boolean }[] = !workspace.supported ? [] : [
    workspace.status === 'disconnected'
      ? {
          item: { label: 'Connect local folder', disabled: loading, icon: folderGlyph },
          // First time in this browser: say what gets uploaded before opening the picker.
          run: () => {
            if (hasLocalFolderConsent()) void workspace.connect();
            else setShowFolderConsent(true);
          },
        }
      : workspace.status === 'needs-permission'
        ? { item: { label: `Reconnect folder: ${workspace.folderName}`, disabled: loading, icon: folderGlyph }, run: () => { void workspace.reconnect(); } }
        : workspace.status === 'syncing'
          ? { item: { label: 'Syncing folder…', disabled: true, icon: folderGlyph }, run: () => {}, keepOpen: true }
          : workspace.status === 'error'
            ? { item: { label: `Folder sync failed (retry): ${workspace.message ?? 'unknown error'}`.slice(0, 60), disabled: loading, icon: folderGlyph }, run: () => { void workspace.resync(); } }
            : {
                item: {
                  label: `Folder: ${workspace.folderName}${workspace.fileCount !== null ? ` · ${workspace.fileCount} files` : ''}`,
                  disabled: loading,
                  icon: folderGlyph,
                },
                run: () => { void workspace.resync(); },
              },
    ...(workspace.status === 'disconnected' ? [] : [{
      item: { label: 'Disconnect folder', disabled: loading, icon: folderGlyph },
      run: () => { void workspace.disconnect(); },
    }]),
    // Survives reloads (the saved originals live in IndexedDB), so a change applied earlier can
    // always be reverted even after its diff card is gone from the chat.
    ...(workspace.lastEdit && workspace.canEdit ? [{
      item: {
        label: `Undo last applied edit (${workspace.lastEdit.fileCount} file${workspace.lastEdit.fileCount === 1 ? '' : 's'})`,
        disabled: loading,
        icon: folderGlyph,
      },
      run: () => {
        const edit = workspace.lastEdit;
        if (!edit) return;
        void workspace.undoEdits(edit.id).then(result => {
          if (!result.ok) window.alert(result.errors.join('\n'));
        });
      },
    }] : []),
  ];
  const settingsEntries: { item: OptionWheelItem; run: () => void; keepOpen?: boolean }[] = isEmbedded ? [] : [
    ...workspaceEntries,
    {
      item: {
        label: fetchingScope ? 'Scope: loading…' : `Scope: ${scopeLabel}`,
        disabled: loading || fetchingScope || allowedAffiliates.length === 0,
        icon: settingsGlyph(<><rect x="3" y="11" width="18" height="11" rx="2" ry="2" /><path d="M7 11V7a5 5 0 0 1 10 0v4" /></>),
      },
      // Opens its own picker popup (rendered next to the overflow popover) instead of cycling
      // through scopes one click at a time.
      run: () => {
        setShowOverflowWheel(false);
        setShowScopePicker(true);
      },
      keepOpen: true,
    },
    {
      item: {
        label: `Open Answers: ${ragMode === 'open' ? 'On' : 'Off'}`,
        disabled: settingsLocked,
        icon: settingsGlyph(<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />),
      },
      run: () => handleRagModeChange(ragMode === 'open' ? 'strict' : 'open'),
      keepOpen: true,
    },
    {
      item: {
        label: `Deep Thinking: ${deepThinking ? 'On' : 'Off'}`,
        disabled: settingsLocked,
        icon: settingsGlyph(<><circle cx="12" cy="12" r="9" /><path d="M12 8v4l2.5 2.5" /></>),
      },
      run: () => handleDeepThinkingChange(!deepThinking),
      keepOpen: true,
    },
    // Which model does the tool agent's reasoning and code-writing steps. The reply you read is still written by Gemini, so Claude here is the hybrid.
    // Not offered when Claude isn't set up on the server, or to guests.
    ...(!toolModelAvailable ? [] : [{
      item: {
        label: `Code model: ${toolModel === 'claude' ? 'Claude' : 'Gemini'}`,
        disabled: settingsLocked,
        icon: settingsGlyph(<><polyline points="16 18 22 12 16 6" /><polyline points="8 6 2 12 8 18" /></>),
      },
      run: () => handleToolModelChange(toolModel === 'claude' ? 'gemini' : 'claude'),
      keepOpen: true,
    }]),
    {
      item: {
        label: `Message First: ${proactiveOpening ? 'On' : 'Off'}`,
        disabled: settingsLocked,
        icon: settingsGlyph(<><path d="M4 5h16v11H8l-4 4V5z" /><line x1="8" y1="9" x2="16" y2="9" /><line x1="8" y1="12" x2="13" y2="12" /></>),
      },
      run: () => handleProactiveOpeningChange(!proactiveOpening),
      keepOpen: true,
    },
    ...(!opsDigest.available ? [] : [{
      item: {
        label: `Incident Digest: ${opsDigest.enabled ? 'On' : 'Off'}`,
        disabled: settingsLocked,
        icon: settingsGlyph(<><path d="M12 3l9 16H3L12 3z" /><line x1="12" y1="10" x2="12" y2="14" /><circle cx="12" cy="17" r="0.5" /></>),
      },
      run: () => handleOpsDigestChange(!opsDigest.enabled),
      keepOpen: true,
    }]),
    ...(!patchyAllowed ? [] : [{
      item: {
        label: `SAAPP: ${showPatchy ? 'On' : 'Off'}`,
        icon: settingsGlyph(<><rect x="5" y="8" width="14" height="11" rx="3" /><line x1="12" y1="8" x2="12" y2="4" /><circle cx="12" cy="3" r="1" /><circle cx="9.5" cy="13" r="1" /><circle cx="14.5" cy="13" r="1" /></>),
      },
      run: togglePatchy,
      keepOpen: true,
    }]),
  ];

  // The conversations list lives in the rail now; the embedded widget has no rail, so only it keeps the menu entry (and the overlay it opens).
  const baseOverflowEntries = baseOverflowItems.map((item, index) => ({ item, index })).filter(e => isEmbedded || e.item.label !== 'Conversations');
  const overflowItems: OptionWheelItem[] = [...settingsEntries.map(e => e.item), ...baseOverflowEntries.map(e => e.item)];

  const runOverflowAction = (index: number) => {
    const entry = settingsEntries[index];
    if (!entry) {
      runBaseOverflowAction(baseOverflowEntries[index - settingsEntries.length]?.index ?? -1);
      return;
    }
    entry.run();
    if (!entry.keepOpen) setShowOverflowWheel(false);
  };

  return (
  <div className={isEmbedded ? undefined : 'chat-page'}>
      {/* PORTAL BODY */}
      <main className={`portal-body ${!hasChatted ? 'initial-state-view' : ''}`}>

        {/* The product's mark, name and sub header: shown while the conversation is empty, then out of the way (the rail carries the name). */}
        {!hasChatted && !isEmbedded && (
          <div className="empty-brand">
            {patchyAllowed && showPatchy
              ? <PatchyHero energy={patchyEnergy} budget={patchyBudget} engaged={inputEngaged} typed={input.length} />
              : <LogoMark size={64} label={BRAND_NAME} />}
            <h2 className="empty-brand-title">{BRAND_NAME}</h2>
            <p className="empty-brand-sub">{BRAND_TAGLINE}</p>
          </div>
        )}

        {/* 1. INITIAL STATE / CARDS */}
        {!hasChatted && (
          <div className="example-cards-container">
            {loadingCards ? (
              <div style={{ color: 'var(--text-muted)', fontSize: '0.85rem', padding: '1rem' }}>
                Querying directory indices for security group context...
              </div>
            ) : (
              currentExampleQuestions.map((card, idx) => (
                <div key={idx} className="query-card" onClick={() => !loading && chooseWelcomeCard(card)}>
                  {card.label && <small className="query-card-label">{card.label}</small>}
                  <p>{card.text}</p>
                  <span>→</span>
                </div>
              ))
            )}
          </div>
        )}

        {/* 2. CHAT MESSAGES WINDOW */}
        <ChatMessageList
          messages={messages}
          earlierCount={historyStart}
          loadingEarlier={loadingEarlier}
          loadEarlierRef={loadEarlierRef}
          hasChatted={hasChatted}
          loading={loading}
          agentStatus={agentStatus}
          latestStepTitle={latestStepTitle}
          theme={theme}
          isEmbedded={isEmbedded}
          chatWindowRef={chatWindowRef}
          messagesEndRef={messagesEndRef}
          attachmentsRef={attachmentsRef}
          handleSendMessageRef={handleSendMessageRef}
          handleFeedbackRef={handleFeedbackRef}
          getTokenRef={getTokenRef}
          canEditLocal={workspace.canEdit}
          localEditActionsRef={localEditActionsRef}
        />

        {/* 3. INPUT AREA & FOOTER */}
        <footer className="controls-footer" style={{ position: 'relative' }}>
          {/* MOBILE TRACE PILL (Pinned inside top of footer) */}
          {isEmbedded && (
          <>
          <div className="mobile-trace-pill-container">
            <style>{`
              @media (min-width: 768px) {
                .mobile-trace-pill-container {
                  display: none !important;
                  z-index: 1;
                }
              }
            `}</style>

            {showTracePanel && (
              <div className="mobile-trace-pill" style={{ display: 'flex', justifyContent: 'center', width: '100%', marginBottom: '8px' }}>
                <button
                  type="button"
                  onClick={() => setIsMobileTraceOpen(true)}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '8px',
                    padding: '6px 14px',
                    background: 'rgba(30, 41, 59, 0.9)',
                    border: '1px solid rgba(148, 163, 184, 0.3)',
                    borderRadius: '9999px',
                    boxShadow: '0 4px 12px rgba(0,0,0,0.3)',
                    fontSize: '12px',
                    color: '#cbd5e1',
                    cursor: 'pointer',
                    backdropFilter: 'blur(8px)',
                    // zIndex: 50
                  }}
                >
                  <span style={{ width: '8px', height: '8px', borderRadius: '50%', background: '#6366f1', display: 'inline-block' }} />
                  <span style={{ maxWidth: '200px', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                    {latestStepTitle || "View live execution trace..."}
                  </span>
                  <span>▲</span>
                </button>
              </div>
            )}
          </div>
          </>
          )}

          {showRepoBanner && (
            <div className="repo-banner"
              style={{
                display: "flex",
                alignItems: "center",
                gap: "8px",
                padding: "10px 14px",
                marginBottom: "8px",
                background: isEmbedded ? "#121316" : "var(--bg-surface)",
                border: isEmbedded ? "1px solid rgba(0, 242, 254, 0.4)" : "1px solid var(--border-color)",
                borderRadius: "12px",
                fontSize: "13px",
                color: "var(--text-main)",
              }}
            >
              <span style={{ color: "var(--text-muted)", whiteSpace: "nowrap" }}>Target repo:</span>
              <input
                type="text"
                value={repoInput}
                onChange={(e) => setRepoInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") { e.preventDefault(); handleSaveTargetRepo(); }
                }}
                placeholder="owner/repo (blank = auto-detect)"
                disabled={repoSaving}
                style={{
                  flex: 1,
                  minWidth: 0,
                  background: "transparent",
                  border: "1px solid var(--border-color)",
                  borderRadius: "8px",
                  padding: "6px 10px",
                  color: "var(--text-main)",
                  fontSize: "13px",
                }}
              />
              <button
                type="button"
                onClick={handleSaveTargetRepo}
                disabled={repoSaving}
                style={{
                  background: "var(--accent-primary)",
                  border: "none",
                  borderRadius: "8px",
                  padding: "6px 12px",
                  color: "var(--on-accent, #fff)",
                  fontSize: "13px",
                  cursor: repoSaving ? "not-allowed" : "pointer",
                }}
              >
                Save
              </button>
              {targetRepo && (
                <button
                  type="button"
                  onClick={handleClearTargetRepo}
                  disabled={repoSaving}
                  style={{
                    background: "transparent",
                    border: "1px solid var(--border-color)",
                    borderRadius: "8px",
                    padding: "6px 12px",
                    color: "var(--text-main)",
                    fontSize: "13px",
                    cursor: repoSaving ? "not-allowed" : "pointer",
                  }}
                >
                  Clear
                </button>
              )}
              <button
                type="button"
                onClick={() => setShowRepoBanner(false)}
                style={{ background: "transparent", border: "none", color: "var(--text-muted)", cursor: "pointer", fontSize: "14px" }}
              >
                ✕
              </button>
            </div>
          )}

          {attachNotice && (
            <div className="attach-notice" role="alert">
              {attachNotice}
              <button type="button" aria-label="Dismiss" onClick={() => setAttachNotice('')}>✕</button>
            </div>
          )}

          {uploadedFiles.length > 0 && (
            <div className="attached-files-banner">
              {uploadedFiles.map((file, idx) => (
                <div key={idx} className="attached-file-pill">
                  📎 {file.name}
                  <button className="remove-file-btn" onClick={() => handleRemoveAttachment(idx)}>✕</button>
                </div>
              ))}
            </div>
          )}

          {!isEmbedded && <BudgetBanner budget={budget} theme={theme} onExtend={async () => setBudget(await api.extendBudget())} onRequest={async () => setBudget(await api.requestBudgetIncrease())} />}
          <form onSubmit={onSubmitForm} className="chat-input-area">
            <input
              id="file-upload"
              type="file"
              multiple
              style={{ display: "none" }}
              onChange={(e) => handleFileUpload(e)}
            />

            <div className="chat-input-wrapper">
              <InputGlow running={loading} />
              {/* The empty chat has the full-size Patchy; this small one takes over when the conversation starts. */}
              {patchyAllowed && showPatchy && hasChatted && (
                <MiniPatchy
                  look={loading ? lookForNode(agentStatus) : 'idle'}
                  cheer={patchyCheer}
                  fault={patchyFault}
                  steer={patchySteer}
                  engaged={inputEngaged}
                  typed={input.length}
                  energy={patchyEnergy}
                  budget={patchyBudget}
                />
              )}
              <textarea
                ref={inputRef}
                onPointerDown={() => setInputEngaged(true)}
                onBlur={() => setInputEngaged(false)}
                className="chat-textarea"
                placeholder={loading ? "Steer Sonic while it works..." : chatPlaceholder}
                value={input}
                onChange={(e) => {
                  setInput(e.target.value);
                  setInputEngaged(true);
                  e.target.style.height = "auto";
                  e.target.style.height = `${e.target.scrollHeight}px`;
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    onSubmitForm(e);
                  }
                }}
                onPaste={handlePastedImage}
                rows={2}
              />

              <div className="icon-row-overlay">
                {!isEmbedded && <UsageRing budget={budget} />}
                <div style={{ position: "relative" }} ref={optionsMenuRef}>
                  <button
                    type="button"
                    className={`circle-icon-button ${showOverflowWheel || showScopePicker ? 'trace-active' : ''}`}
                    onClick={() => {
                      if (showScopePicker) {
                        setShowScopePicker(false);
                        return;
                      }
                      setShowOverflowWheel(v => !v);
                    }}
                    title="More actions"
                    style={showOverflowWheel || showScopePicker ? {
                      background: 'var(--accent-primary)',
                      border: '1px solid var(--accent-primary)'
                    } : {}}
                  >
                    <style>{`
                      .circle-icon-button.trace-active svg,
                      .circle-icon-button.trace-active svg * {
                        stroke: #ffffff !important;
                      }
                    `}</style>
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <circle cx="5" cy="12" r="1.5" fill="currentColor" stroke="none" />
                      <circle cx="12" cy="12" r="1.5" fill="currentColor" stroke="none" />
                      <circle cx="19" cy="12" r="1.5" fill="currentColor" stroke="none" />
                    </svg>
                  </button>

                  {showOverflowWheel && (
                    <div
                      className="chat-overflow-wheel-popover"
                      style={{
                        position: "absolute",
                        bottom: "45px",
                        // Anchored from the right, not the left: .icon-row-overlay pins this
                        // button flush against the screen's right edge (right: 0.75rem), so a
                        // fixed-width box anchored at left:0 grows rightward straight off-screen
                        // on mobile. Anchoring from the right instead grows it leftward, into the
                        // actual available space, regardless of viewport width.
                        right: "0",
                        width: "200px",
                        maxWidth: "calc(100vw - 24px)",
                        // Scrolls instead of spilling: unconstrained, a list taller than this box
                        // overflows downward and lands on top of the "..." button, so closing the
                        // menu would click whichever item (e.g. Delete) ended up under it.
                        maxHeight: "min(440px, calc(100vh - 160px))",
                        overflowY: "auto",
                        background: isEmbedded ? "#121316" : "#121824",
                        border: isEmbedded ? "1px solid rgba(0, 242, 254, 0.4)" : "1px solid #334155",
                        borderRadius: "12px",
                        boxShadow: isEmbedded
                          ? "0 10px 25px -5px rgba(0, 0, 0, 0.7), 0 0 12px rgba(0, 242, 254, 0.15)"
                          : "0 10px 25px -5px rgba(0, 0, 0, 0.5)",
                        zIndex: 900,
                        padding: "6px",
                      }}
                    >
                      <OptionWheel
                        items={overflowItems}
                        columnLayout
                        fontSize={0.85}
                        textColor="#94a3b8"
                        activeColor="#f8fafc"
                        onActivate={index => runOverflowAction(index)}
                      />
                    </div>
                  )}

                  {showScopePicker && (
                    <div
                      className="chat-scope-picker-popover"
                      style={{
                        position: "absolute",
                        bottom: "45px",
                        right: "0",
                        width: "220px",
                        maxWidth: "calc(100vw - 24px)",
                        maxHeight: "min(320px, calc(100vh - 160px))",
                        overflowY: "auto",
                        background: isEmbedded ? "#121316" : "#121824",
                        border: isEmbedded ? "1px solid rgba(0, 242, 254, 0.4)" : "1px solid #334155",
                        borderRadius: "12px",
                        boxShadow: "0 10px 25px -5px rgba(0, 0, 0, 0.5)",
                        zIndex: 100,
                        padding: "6px",
                      }}
                    >
                      <div style={{ padding: "4px 8px 6px", fontSize: "0.7rem", letterSpacing: "0.05em", textTransform: "uppercase", color: "#64748b" }}>
                        Active security scope
                      </div>
                      <OptionWheel
                        items={allowedAffiliates.map(a => ({
                          label: `${a.id === selectedAffiliate ? "✓ " : ""}${a.display_name}`,
                        }))}
                        columnLayout
                        fontSize={0.85}
                        textColor="#94a3b8"
                        activeColor="#f8fafc"
                        onActivate={index => {
                          const chosen = allowedAffiliates[index];
                          if (chosen) setSelectedAffiliate(chosen.id);
                          setShowScopePicker(false);
                        }}
                      />
                    </div>
                  )}

                  {showTooltip && (
                    <div
                      className="chat-tooltip-popover"
                      style={{
                        position: "absolute",
                        bottom: "45px",
                        left: "0",
                        width: "260px",
                        maxWidth: "80vw",
                        background: isEmbedded ? "#121316" : "var(--bg-surface)",
                        border: isEmbedded ? "1px solid rgba(0, 242, 254, 0.4)" : "1px solid var(--border-color)",
                        textDecoration: "none",
                        padding: "12px 14px",
                        borderRadius: "12px",
                        boxShadow: isEmbedded
                          ? "0 10px 25px -5px rgba(0, 0, 0, 0.7), 0 0 12px rgba(0, 242, 254, 0.15)"
                          : "0 10px 25px -5px rgba(0, 0, 0, 0.5)",
                        zIndex: 100,
                        fontSize: "12px",
                        color: "var(--text-main)",
                        lineHeight: "1.4"
                      }}
                    >
                      <button
                        type="button"
                        onClick={() => setShowTooltip(false)}
                        aria-label="Close"
                        style={{
                          all: "unset",
                          position: "absolute",
                          top: "8px",
                          right: "10px",
                          cursor: "pointer",
                          color: "var(--text-muted)",
                          fontSize: "14px",
                          lineHeight: 1,
                          padding: "2px"
                        }}
                      >
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                          <line x1="18" y1="6" x2="6" y2="18" />
                          <line x1="6" y1="6" x2="18" y2="18" />
                        </svg>
                      </button>
                      <strong style={{ color: "#38bdf8", display: "block", marginBottom: "6px", fontSize: "13px", paddingRight: "16px" }}>
                        Secure Index Tip:
                      </strong>
                      <ul style={{ margin: 0, paddingLeft: "16px", display: "flex", flexDirection: "column", gap: "6px", color: "var(--text-main)" }}>
                        <li>Please be as specific as possible when asking questions to get the most accurate responses.</li>
                        <li>Due to operating on cost-sensitive infrastructure, response times may vary or be unavailable due to model demand.</li>
                        {isEmbedded && (
                          <li>Powered by the <a href="https://www.sonicassistant.com" target="_blank" rel="noopener noreferrer">Sonic Assistant</a></li>
                        )}
                        <li>If you encounter any bugs, issues, or would like request access , please reach out to <a href="mailto:jackharper0517@outlook.com">jackharper0517@outlook.com</a>.</li>
                      </ul>
                    </div>
                  )}
                </div>

                <button
                  type="button"
                  className="circle-icon-button"
                  onClick={() => document.getElementById("file-upload")?.click()}
                >
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <line x1="12" y1="5" x2="12" y2="19" />
                    <line x1="5" y1="12" x2="19" y2="12" />
                  </svg>
                </button>

                <button
                  type={loading && !input.trim() ? "button" : "submit"}
                  className="circle-icon-button send-button"
                  onClick={loading && !input.trim() ? stopGeneration : undefined}
                  disabled={!loading && !input.trim()}
                  title={loading ? (input.trim() ? "Steer Sonic" : "Stop generating") : "Send"}
                >
                  {loading && !input.trim() ? (
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor">
                      <rect x="6" y="6" width="12" height="12" rx="2" />
                    </svg>
                  ) : (
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <polyline points="2 12 22 12" />
                      <polyline points="12 2 22 12 12 22" />
                    </svg>
                  )}
                </button>
              </div>
            </div>
          </form>
        </footer>

        {/* --- CONVERSATIONS BLADE (Root Level, mirrors Help panel's slide-in) --- */}
        {showConversations && (
          <div className={`conversations-panel-container ${conversationsClosing ? "closing" : ""}`}>
            <ConversationsBlade
              activeSessionId={sessionId}
              onSelect={switchConversation}
              onNew={startNewConversation}
              refreshKey={conversationsRefreshKey}
              onClose={toggleConversationsBlade}
            />
          </div>
        )}

        {/* --- MOBILE TRACE MODAL DRAWER (Root Level) --- */}
        {isEmbedded && isMobileTraceOpen && (
          <div className="mobile-trace-modal-root">
            <style>{`
              @media (min-width: 768px) {
                .mobile-trace-modal-root {
                  display: none !important;
                }
              }
            `}</style>
            <div 
              onClick={() => setIsMobileTraceOpen(false)}
              style={{
                position: 'fixed',
                inset: 0,
                background: 'rgba(0, 0, 0, 0.7)',
                backdropFilter: 'blur(4px)',
                zIndex: 999
              }}
            />
            <div className="mobile-trace-modal-content"
              style={{
                position: 'fixed',
                bottom: 0,
                left: 0,
                right: 0,
                maxHeight: '75vh',
                background: 'rgba(15, 23, 42, 0.98)',
                borderTop: '1px solid rgba(148, 163, 184, 0.3)',
                borderRadius: '20px 20px 0 0',
                padding: '1.25rem',
                color: '#e2e8f0',
                boxShadow: '0 -10px 30px rgba(0,0,0,0.5)',
                zIndex: 1000,
                overflowY: 'auto',
                boxSizing: 'border-box'
              }}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.75rem', borderBottom: '1px solid rgba(148, 163, 184, 0.2)', paddingBottom: '0.5rem' }}>
                <div>
                  <div style={{ fontSize: '0.95rem', fontWeight: 700 }}>How it works</div>
                  <div style={{ fontSize: '0.75rem', color: '#94a3b8' }}>Live execution trace</div>
                </div>
                <button
                  type="button"
                  onClick={() => setIsMobileTraceOpen(false)}
                  style={{ background: 'none', border: 'none', color: '#94a3b8', fontSize: '1.2rem', cursor: 'pointer', padding: '4px' }}
                >
                  ✕
                </button>
              </div>

              <div style={{ fontSize: '0.8rem', color: '#cbd5e1', marginBottom: '0.75rem', fontFamily: 'monospace' }}>
                {traceSteps.length
                  ? 'Tracing request...'
                  : 'Waiting for the first execution step...'}
              </div>

              <div style={{ display: 'flex', flexDirection: 'column', gap: '0.6rem' }}>
                {traceSteps.length === 0 && (
                  <div style={{ fontSize: '0.85rem', color: '#94a3b8' }}>
                    Start a request and the route will appear here.
                  </div>
                )}

                {traceSteps.map(step => (
                  <div className="trace-step"
                    key={step.id}
                    style={{
                      border: '1px solid rgba(148, 163, 184, 0.2)',
                      borderRadius: '10px',
                      padding: '0.7rem',
                      background: step.status === 'complete' ? 'rgba(30, 41, 59, 0.8)' : 'rgba(15, 23, 42, 0.95)'
                    }}
                  >
                    <div style={{ fontSize: '0.82rem', fontWeight: 700, display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
                      {step.title}
                      {step.batchSize && step.batchSize > 1 && (
                        <span style={{
                          fontSize: '0.65rem', fontWeight: 600, padding: '0.1rem 0.4rem', borderRadius: '999px',
                          background: 'rgba(56, 189, 248, 0.18)', color: '#38bdf8', border: '1px solid rgba(56, 189, 248, 0.4)',
                        }}>
                          ⚡ batched {step.batchIndex}/{step.batchSize}
                        </span>
                      )}
                    </div>
                    <div style={{ fontSize: '0.76rem', color: '#94a3b8', marginTop: '0.25rem' }}>
                      {step.detail}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}

        {/* --- DESKTOP TRACE SIDEBAR (Desktop Only) --- */}
        {isEmbedded && showTracePanel && (
          <aside className="desktop-trace-sidebar" style={{
            // The embedded widget only (everyone else gets the blade): the original floating panel beside the column, draggable.
            position: 'absolute',
            top: 0,
            left: 'calc(100% + 1.25rem)',
            transform: `translate3d(${tracePanelPos.x}px, ${tracePanelPos.y}px, 0)`,
            transition: isDraggingTrace ? 'none' : 'transform 0.1s ease',
            width: '320px',
            maxHeight: 'calc(100vh - 180px)',
            border: '1px solid rgba(148, 163, 184, 0.24)',
            borderRadius: '16px',
            background: 'rgba(15, 23, 42, 0.98)',
            padding: '1rem',
            color: '#e2e8f0',
            boxShadow: '0 12px 28px rgba(0,0,0,0.5)',
            overflowY: 'auto',
            zIndex: 900,
            boxSizing: 'border-box',
            userSelect: isDraggingTrace ? 'none' : 'auto'
          }}>
            <style>{`
              @media (max-width: 767px) {
                .desktop-trace-sidebar {
                  display: none !important;
                }
              }
            `}</style>
            <div
              onMouseDown={handleTraceMouseDown}
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                marginBottom: '0.75rem',
                cursor: isDraggingTrace ? 'grabbing' : 'grab',
                paddingBottom: '0.5rem',
                borderBottom: '1px solid rgba(148, 163, 184, 0.1)'
              }}
            >
              <div>
                <div style={{ fontSize: '0.9rem', fontWeight: 700 }}>How it works ⠿</div>
                <div style={{ fontSize: '0.75rem', color: '#94a3b8' }}>Live execution trace (Drag to move)</div>
              </div>
              <button
                type="button"
                className="circle-icon-button"
                onClick={toggleTracePanel}
                title="Hide trace panel"
                style={{ cursor: 'pointer' }}
              >
                ✕
              </button>
            </div>

            <div style={{ fontSize: '0.8rem', color: '#cbd5e1', marginBottom: '0.75rem', fontFamily: 'monospace' }}>
              {traceSteps.length
                ? 'Tracing request...'
                : 'Waiting for the first execution step...'}
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.6rem' }}>
              {traceSteps.length === 0 && (
                <div style={{ fontSize: '0.85rem', color: '#94a3b8' }}>
                  Start a request and the route will appear here.
                </div>
              )}

              {traceSteps.map(step => (
                <div className="trace-step"
                  key={step.id}
                  style={{
                    border: '1px solid rgba(148, 163, 184, 0.2)',
                    borderRadius: '10px',
                    padding: '0.7rem',
                    background: step.status === 'complete' ? 'rgba(30, 41, 59, 0.8)' : 'rgba(15, 23, 42, 0.95)'
                  }}
                >
                  <div style={{ fontSize: '0.82rem', fontWeight: 700, display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
                    {step.title}
                    {step.batchSize && step.batchSize > 1 && (
                      <span style={{
                        fontSize: '0.65rem', fontWeight: 600, padding: '0.1rem 0.4rem', borderRadius: '999px',
                        background: 'rgba(56, 189, 248, 0.18)', color: '#38bdf8', border: '1px solid rgba(56, 189, 248, 0.4)',
                      }}>
                        ⚡ batched {step.batchIndex}/{step.batchSize}
                      </span>
                    )}
                  </div>
                  <div style={{ fontSize: '0.76rem', color: '#94a3b8', marginTop: '0.25rem' }}>
                    {step.detail}
                  </div>
                </div>
              ))}
            </div>
          </aside>
        )}
        {showFolderConsent && (
          <LocalFolderConsent
            onCancel={() => setShowFolderConsent(false)}
            onAccept={() => {
              recordLocalFolderConsent();
              setShowFolderConsent(false);
              void workspace.connect();
            }}
          />
        )}
        {showClearConfirm && (
          <div style={{
            position: 'fixed',
            inset: 0,
            backgroundColor: 'rgba(0, 0, 0, 0.75)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 1000,
            backdropFilter: 'blur(4px)'
          }}>
            <div style={{
              background: 'var(--bg-surface)',
              border: '1px solid var(--border-color)',
              borderRadius: '12px',
              padding: '1.5rem',
              width: '100%',
              maxWidth: '400px',
              color: 'var(--text-main)',
              boxShadow: '0 20px 25px -5px rgba(0, 0, 0, 0.5)'
            }}>
              <h3 style={{ margin: '0 0 0.5rem 0', fontSize: '1.1rem', fontWeight: 600 }}>
                Delete this conversation?
              </h3>
              <p style={{ margin: '0 0 1.25rem 0', fontSize: '0.85rem', color: 'var(--text-muted)' }}>
                This clears the chat and removes it from your conversations list. This can't be undone.
              </p>
              <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '0.5rem' }}>
                <button
                  type="button"
                  onClick={() => setShowClearConfirm(false)}
                  style={{
                    padding: '0.4rem 0.8rem',
                    borderRadius: '6px',
                    background: 'transparent',
                    border: '1px solid var(--border-color)',
                    color: 'var(--text-main)',
                    cursor: 'pointer',
                    fontSize: '0.85rem'
                  }}
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={confirmClearChat}
                  style={{
                    padding: '0.4rem 0.8rem',
                    borderRadius: '6px',
                    background: '#ef4444',
                    border: 'none',
                    color: '#ffffff',
                    fontWeight: 500,
                    cursor: 'pointer',
                    fontSize: '0.85rem'
                  }}
                >
                  Delete Conversation
                </button>
              </div>
            </div>
          </div>
        )}
        {feedbackModal.isOpen && (
          <div style={{
            position: 'fixed',
            inset: 0,
            backgroundColor: 'rgba(0, 0, 0, 0.75)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 1000,
            backdropFilter: 'blur(4px)'
          }}>
            <div style={{
              background: 'var(--bg-surface)',
              border: '1px solid var(--border-color)',
              borderRadius: '12px',
              padding: '1.5rem',
              width: '100%',
              maxWidth: '450px',
              color: 'var(--text-main)',
              boxShadow: '0 20px 25px -5px rgba(0, 0, 0, 0.5)'
            }}>
              <h3 style={{ margin: '0 0 0.5rem 0', fontSize: '1.1rem', fontWeight: 600 }}>
                Specify Negative Feedback
              </h3>
              <p style={{ margin: '0 0 1rem 0', fontSize: '0.85rem', color: 'var(--text-muted)' }}>
                Help improve future responses by categorizing and explaining the issue.
              </p>

              <form onSubmit={handleSubmitNegativeFeedback}>
                <div style={{ marginBottom: '1rem' }}>
                  <label style={{ display: 'block', fontSize: '0.8rem', marginBottom: '0.4rem', color: 'var(--text-main)' }}>
                    Category Tag
                  </label>
                  <select
                    value={feedbackTag}
                    onChange={(e) => setFeedbackTag(e.target.value)}
                    style={{
                      width: '100%',
                      padding: '0.5rem',
                      borderRadius: '6px',
                      background: 'var(--bg-main)',
                      border: '1px solid var(--border-color)',
                      color: 'var(--text-main)',
                      fontSize: '0.875rem'
                    }}
                  >
                    <option value="hallucination">Hallucination / Inaccurate Fact</option>
                    <option value="incorrect_filter">Incorrect Document/Affiliate Filter</option>
                    <option value="formatting">Formatting / Markdown Issue</option>
                    <option value="incomplete">Incomplete or Truncated Output</option>
                    <option value="other">Other</option>
                  </select>
                </div>

                <div style={{ marginBottom: '1.25rem' }}>
                  <label style={{ display: 'block', fontSize: '0.8rem', marginBottom: '0.4rem', color: 'var(--text-main)' }}>
                    Reason / Details
                  </label>
                  <textarea
                    value={feedbackReason}
                    onChange={(e) => setFeedbackReason(e.target.value)}
                    placeholder="Explain what was wrong with the response..."
                    rows={4}
                    required
                    style={{
                      width: '100%',
                      padding: '0.5rem',
                      borderRadius: '6px',
                      background: 'var(--bg-main)',
                      border: '1px solid var(--border-color)',
                      color: 'var(--text-main)',
                      fontSize: '0.875rem',
                      resize: 'vertical',
                      boxSizing: 'border-box'
                    }}
                  />
                </div>

                <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '0.5rem' }}>
                  <button
                    type="button"
                    onClick={() => setFeedbackModal({ isOpen: false, messageId: null })}
                    style={{
                      padding: '0.4rem 0.8rem',
                      borderRadius: '6px',
                      background: 'transparent',
                      border: '1px solid var(--border-color)',
                      color: 'var(--text-main)',
                      cursor: 'pointer',
                      fontSize: '0.85rem'
                    }}
                  >
                    Cancel
                  </button>
                  <button
                    type="submit"
                    style={{
                      padding: '0.4rem 0.8rem',
                      borderRadius: '6px',
                      background: '#ef4444',
                      border: 'none',
                      color: '#ffffff',
                      fontWeight: 500,
                      cursor: 'pointer',
                      fontSize: '0.85rem'
                    }}
                  >
                    Submit Feedback
                  </button>
                </div>
              </form>
            </div>
          </div>
        )}
      </main>

      {/* The execution trace: a blade down the right side on a wide screen (it pushes the conversation over), a bottom sheet on a phone. */}
      {!isEmbedded && !isMobileView && showTracePanel && (
        <aside className="trace-blade" aria-label="Execution trace">
          <TracePanel steps={tracePanelSteps} running={loading} onClose={toggleTracePanel} />
        </aside>
      )}
      {!isEmbedded && isMobileView && isMobileTraceOpen && (
        <>
          <div className="trace-sheet-backdrop" onClick={() => setIsMobileTraceOpen(false)} />
          <div className="trace-sheet" role="dialog" aria-label="Execution trace">
            <div className="trace-sheet-grip" />
            <TracePanel steps={tracePanelSteps} running={loading} onClose={() => setIsMobileTraceOpen(false)} />
          </div>
        </>
      )}
    </div>  
  );
}
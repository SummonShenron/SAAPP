import React, { useEffect, useState, useRef } from 'react';
import sonicImg from '../assets/sonicandshadow.jpg';
import { Filters } from '../components/Filters';
import { getDynamicExampleQuestions } from '../utils/Example_List';
import { api, BASE_URL, getAuthHeaders, getEffectivePrincipal } from '../api'; 
import ReactMarkdown from 'react-markdown';
import sonicSpinImg from '../assets/sonic-rolling.gif';
import shadowSpinImg from '../assets/shadow.gif';
import { useAuth } from '@clerk/clerk-react';

interface Message {
  id: string;
  sender: 'user' | 'ai' | 'system';
  text: string;
  feedback?: 'like' | 'dislike' | null;
}

interface ChatPageProps {
  theme: "sonic" | "shadow";
  toggleTheme: () => void;
}

interface TraceStep {
  id: string;
  title: string;
  detail: string;
  status: "active" | "complete";
}

export const ChatPage: React.FC<ChatPageProps> = ({ theme, toggleTheme }) => {
  const hashSearch = window.location.hash.includes('?') ? window.location.hash.split('?')[1] : '';
  const searchParams = new URLSearchParams(window.location.search || hashSearch);
  const isEmbedded = searchParams.get('mode') === 'embed';

  // Trace is available by default in standalone mode, but starts hidden when embedded.
  const [showTracePanel, setShowTracePanel] = useState(() => {
    if (isEmbedded) return false;
    if (typeof window !== 'undefined') {
      const saved = localStorage.getItem('showTracePanel');
      if (saved !== null) return JSON.parse(saved);
    }
    return true; 
  });

  const toggleTracePanel = () => {
    setShowTracePanel((prev: boolean) => {
      const next = !prev;
      localStorage.setItem('showTracePanel', JSON.stringify(next));
      return next;
    });
  };
  const [traceSteps, setTraceSteps] = useState<TraceStep[]>([]);
  const { isLoaded, isSignedIn, getToken } = useAuth();
  const principal = getEffectivePrincipal();
  const [selectedAffiliate, setSelectedAffiliate] = useState<string>('All');
  const [allowedAffiliates, setAllowedAffiliates] = useState<string[]>([]);
  const [userEmail, setUserEmail] = useState<string>('');
  const [activeTab, setActiveTab] = useState<'chat' | 'self-service' | 'saved-conversations'>('chat');
  const [agentStatus, setAgentStatus] = useState<string>('');
  const [agentPath, setAgentPath] = useState<string[]>([]);
  const [attachedFiles, setAttachedFiles] = useState<File[]>([]);
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
  const chatStorageKey = isEmbedded
    ? `chat-messages-${principal}-${embedVisitorId}`
    : `chat-messages-${principal}`;

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
  const [isMobileTraceOpen, setIsMobileTraceOpen] = useState(false);
  const [latestStepTitle, setLatestStepTitle] = useState("");
  const nodeQueueRef = useRef<{ node: string; detail?: string }[]>([]);
  const isProcessingQueue = useRef<boolean>(false);

  const [showTooltip, setShowTooltip] = useState(false);
  const tooltipRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (tooltipRef.current && !tooltipRef.current.contains(event.target as Node)) {
        setShowTooltip(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const [sessionId] = useState<string>(() => {
    if (!isEmbedded) return crypto.randomUUID();
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
  });
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

  const [input, setInput] = useState<string>('');
  const [loading, setLoading] = useState<boolean>(false);
  const [currentExampleQuestions, setCurrentExampleQuestions] = useState<string[]>([]);
  const [loadingCards, setLoadingCards] = useState<boolean>(false);
  const [attachments, setAttachments] = useState<{ filename: string; content: string }[]>([]);
  const attachmentsRef = useRef<{ filename: string; content: string }[]>([]);
  const principalKey = (localStorage.getItem('principal') || '').toLowerCase();
  const guestBtyDetected = principalKey === 'guest_bty' || !!localStorage.getItem('guest_token');
  const affiliateDActive = selectedAffiliate === 'Affiliate_D' || embedAffiliate === 'Affiliate_D';
  const chatPlaceholder = guestBtyDetected
    ? 'Ask Madison\'s Assistant...'
    : affiliateDActive
      ? 'Ask a question against the BTY knowledge base...'
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
          setUserEmail(`${principal.toLowerCase()}@entra.local`);
        }
      } catch (err) {
        console.error("Failed to sync user claims:", err);
      }
    };
    syncUserClaims();
  }, [principal]);

  const handleClearChat = async () => {
    setMessages([
      { id: genId(), sender: 'system', text: `What would you like to find out about, ${principal}?` }
    ]);
    setSelectedAffiliate(isEmbedded ? embedAffiliate : 'All');
    setAgentStatus('');
    setAgentPath([]);
    setHasChatted(false);
    localStorage.removeItem(chatStorageKey);
    try {
      const authHeaders = await getAuthHeaders();
      await fetch(`${BASE_URL}/api/chat/clear`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...authHeaders
        },
        body: JSON.stringify({ username: principal, session_id: sessionId })
      });
    } catch (e) {
      console.warn("Backend persistent clearance was skipped (server offline).");
    }
    setAttachments([]);
    setAttachedFiles([]);
    try {
      const questions = await getDynamicExampleQuestions(
      allowedAffiliates,
      isEmbedded ? embedAffiliate : 'All'
    );
    setCurrentExampleQuestions(questions);
    } catch (error) {
      console.error("Failed to fetch dynamic example questions:", error);
    }
  };

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files) return;

    const fileArray = Array.from(files);
    setUploadedFiles(prev => [...prev, ...fileArray]);

    const file = fileArray[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onload = async () => {
      const result = reader.result as string;
      const base64 = result.split(",")[1];

      setAttachments(prev => [...prev, { filename: file.name, content: base64 }]);
      setAttachedFiles(prev => [...prev, file]);

      await fetch(`${BASE_URL}api/upload-attachment`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          username: principal,
          session_id: sessionId,
          filename: file.name,
          content: base64,
        }),
      });
    };
    reader.readAsDataURL(file);
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
    localStorage.setItem(chatStorageKey, JSON.stringify(messages));
  }, [messages, chatStorageKey]);

  useEffect(() => {
    if (allowedAffiliates.length === 0) return;
    const syncQuestionPool = async () => {
      setLoadingCards(true);
      const questions = await getDynamicExampleQuestions(allowedAffiliates, selectedAffiliate);
      setCurrentExampleQuestions(questions);
      setLoadingCards(false);
    };
    syncQuestionPool();
  }, [allowedAffiliates, selectedAffiliate]);

  useEffect(() => {
    const scrollToBottom = () => {
      if (messagesEndRef.current) {
        messagesEndRef.current.scrollIntoView({
          behavior: loading ? 'auto' : 'smooth',
          block: 'end', 
        });
      }
    };
    scrollToBottom();
    const timer = setTimeout(scrollToBottom, 50);
    return () => clearTimeout(timer);
  }, [messages, loading]);

  const getNodeLabel = (nodeName: string): string => {
    switch (nodeName) {
      case 'coordinator_node': return 'Analyzing intent and planning route...';
      case 'reasoner_node': return 'Evaluating request context...';
      case 'memory_node': return 'Updating memory and preferences...';
      case 'retrieve_node': return 'GraphRAG Retrieval in progress...';
      case 'grade_documents_node': return 'Evaluating document relevance...';
      case 'rewrite_query_node': return 'Refining search parameters...';
      case 'summarizer_node': return 'Summarizing retrieved documents...';
      case 'formatter_node': return 'Formatting output structure...';
      case 'conversational_node': return 'Generating response...';
      case 'generate_node': return 'Collecting rings and generating tokens...';
      case 'paapp_node': return 'Processing schedule and task data...';
      case 'web_search_node': return 'Searching the web for real-time info...';
      case 'code_interpreter_node': return 'Executing query in code interpreter...';
      case 'github_search': return 'Searching GitHub repositories...';
      case 'pr_summary': return 'Analyzing pull request changes...';
      case 'draft_pr_node': return 'Drafting pull request...';
      case 'execute_pr_node': return 'Executing pull request action...';
      case 'snapshot_node': return 'Taking analytical data snapshot...';
      case 'classifier_node': return 'Classifying activity patterns...';
      case 'pattern_node': return 'Detecting behavioral patterns...';
      case 'trend_node': return 'Analyzing metrics and trends...';
      case 'insight_query_node': return 'Synthesizing data insights...';
      default: return nodeName ? `Processing step: ${nodeName}` : 'Collecting rings and tokens...';
    }
  };

  const addTraceStep = (payload: any) => {
    const title = payload?.title || payload?.message || "Agent step";
    const detail = payload?.detail || payload?.message || "";
    const id = `${title}-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
    setLatestStepTitle(title);
    setTraceSteps(prev => [
      ...prev,
      { id, title, detail, status: payload?.status === "complete" ? "complete" : "active" }
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
    const friendlyLabel = getNodeLabel(item.node);

    setAgentStatus(item.node);
    setAgentPath(prev => (prev.includes(item.node) ? prev : [...prev, item.node]));
    markTraceComplete();
    setLatestStepTitle(friendlyLabel);

    addTraceStep({
      title: friendlyLabel,
      detail: item.detail || `Node: ${item.node}`,
      status: 'active'
    });

    setTimeout(() => {
      processNodeQueue();
    }, 700);
  };

  const handleSendMessage = async (
    textToSend: string,
    currentAttachments: { filename: string; content: string }[]
  ) => {
    setTraceSteps([]);
    if (!textToSend.trim() || loading) return;

    setMessages(prev => [
      ...prev,
      { id: genId(), sender: 'user', text: textToSend },
      { id: genId(), sender: 'ai', text: '' }
    ]);

    setHasChatted(true);
    setInput('');
    setLoading(true);
    setAgentStatus('Running at the speed of sound');
    setAgentPath([]);

    try {
      await api.sendChatMessage(
        principal,
        textToSend,
        attachments,
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
                nodeQueueRef.current.push({ node: payload.node, detail: payload.detail });
                if (!isProcessingQueue.current) {
                  processNodeQueue();
                }
              }
              if (payload.event === 'token') {
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
              if (payload.event === 'final_generation') {
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
                addTraceStep({ title: "Execution error", detail: payload.message, status: "active" });
                markTraceComplete();
              }
            } catch (jsonErr) {
              console.warn("Skipping partial, non-JSON SSE chunk buffer:", jsonErr);
            }
          }
        }
      );
    } catch (err) {
      console.error("Chat send failed:", err);
      setMessages(prev => [
        ...prev,
        { id: genId(), sender: 'ai', text: "Vector assertion timed out. Check local engine allocations." }
      ]);
    } finally {
      setLoading(false);
      setAgentStatus('');
    }
  };

  const onSubmitForm = (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim()) return;
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
  
  return (
  <div>
    {/* HERO BANNER (hidden entirely in embed mode) */}
    {!isEmbedded && (
      <div className="hero-banner" style={{ backgroundImage: `linear-gradient(rgba(18, 24, 36, 0.7), rgba(18, 24, 36, 0.95)), url(${sonicImg})` }}>
        <div className="banner-context">
          <h3>{theme === 'sonic' ? 'Sonic Assistant' : 'Shadow Engine'}</h3>
          <h4>{theme === 'sonic' ? 'Rolling around at the speed of sound.' : 'Behold the Ultimate Power.'}</h4>
          {userEmail && <p className="badge">Principal Account Identity: {userEmail}</p>}
        </div>
      </div>
    )}
      {/* PORTAL BODY */}
      <main className={`portal-body ${!hasChatted ? 'initial-state-view' : ''}`}>

        {/* 1. INITIAL STATE / CARDS */}
        {!hasChatted && (
          <div className="example-cards-container">
            {loadingCards ? (
              <div style={{ color: '#64748b', fontSize: '0.85rem', padding: '1rem' }}>
                Querying directory indices for security group context...
              </div>
            ) : (
              currentExampleQuestions.map((q, idx) => (
                <div key={idx} className="query-card" onClick={() => !loading && handleSendMessage(q, attachmentsRef.current)}>
                  <p>{q}</p>
                  <span>→</span>
                </div>
              ))
            )}
          </div>
        )}

        {/* 2. CHAT MESSAGES WINDOW */}
        {hasChatted && (
          <div
              className="chat-window"
              ref={chatWindowRef}
              style={isEmbedded ? undefined : { maxHeight: 'calc(100vh - 380px)', overflowY: 'auto' }}
            >
            {messages
              .filter(msg => !(hasChatted && msg.sender === 'system'))
              .map(msg => (
                <div key={msg.id} className={`message-bubble ${msg.sender}`}>
                  <div className="message-sender">{msg.sender.toUpperCase()}</div>
                  <div className="message-text">
                    <ReactMarkdown
                      components={{
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
                                } else if (getToken) {
                                  token = await getToken();
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
                      {msg.text}
                    </ReactMarkdown>
                  </div>
                  {msg.sender === 'ai' && !loading && msg.text && (
                    <div className="feedback-actions" style={{ display: 'flex', gap: '6px', marginTop: '8px' }}>
                      <button 
                        type="button"
                        onClick={() => handleFeedback(msg.id, 'like')}
                        className={`circle-icon-button ${msg.feedback === 'like' ? 'active' : ''}`}
                        title="Helpful"
                        style={msg.feedback === 'like' ? { color: '#22c55e', borderColor: '#22c55e', background: '22c55e' } : {}}
                      >
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                          <path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3" />
                        </svg>
                      </button>

                      <button 
                        type="button"
                        onClick={() => handleFeedback(msg.id, 'dislike')}
                        className={`circle-icon-button ${msg.feedback === 'dislike' ? 'active' : ''}`}
                        title="Not helpful"
                        style={msg.feedback === 'dislike' ? { color: '#ef4444', borderColor: '#ef4444' } : {}}
                      >
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                          <path d="M10 15v4a3 3 0 0 0 3 3l4-9V2H5.72a2 2 0 0 0-2 1.7l-1.38 9a2 2 0 0 0 2 2.3zm7-13h3a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2h-3" />
                        </svg>
                      </button>
                    </div>
                  )}
                </div>
              ))}

            {loading && (
              isEmbedded ? (
                <div className="bty-loader-container">
                  <div className="bty-loader-dots">
                    <span></span>
                    <span></span>
                    <span></span>
                  </div>
                  <div className="loading-text">
                    {getNodeLabel(agentStatus) || "Thinking..."}
                  </div>
                </div>
              ) : (
                <div className="sonic-loader-container">
                  <img
                    src={theme === 'sonic' ? sonicSpinImg : shadowSpinImg}
                    alt="loading"
                    style={{ width: '48px', height: '48px' }}
                  />
                  <div className="loading-text">
                    {getNodeLabel(agentStatus) || "Collecting rings and tokens..."}
                  </div>
                </div>
              )
            )}
            <div ref={messagesEndRef} />
          </div>
        )}

        {/* 3. INPUT AREA & FOOTER */}
        <footer className="controls-footer" style={{ position: 'relative' }}>
          {/* MOBILE TRACE PILL (Pinned inside top of footer) */}
          <div className="mobile-trace-pill-container">
            <style>{`
              @media (min-width: 768px) {
                .mobile-trace-pill-container {
                  display: none !important;
                }
              }
            `}</style>

            {showTracePanel && (
              <div style={{ display: 'flex', justifyContent: 'center', width: '100%', marginBottom: '8px' }}>
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
                    zIndex: 50
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

          <form onSubmit={onSubmitForm} className="chat-input-area">
            <input
              id="file-upload"
              type="file"
              multiple
              style={{ display: "none" }}
              onChange={(e) => handleFileUpload(e)}
            />

            <div className="chat-input-wrapper">
              <textarea
                className="chat-textarea"
                placeholder={chatPlaceholder}
                value={input}
                onChange={(e) => {
                  setInput(e.target.value);
                  e.target.style.height = "auto";
                  e.target.style.height = `${e.target.scrollHeight}px`;
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    onSubmitForm(e);
                  }
                }}
                disabled={loading}
                rows={2}
              />

              <div className="icon-row-overlay">
                <div style={{ position: "relative" }}>
                  <button 
                    type="button" 
                    className="circle-icon-button"
                    onClick={() => setShowTooltip(!showTooltip)}
                    title="Help / Info"
                  >
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <circle cx="12" cy="12" r="9" />
                      <line x1="12" y1="8" x2="12" y2="12" />
                      <line x1="12" y1="16" x2="12.01" y2="16" />
                    </svg>
                  </button>

                  {showTooltip && (
                    <div className="chat-tooltip-popover" style={{
                      position: "absolute",
                      bottom: "45px",
                      right: "0",
                      width: "220px",
                      background: "var(--card-bg, #ffffff)",
                      border: "1px solid var(--border-color, #e2e8f0)",
                      padding: "10px 14px",
                      borderRadius: "8px",
                      boxShadow: "0 4px 12px rgba(0,0,0,0.15)",
                      zIndex: 100,
                      fontSize: "13px",
                      color: "var(--text-main, #1e293b)",
                      lineHeight: "1.4"
                    }}>
                      <strong>Secure Index Tip:</strong>
                      <p style={{ margin: "8px 8px 8px" }}>
                        <ul>
                          <li>Please only begin your queries once you see the example questions and the green (or yellow in dark mode) username in the top left indicating your session permissions are set.[cite: 2]</li>
                          <li>If the example questions have not loaded yet, it means the backend is still spinning up due to inactivity. please wait for the app to be fully loaded before using.[cite: 2]</li>
                          <li>Due to operating on cost-sensitive infrastructure, response times may vary or be unavailable due to model demand.[cite: 2]</li>
                          <li>If you encounter any bugs, issues, or would like your permissions changed, please reach out to jackharper0517@outlook.com.[cite: 2]</li>
                        </ul>
                      </p>
                    </div>
                  )}
                </div>

                <button
                  type="button"
                  className={`circle-icon-button ${showTracePanel ? 'trace-active' : ''}`}
                  onClick={toggleTracePanel}
                  title="Toggle execution trace"
                  style={showTracePanel ? {
                    background: '#3b82f6',
                    border: '1px solid #3b82f6',
                    boxShadow: '0 0 8px rgba(99, 102, 241, 0.5)'
                  } : {}}
                >
                  <style>{`
                    .circle-icon-button.trace-active svg,
                    .circle-icon-button.trace-active svg * {
                      stroke: #ffffff !important;
                    }
                  `}</style>
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M4 5h16" />
                    <path d="M7 10h10" />
                    <path d="M10 15h4" />
                  </svg>
                </button>

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
                  type="button"
                  className="circle-icon-button"
                  onClick={handleExportChat}
                  disabled={loading || !hasChatted}
                >
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                    <polyline points="7 10 12 15 17 10" />
                    <line x1="12" y1="15" x2="12" y2="3" />
                  </svg>
                </button>

                <button
                  type="submit"
                  className="circle-icon-button"
                  disabled={loading || !input.trim()}
                >
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <polyline points="2 12 22 12" />
                    <polyline points="12 2 22 12 12 22" />
                  </svg>
                </button>

                <button
                  type="button"
                  className="circle-icon-button"
                  onClick={handleClearChat}
                  disabled={loading}
                >
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <polyline points="3 6 5 6 21 6" />
                    <path d="M19 6l-2 14H7L5 6" />
                    <line x1="10" y1="11" x2="10" y2="17" />
                    <line x1="14" y1="11" x2="14" y2="17" />
                  </svg>
                </button>
              </div>
            </div>
          </form>
          {!isEmbedded && (
            <Filters 
              selectedAffiliate={selectedAffiliate}
              setSelectedAffiliate={setSelectedAffiliate}
              loadingChat={loading}
              allowedAffiliates={allowedAffiliates}
              setAllowedAffiliates={setAllowedAffiliates}
            />
          )}
        </footer>

        {/* --- MOBILE TRACE MODAL DRAWER (Root Level) --- */}
        {isMobileTraceOpen && (
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
            <div
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
                  ? traceSteps.map(step => step.title).join(' → ')
                  : 'Waiting for the first execution step...'}
              </div>

              <div style={{ display: 'flex', flexDirection: 'column', gap: '0.6rem' }}>
                {traceSteps.length === 0 && (
                  <div style={{ fontSize: '0.85rem', color: '#94a3b8' }}>
                    Start a request and the route will appear here.
                  </div>
                )}

                {traceSteps.map(step => (
                  <div
                    key={step.id}
                    style={{
                      border: '1px solid rgba(148, 163, 184, 0.2)',
                      borderRadius: '10px',
                      padding: '0.7rem',
                      background: step.status === 'complete' ? 'rgba(30, 41, 59, 0.8)' : 'rgba(15, 23, 42, 0.95)'
                    }}
                  >
                    <div style={{ fontSize: '0.82rem', fontWeight: 700 }}>{step.title}</div>
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
        {showTracePanel && (
          <aside className="desktop-trace-sidebar" style={{
            position: 'absolute',
            top: 0,
            left: 'calc(100% + 1.25rem)',
            width: '320px',
            maxHeight: 'calc(100vh - 180px)',
            border: '1px solid rgba(148, 163, 184, 0.24)',
            borderRadius: '16px',
            background: 'rgba(15, 23, 42, 0.98)',
            padding: '1rem',
            color: '#e2e8f0',
            boxShadow: '0 12px 28px rgba(0,0,0,0.5)',
            overflowY: 'auto',
            zIndex: 100,
            boxSizing: 'border-box'
          }}>
            <style>{`
              @media (max-width: 767px) {
                .desktop-trace-sidebar {
                  display: none !important;
                }
              }
            `}</style>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.75rem' }}>
              <div>
                <div style={{ fontSize: '0.9rem', fontWeight: 700 }}>How it works</div>
                <div style={{ fontSize: '0.75rem', color: '#94a3b8' }}>Live execution trace</div>
              </div>
              <button
                type="button"
                className="circle-icon-button"
                onClick={toggleTracePanel}
                title="Hide trace panel"
              >
                ✕
              </button>
            </div>

            <div style={{ fontSize: '0.8rem', color: '#cbd5e1', marginBottom: '0.75rem', fontFamily: 'monospace' }}>
              {traceSteps.length
                ? traceSteps.map(step => step.title).join(' → ')
                : 'Waiting for the first execution step...'}
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.6rem' }}>
              {traceSteps.length === 0 && (
                <div style={{ fontSize: '0.85rem', color: '#94a3b8' }}>
                  Start a request and the route will appear here.
                </div>
              )}

              {traceSteps.map(step => (
                <div
                  key={step.id}
                  style={{
                    border: '1px solid rgba(148, 163, 184, 0.2)',
                    borderRadius: '10px',
                    padding: '0.7rem',
                    background: step.status === 'complete' ? 'rgba(30, 41, 59, 0.8)' : 'rgba(15, 23, 42, 0.95)'
                  }}
                >
                  <div style={{ fontSize: '0.82rem', fontWeight: 700 }}>{step.title}</div>
                  <div style={{ fontSize: '0.76rem', color: '#94a3b8', marginTop: '0.25rem' }}>
                    {step.detail}
                  </div>
                </div>
              ))}
            </div>
          </aside>
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
              background: '#1e293b',
              border: '1px solid rgba(148, 163, 184, 0.2)',
              borderRadius: '12px',
              padding: '1.5rem',
              width: '100%',
              maxWidth: '450px',
              color: '#f8fafc',
              boxShadow: '0 20px 25px -5px rgba(0, 0, 0, 0.5)'
            }}>
              <h3 style={{ margin: '0 0 0.5rem 0', fontSize: '1.1rem', fontWeight: 600 }}>
                Specify Negative Feedback
              </h3>
              <p style={{ margin: '0 0 1rem 0', fontSize: '0.85rem', color: '#94a3b8' }}>
                Help improve future responses by categorizing and explaining the issue.
              </p>

              <form onSubmit={handleSubmitNegativeFeedback}>
                <div style={{ marginBottom: '1rem' }}>
                  <label style={{ display: 'block', fontSize: '0.8rem', marginBottom: '0.4rem', color: '#cbd5e1' }}>
                    Category Tag
                  </label>
                  <select
                    value={feedbackTag}
                    onChange={(e) => setFeedbackTag(e.target.value)}
                    style={{
                      width: '100%',
                      padding: '0.5rem',
                      borderRadius: '6px',
                      background: '#0f172a',
                      border: '1px solid #334155',
                      color: '#f8fafc',
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
                  <label style={{ display: 'block', fontSize: '0.8rem', marginBottom: '0.4rem', color: '#cbd5e1' }}>
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
                      background: '#0f172a',
                      border: '1px solid #334155',
                      color: '#f8fafc',
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
                      border: '1px solid #475569',
                      color: '#cbd5e1',
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
    </div>  
  );
}
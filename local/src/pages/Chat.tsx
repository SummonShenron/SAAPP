import React, { useEffect, useState, useRef } from 'react';
import sonicImg from '../assets/sonicandshadow.jpg';
import { Filters } from '../components/Filters';
import { getDynamicExampleQuestions } from '../utils/Example_List';
import { api } from '../api'; 

import sonicSpinImg from '../assets/sonic-rolling.gif';
import shadowSpinImg from '../assets/shadow.gif';

interface Message {
  id: string;
  sender: 'user' | 'ai' | 'system';
  text: string;
}

interface ChatPageProps {
  theme: "sonic" | "shadow";
  toggleTheme: () => void;
}


export const ChatPage: React.FC<ChatPageProps> = ({ theme, toggleTheme }) => {
  const principal = localStorage.getItem("principal") ?? "";
  const [selectedAffiliate, setSelectedAffiliate] = useState<string>('All');
  const [allowedAffiliates, setAllowedAffiliates] = useState<string[]>([]);
  const [userEmail, setUserEmail] = useState<string>('');
  const [activeTab, setActiveTab] = useState<'chat' | 'self-service' | 'saved-conversations'>('chat');
  const [agentStatus, setAgentStatus] = useState<string>('');
  const [agentPath, setAgentPath] = useState<string[]>([]);
  const [attachedFiles, setAttachedFiles] = useState<File[]>([]);
  const BASE_URL = "https://saapp.onrender.com/";
  const [showTooltip, setShowTooltip] = useState(false);
    const tooltipRef = useRef<HTMLDivElement>(null);

    // Optional: Close tooltip when clicking outside of it
    useEffect(() => {
        function handleClickOutside(event: MouseEvent) {
            if (tooltipRef.current && !tooltipRef.current.contains(event.target as Node)) {
                setShowTooltip(false);
            }
        }
        document.addEventListener("mousedown", handleClickOutside);
        return () => document.removeEventListener("mousedown", handleClickOutside);
    }, []);
  const [sessionId, setSessionId] = useState<string>(() => {
    // new conversation → fresh ID
    return crypto.randomUUID();
  });
  const genId = () => crypto.randomUUID();
  const [uploadedFiles, setUploadedFiles] = useState<File[]>([]);
  // 1. Warm-initialize the messages state from localStorage to prevent auto-clearing
  const [messages, setMessages] = useState<Message[]>(() => {
    const persistedHistory = localStorage.getItem(`chat-messages-${principal}`);
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
    // initialize from persisted messages safely
    try {
      const persisted = localStorage.getItem(`chat-messages-${principal}`);
      if (persisted) {
        const parsed: Message[] = JSON.parse(persisted);
        return parsed.some(m => m.sender === 'user');
      }
    } catch {
      // fall through
    }
    // fallback to current messages array
    return messages.some(msg => msg.sender === 'user');
  });
  const [input, setInput] = useState<string>('');
  const [loading, setLoading] = useState<boolean>(false);
  const [currentExampleQuestions, setCurrentExampleQuestions] = useState<string[]>([]);
  const [loadingCards, setLoadingCards] = useState<boolean>(false);
  const [attachments, setAttachments] = useState<{ filename: string; content: string }[]>([]);
  const attachmentsRef = useRef<{ filename: string; content: string }[]>([]);
  const handleRemoveAttachment = (idx: number) => {
        setUploadedFiles(prev => prev.filter((_, i) => i !== idx));
        setAttachments(prev => prev.filter((_, i) => i !== idx));
    };
  useEffect(() => {
    attachmentsRef.current = attachments;
  }, [attachments]);

  const chatWindowRef = useRef<HTMLDivElement>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  // const toggleTheme = () => {
  //   setTheme(prev => (prev === 'sonic' ? 'shadow' : 'sonic'));
  // };

  // Synchronize secure directory claims from simulated Entra ID
  useEffect(() => {
    const syncUserClaims = async () => {
      if (!principal) return; // <-- FIX

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


  // 2. Clear history updates both reactive states, client localStorage, and backend database
  const handleClearChat = async () => {
    setMessages([
      { id: genId(), sender: 'system', text: `What would you like to find out about, ${principal}?` }
    ]);
    setSelectedAffiliate('All');
    setAgentStatus('');
    setAgentPath([]);
    setHasChatted(false)
    localStorage.removeItem(`chat-messages-${principal}`);
    try {
      // Direct call to purge the persisted memory on your local-RAG backend API
      await fetch('https://saapp.onrender.com/api/chat/clear', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: principal })
      });
    } catch (e) {
      console.warn("Backend persistent clearance was skipped (server offline).");
    }
    setAttachments([]);
    setAttachedFiles([]);
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

      // 1. Store locally for UI + chat send
      setAttachments(prev => [...prev, { filename: file.name, content: base64 }]);
      setAttachedFiles(prev => [...prev, file]);

      // 2. Upload to backend with session_id
      await fetch(`${BASE_URL}api/upload-attachment`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          username: principal,
          session_id: sessionId,        // ← CRITICAL
          filename: file.name,
          content: base64,
        }),
      });
    };

    reader.readAsDataURL(file);
  };

  // --- PORTABLE DOCUMENT EXPORT ENGINE ---
  const handleExportChat = () => {
    const transcript = messages
      .filter(msg => msg.sender !== 'system')
      .map(msg => `[${msg.sender.toUpperCase()}] (${new Date().toLocaleTimeString()})\n${msg.text}`)
      .join("\n\n----------------------------------------\n\n");
    if (!transcript.trim()) return;
    // Build downloadable Markdown asset dynamically
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

  // 3. Sync themes to localStorage
  // useEffect(() => {
  //   localStorage.setItem('saapp-theme', theme);
  // }, [theme]);

  // 4. Clean side-effect trigger: Auto-sync dialogue history to localStorage on any message mutations
  useEffect(() => {
    localStorage.setItem(`chat-messages-${principal}`, JSON.stringify(messages));
  }, [messages, principal]);

  // Fetch dynamic cards based on security clearance and active affiliate
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

  // Latency-aware Scroll Anchor Hook
  useEffect(() => {
    const scrollToBottom = () => {
      if (messagesEndRef.current) {
        messagesEndRef.current.scrollIntoView({
          // Use instant snap ('auto') during loading/streaming, smooth during idle clicks
          behavior: loading ? 'auto' : 'smooth',
          block: 'nearest',
        });
      }
    };
    // 1. Fire immediately for rapid reactive updates
    scrollToBottom();
    // 2. Fire with a tiny delay to allow the browser to paint newly loaded images/gifs (fixes refresh issue)
    const timer = setTimeout(scrollToBottom, 50);
    return () => clearTimeout(timer);
  }, [messages, loading]);

  const getNodeLabel = (nodeName: string): string => {
    switch (nodeName) {
      case 'retrieve_node':
        return 'GraphRAG Retrieval in Progress'
      case 'grade_documents_node':
        return 'Evaluating Document Relevance'
      case 'rewrite_query_node':
        return 'Refining Query Parameters'
      case 'generate_node':
        return 'Collecting Rings and Generating Tokens'
      case 'conversational_node':
        return 'generating a friendly hedgehog response'
      default:
        return `${nodeName}`;
    }
  }
  
const handleSendMessage = async (
  textToSend: string,
  currentAttachments: { filename: string; content: string }[]
) => {

  if (!textToSend.trim() || loading) return;

  // Use stable sessionId from state
  console.log("Using sessionId:", sessionId);


    // Add user message + placeholder AI message
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
    console.log("ATTACHMENTS AT SEND TIME:", attachments);


    try {
      // 2. THEN send chat message WITHOUT attachments
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

              if (payload.event === 'node_progress') {
                const nodeLabel = getNodeLabel(payload.node);
                setAgentStatus(nodeLabel);
                setAgentPath(prev =>
                  prev.includes(payload.node) ? prev : [...prev, payload.node]
                );
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
                    updated[lastIndex] = {
                      ...updated[lastIndex],
                      text: payload.text
                    };
                  }
                  return updated;
                });

                setAgentStatus('');
              }

              if (payload.event === 'error') {
                setMessages(prev => {
                  const updated = [...prev];
                  const lastIndex = updated.length - 1;

                  if (updated[lastIndex] && updated[lastIndex].sender === 'ai') {
                    updated[lastIndex] = {
                      ...updated[lastIndex],
                      text: `Execution Fault: ${payload.message}`
                    };
                  }
                  return updated;
                });

                setAgentStatus('');
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
    }
  };
  const onSubmitForm = (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim()) return;
    handleSendMessage(input, attachmentsRef.current);
  };
  
  return (
    <div>
      {/* CHAT UI */}
      <div className="hero-banner" style={{ backgroundImage: `linear-gradient(rgba(18, 24, 36, 0.7), rgba(18, 24, 36, 0.95)), url(${sonicImg})` }}>
        <div className="banner-context">
          <h3>{theme === 'sonic' ? 'Sonic Assistant' : 'Shadow Engine'}</h3>
          <h4>{theme === 'sonic' ? 'Rolling around at the speed of sound.' : 'Behold the Ultimate Power.'}</h4>
          {userEmail && <p className="badge">Principal Account Identity: {userEmail}</p>}
        </div>
      </div>
      <main className={`portal-body ${!hasChatted ? 'initial-state-view' : ''}`}>
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
        {hasChatted && (
          <div className="chat-window" ref={chatWindowRef}>
            {messages
              .filter(msg => !(hasChatted && msg.sender === 'system'))
              .map(msg => (
                <div key={msg.id} className={`message-bubble ${msg.sender}`}>
                  <div className="message-sender">{msg.sender.toUpperCase()}</div>
                  <div className="message-text">{msg.text}</div>
                </div>
              ))}
            {loading && (
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
            )}
            <div ref={messagesEndRef} />
          </div>
        )}
        <footer className="controls-footer" ref={messagesEndRef}>
          {uploadedFiles.length > 0 && (
            <div className="attached-files-banner">
              {uploadedFiles.map((file, idx) => (
                <div key={idx} className="attached-file-pill">
                  📎 {file.name}
                   <button
                    className="remove-file-btn"
                    onClick={() => handleRemoveAttachment(idx)}
                  >
                    ✕
                  </button>
                </div>
              ))}
            </div>
          )}
          <form onSubmit={onSubmitForm} className="chat-input-area">
            {/* Hidden file input */}
            <input
              id="file-upload"
              type="file"
              multiple
              style={{ display: "none" }}
              onChange={(e) => {
              console.log("FILE INPUT ONCHANGE FIRED");
              handleFileUpload(e);
            }}
            />

            <div className="chat-input-wrapper">
              <textarea
                className="chat-textarea"
                placeholder="Ask a question against your isolated data index..."
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
              {/* Tooltip / Info Button Wrapper */}
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

                {/* Tooltip Popup Message Box */}
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
                        <li> Please only begin your queries once you see the example questions and the green (or yellow in dark mode) username in the top left indicating your session permissions are set. </li>
                        <li> If the example questions have not loaded yet, it means the backend is still spinning up due to inactivity. please wait for the app to be fully loaded before using.</li>
                        <li> Due to operating on cost-sensitive infrastructure, response times may vary or be unavailable due to model demand.</li>
                        <li> If you encounter any bugs, issues, or would like your permissions changed, please reach out to jackharper0517@outlook.com.</li>
                      </ul>
                    </p>
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




          <Filters 
            selectedAffiliate={selectedAffiliate}
            setSelectedAffiliate={setSelectedAffiliate}
            loadingChat={loading}
            allowedAffiliates={allowedAffiliates}
            setAllowedAffiliates={setAllowedAffiliates}
          />
        </footer>
      </main>
    </div>  
  );
}

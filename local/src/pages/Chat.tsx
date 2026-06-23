import React, { useEffect, useState, useRef } from 'react';
import sonicImg from '../assets/sonicandshadow.jpg';
import { Filters } from '../components/Filters';
import { getDynamicExampleQuestions } from '../utils/Example_List';
import { api } from '../api'; 
import sonicSpinImg from '../assets/sonic-rolling.gif';
import shadowSpinImg from '../assets/shadow.gif';
import { SelfServicePage } from './SelfService';

interface Message {
  sender: 'user' | 'ai' | 'system';
  text: string;
}

interface ChatPageProps {
  onExit: () => void;
}

export const ChatPage: React.FC<ChatPageProps> = ({ onExit }) => {
  const [username] = useState(() => localStorage.getItem('x-user-id') || 'Unknown Principal');
  const [selectedAffiliate, setSelectedAffiliate] = useState<string>('All');
  const [allowedAffiliates, setAllowedAffiliates] = useState<string[]>([]);
  const [userEmail, setUserEmail] = useState<string>('');
  const [activeTab, setActiveTab] = useState<'chat' | 'self-service'>('chat');
  const [menuOpen, setMenuOpen] = useState<boolean>(false); // Mobile hamburger control
  const [isMobile, setIsMobile] = useState<boolean>(window.innerWidth < 768);

  useEffect(() => {
    const handleResize = () => {
      setIsMobile(window.innerWidth < 768);
    };
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, []);

  // 1. Warm-initialize the messages state from localStorage to prevent auto-clearing
  const [messages, setMessages] = useState<Message[]>(() => {
    const persistedHistory = localStorage.getItem(`chat-messages-${username}`);
    if (persistedHistory) {
      try {
        return JSON.parse(persistedHistory);
      } catch (e) {
        console.error("Failed to parse persisted conversation logs:", e);
      }
    }
    return [
      { sender: 'system', text: `What would you like to find out about, ${username}?` }
    ];
  });
  
  const [input, setInput] = useState<string>('');
  const [loading, setLoading] = useState<boolean>(false);
  const [currentExampleQuestions, setCurrentExampleQuestions] = useState<string[]>([]);
  const [loadingCards, setLoadingCards] = useState<boolean>(false);
  
  const [theme, setTheme] = useState<'sonic' | 'shadow'>(() => {
    const savedTheme = localStorage.getItem('saapp-theme');
    return (savedTheme === 'shadow' || savedTheme === 'sonic') ? savedTheme : 'sonic';
  });
  
  const chatWindowRef = useRef<HTMLDivElement>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const toggleTheme = () => {
    setTheme(prev => (prev === 'sonic' ? 'shadow' : 'sonic'));
  };

  // 2. Clear history updates both reactive states, client localStorage, and backend database
  const handleClearChat = async () => {
    setMessages([
      { sender: 'system', text: `What would you like to find out about, ${username}?` }
    ]);
    setSelectedAffiliate('All');
    localStorage.removeItem(`chat-messages-${username}`);

    try {
      // Direct call to purge the persisted memory on your local-RAG backend API
      await fetch('http://192.168.1.10:8000/api/chat/clear', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username })
      });
    } catch (e) {
      console.warn("Backend persistent clearance was skipped (server offline).");
    }
  };

  // --- PORTABLE DOCUMENT EXPORT ENGINE ---
  const handleExportChat = () => {
    const transcript = messages
      .filter(msg => msg.sender !== 'system')
      .map(msg => `[${msg.sender.toUpperCase()}] (${new Date().toLocaleTimeString()})\n${msg.text}`)
      .join("\n\n----------------------------------------\n\n");

    if (!transcript.trim()) return;

    // Build downloadable Markdown asset dynamically
    const blob = new Blob([`# Secure RAG Chat Session: ${username}\n\n${transcript}`], { type: 'text/markdown;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `rag_chat_session_${username}_${new Date().toISOString().slice(0, 10)}.md`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  };

  // 3. Sync themes to localStorage
  useEffect(() => {
    localStorage.setItem('saapp-theme', theme);
  }, [theme]);

  // 4. Clean side-effect trigger: Auto-sync dialogue history to localStorage on any message mutations
  useEffect(() => {
    localStorage.setItem(`chat-messages-${username}`, JSON.stringify(messages));
  }, [messages, username]);

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

  
  const handleSendMessage = async (textToSend: string) => {
    if (!textToSend.trim() || loading) return;

    const userMsg = { sender: 'user' as const, text: textToSend };
    setMessages(prev => [...prev, userMsg, { sender: 'ai' as const, text: '' }]);
    setInput('');
    setLoading(true);

    try {
      await api.sendChatMessage(username, textToSend, selectedAffiliate, (newToken) => {
        setMessages(prev => {
          const updated = [...prev];
          const lastIndex = updated.length - 1;
          if (updated[lastIndex] && updated[lastIndex].sender === 'ai') {
            updated[lastIndex] = {
              ...updated[lastIndex],
              text: updated[lastIndex].text + newToken
            };
          }
          return updated;
        });
      });
    } catch (error) {
      console.error(error);
      setMessages(prev => [...prev, { sender: 'ai', text: "Vector assertion timed out. Check local engine allocations." }]);
    } finally {
      setLoading(false);
    }
  };

  const onSubmitForm = (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim()) return;
    handleSendMessage(input);
  };

  const hasChatted = messages.some(msg => msg.sender === 'user');

  // =========================================================================
  //                          VIEWPORT VIEW RENDERS
  // =========================================================================

  const renderMobileView = () => (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' }}>
      {/* Shrunk Hero Banner for Mobile Cleanliness */}
      <div className="hero-banner" style={{ backgroundImage: `linear-gradient(rgba(18, 24, 36, 0.7), rgba(18, 24, 36, 0.95)), url(${sonicImg})`, padding: '16px 12px', minHeight: 'auto', textAlign: 'center' }}>
        <div className="banner-context">
          <h3 style={{ fontSize: '1.3rem', margin: '0 0 4px 0', color: 'var(--text-main)' }}>{theme === 'sonic' ? 'Sonic Assistant' : 'Shadow Engine'}</h3>
          <h4 style={{ fontSize: '0.85rem', color: 'var(--text-muted)', margin: 0 }}>{theme === 'sonic' ? 'Rolling at the speed of thought.' : 'Behold the Ultimate Power.'}</h4>
        </div>
      </div>

      {/* Chat Bubble Sandbox Window */}
      <main className={`portal-body ${!hasChatted ? 'initial-state-view' : ''}`} style={{ flex: 1, display: 'flex', flexDirection: 'column', overflowY: 'auto', padding: '12px', backgroundColor: 'var(--bg-main)' }}>
        {!hasChatted && (
          <div className="example-cards-container" style={{ display: 'grid', gridTemplateColumns: '1fr', gap: '8px', maxWidth: '100%', margin: 'auto 0' }}>
            {loadingCards ? (
              <div style={{ color: 'var(--text-muted)', fontSize: '0.85rem', textAlign: 'center' }}>
                Querying directory indices...
              </div>
            ) : (
              currentExampleQuestions.map((q, idx) => (
                <div key={idx} className="query-card" onClick={() => !loading && handleSendMessage(q)} style={{ padding: '12px', borderRadius: '8px', fontSize: '0.9rem', cursor: 'pointer' }}>
                  <p style={{ margin: 0, color: 'var(--text)' }}>{q}</p>
                </div>
              ))
            )}
          </div>
        )}

        {hasChatted && (
          <div className="chat-window" ref={chatWindowRef} style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: '12px', paddingBottom: '20px' }}>
            {messages
              .filter(msg => !(hasChatted && msg.sender === 'system'))
              .map((msg, index) => (
                <div key={index} className={`message-bubble ${msg.sender}`} style={{ maxWidth: '85%', padding: '10px 14px', borderRadius: '12px', alignSelf: msg.sender === 'user' ? 'flex-end' : 'flex-start', fontSize: '0.95rem', lineHeight: '1.4' }}>
                  <div className="message-sender" style={{ fontSize: '0.7rem', opacity: 0.6, marginBottom: '2px' }}>{msg.sender.toUpperCase()}</div>
                  <div className="message-text" style={{ wordBreak: 'break-word' }}>{msg.text}</div>
                </div>
              ))}
              
            {loading && (
              <div className="message-bubble ai thinking sonic-loader-container" style={{ alignSelf: 'flex-start', display: 'flex', alignItems: 'center', gap: '8px' }}>
                {theme === 'sonic' ? (
                  <img src={sonicSpinImg} alt="Spinning..." style={{ width: '24px', height: '24px' }} />
                ) : (
                  <img src={shadowSpinImg} alt="Spinning..." style={{ width: '24px', height: '24px' }} />
                )}
                <div className="loading-text" style={{ fontSize: '0.85rem', color: 'var(--text-muted)' }}>Collecting rings & tokens...</div>
              </div>
            )}
          </div>
        )}

        {/* Mobile Footer Area sticky bottom setup */}
        <footer className="controls-footer" ref={messagesEndRef} style={{ marginTop: 'auto', backgroundColor: 'var(--bg-main)', borderTop: '1px solid var(--border-color)', padding: '8px', borderRadius: '12px', display: 'flex', flexDirection: 'column', gap: '8px' }}>
          <form onSubmit={onSubmitForm} style={{ display: 'flex', width: '100%', gap: '6px' }}>
            <input
              type="text"
              placeholder="Ask a question..."
              value={input}
              onChange={(e) => setInput(e.target.value)}
              disabled={loading}
              style={{ flex: 1, height: '44px', padding: '0 12px', borderRadius: '8px', border: '1px solid var(--border-color)', backgroundColor: 'var(--bg-main)', color: 'var(--text)', fontSize: '16px' }} // Blocks zoom on iOS
            />
            <button className="submit-button" type="submit" disabled={loading || !input.trim()} style={{ height: '44px', padding: '0 16px', borderRadius: '8px', fontWeight: 'bold' }}>Send</button>
          </form>

          {/* Action Row */}
          <div style={{ display: 'flex', gap: '6px', width: '100%' }}>
            <button className="export-button" type="button" onClick={handleExportChat} disabled={loading || !hasChatted} style={{ flex: 1, height: '36px', fontSize: '0.85rem', borderRadius: '6px', border: '1px solid var(--border-color)' }}>
              Export (.md)
            </button>
            <button className="clear-button" type="button" onClick={handleClearChat} disabled={loading} style={{ flex: 1, height: '36px', fontSize: '0.85rem', borderRadius: '6px', border: '1px solid var(--border-color)', backgroundColor: '#334155', color: '#fff' }}>
              Clear
            </button>
          </div>

          <div style={{ padding: '4px 0' }}>
            <Filters 
              selectedAffiliate={selectedAffiliate}
              setSelectedAffiliate={setSelectedAffiliate}
              loadingChat={loading}
              allowedAffiliates={allowedAffiliates}
              setAllowedAffiliates={setAllowedAffiliates}
            />
          </div>
        </footer>
      </main>
    </div>
  );

  const renderDesktopView = () => (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {/* Spacious Full-Size Desktop Banner */}
      <div className="hero-banner" style={{ backgroundImage: `linear-gradient(rgba(18, 24, 36, 0.7), rgba(18, 24, 36, 0.95)), url(${sonicImg})` }}>
        <div className="banner-context">
          <h3>{theme === 'sonic' ? 'Sonic Assistant' : 'Shadow Engine'}</h3>
          <h4>{theme === 'sonic' ? 'rolling around at the speed of thought.' : 'Behold the Ultimate Power.'}</h4>
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
                <div key={idx} className="query-card" onClick={() => !loading && handleSendMessage(q)}>
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
              .map((msg, index) => (
                <div key={index} className={`message-bubble ${msg.sender}`}>
                  <div className="message-sender">{msg.sender.toUpperCase()}</div>
                  <div className="message-text">{msg.text}</div>
                </div>
              ))}
              
            {loading && (
              <div className="message-bubble ai thinking sonic-loader-container">
                {theme === 'sonic' ? (
                  <img src={sonicSpinImg} alt="Spinning..." className="sonic-spin-gif" />
                ) : (
                  <img src={shadowSpinImg} alt="Spinning..." className="shadow-spin-gif" />
                )}
                <div className="loading-text">
                  Collecting rings & tokens...
                </div>
              </div>
            )}
          </div>
        )}

        <footer className="controls-footer" ref={messagesEndRef}>
          <form onSubmit={onSubmitForm} className="chat-input-area">
            <input
              type="text"
              placeholder="Ask a question against your isolated data index..."
              value={input}
              onChange={(e) => setInput(e.target.value)}
              disabled={loading}
            />
            <button className="submit-button" type="submit" disabled={loading || !input.trim()}>Send</button>
            
            <button
              className="export-button"
              type="button"
              onClick={handleExportChat}
              disabled={loading || !hasChatted}
              // style={{ backgroundColor: '#4f46e5', color: '#ffffff', marginLeft: '0.5rem' }}
            >
              Export
            </button>

            <button
              className="clear-button" 
              type="button" 
              onClick={handleClearChat} 
              disabled={loading}
              style={{ backgroundColor: '#334155', marginLeft: '0.5rem' }}
            >
              Clear
            </button>
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

  return (
    <div className={`portal-container ${theme === 'shadow' ? 'theme-shadow' : ''}`} style={{ display: 'flex', flexDirection: 'column', height: '100vh', width: '100vw', overflow: 'hidden' }}>
      
      {/* Persistent Global Navigation Core */}
      <nav className="menu-navigator" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '10px 16px', zIndex: 1000, position: 'relative' }}>
        <div className="nav-logo" style={{ fontSize: '1.1rem', fontWeight: 'bold' }}>
          <a href="/" style={{ textDecoration: 'none' }}>{theme === 'sonic' ? '⚡Sonic Assistant' : '⚡Shadow Engine'}</a>
        </div>
        
        {/* Toggle Hamburguer - renders strictly on mobile devices */}
        <div className="mobile-menu-toggle" onClick={() => setMenuOpen(!menuOpen)} style={{ display: isMobile ? 'block' : 'none', cursor: 'pointer', fontSize: '1.5rem', color: '#fff' }}>
          ☰
        </div>

        <div className={`nav-links ${isMobile && menuOpen ? 'mobile-open' : ''}`} style={{ display: !isMobile || menuOpen ? 'flex' : 'none' }}>
          <span onClick={() => { toggleTheme(); setMenuOpen(false); }} className="theme-toggle-btn">
            {theme === 'sonic' ? 'Hero' : 'Dark'}
          </span>
          <span className={activeTab === 'chat' ? 'active' : ''} onClick={() => { setActiveTab('chat'); setMenuOpen(false); }}>
            Dashboard
          </span>
          <span className={activeTab === 'self-service' ? 'active' : ''} onClick={() => { setActiveTab('self-service'); setMenuOpen(false); }}>
            Self Service
          </span>
          <span onClick={onExit} className="nav-exit">Disconnect Session</span>
        </div>
      </nav>

      {/* Main Tab Controller Routing */}
      {activeTab === 'self-service' ? (
        <div style={{ flex: 1, overflowY: 'auto', padding: isMobile ? '12px' : '24px' }}>
          <SelfServicePage />
        </div>
      ) : (
        <div style={{ flex: 1, overflow: 'hidden' }}>
          {isMobile ? renderMobileView() : renderDesktopView()}
        </div>
      )}
    </div>
  );
};
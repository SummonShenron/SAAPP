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
  const [messages, setMessages] = useState<Message[]>([
    { sender: 'system', text: `What would you like to find out about, ${username}?` }
  ]);
  const [input, setInput] = useState<string>('');
  const [loading, setLoading] = useState<boolean>(false);
  const [currentExampleQuestions, setCurrentExampleQuestions] = useState<string[]>([]);
  const [loadingCards, setLoadingCards] = useState<boolean>(false);
  const [theme, setTheme] = useState<'sonic' | 'shadow'>(() => {
    const savedTheme = localStorage.getItem('saapp-theme');
    return (savedTheme === 'shadow' || savedTheme === 'sonic') ? savedTheme : 'sonic';
  });
  // Ref anchor to target the scrollable chat container viewport
  const chatWindowRef = useRef<HTMLDivElement>(null);
  const toggleTheme = () => {
    setTheme(prev => (prev === 'sonic' ? 'shadow' : 'sonic'));
  };
  const handleClearChat = () => {
    setMessages([
      { sender: 'system', text: `What would you like to find out about, ${username}?` }
    ]);
    setSelectedAffiliate('All');
  };

  useEffect(() => {
    localStorage.setItem('saapp-theme', theme);
  }, [theme]);

  useEffect(() => {
    // Defensive check: Don't fetch cards until permissions have actually loaded
    if (allowedAffiliates.length === 0) return;
    const syncQuestionPool = async () => {
      setLoadingCards(true);
      const questions = await getDynamicExampleQuestions(allowedAffiliates, selectedAffiliate);
      setCurrentExampleQuestions(questions);
      setLoadingCards(false);
    };

    syncQuestionPool();
  }, [allowedAffiliates, selectedAffiliate]);

  // Auto-Scroll Hook: Fires instantly when messages update or streaming starts
  useEffect(() => {
    if (chatWindowRef.current) {
      chatWindowRef.current.scrollTo({
        top: chatWindowRef.current.scrollHeight,
        behavior: 'smooth'
      });
    }
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
    setInput('');
  };

  const hasChatted = messages.some(msg => msg.sender === 'user');

  return (
    <div className={`portal-container ${theme === 'shadow' ? 'theme-shadow' : ''}`}>
      <nav className="menu-navigator">
        <div className="nav-logo"><a href="/" style={{ textDecoration: 'none' }}> {theme === 'sonic' ? '⚡Sonic Assistant' : '⚡Shadow Engine'}</a></div>
        {/* 3. UPDATE THE NAV-LINKS SECTION TO HANDLE TAB NAVIGATION TOGGLES */}
        <div className="nav-links">
          <span onClick={toggleTheme} className="theme-toggle-btn">
            {theme === 'sonic' ? 'Hero' : 'Dark'}
          </span>
          
          {/* Dashboard Tab Link */}
          <span 
            className={activeTab === 'chat' ? 'active' : ''} 
            onClick={() => setActiveTab('chat')}
          >
            Dashboard
          </span>
          
          {/* New Self Service Tab Link */}
          <span 
            className={activeTab === 'self-service' ? 'active' : ''} 
            onClick={() => setActiveTab('self-service')}
          >
            Self Service
          </span>
          
          <span onClick={onExit} className="nav-exit">Disconnect Session</span>
        </div>
      </nav>

      {activeTab === 'self-service' ? (
        // Render Self Service Page layout seamlessly underneath the persistent global navigation bar
        <SelfServicePage />
      ) : (
        // Standard Chat Assistant Core Interface Layout View
        <>
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

            <div className="controls-footer">
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
            </div>
          </main>
        </>
      )}
    </div>
  );
};
import React from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import type { ConversationSummary } from '../api';
import { BRAND_NAME } from './brand';
import Icon, { type IconName } from './icons';
import LogoMark from './LogoMark';
import UserMenu from './UserMenu';

export interface RailConversations {
  items: ConversationSummary[];
  loading: boolean;
  activeId: string | null;
  onSelect: (sessionId: string) => void;
  onNew: () => void;
  onDelete: (sessionId: string, title: string) => void;
}

interface RailProps {
  theme: string;
  onToggleTheme: () => void;
  /** Icons only (the desktop rail, collapsed). Never true on a phone, where the rail is a drawer. */
  compact: boolean;
  onToggleCompact: () => void;
  isMobile: boolean;
  drawerOpen: boolean;
  onCloseDrawer: () => void;
  isAdmin: boolean;
  isGuest: boolean;
  userName: string;
  userEmail: string | null;
  onHelp: () => void;
  onLogout: () => void;
  conversations: RailConversations;
}

function relativeTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '';
  const minutes = Math.round((Date.now() - date.getTime()) / 60000);
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

/**
 * The app's left rail: the logo and name, the pages, the recent conversations, and the person's own chip with its menu. On a wide screen it can
 * collapse to icons; on a phone it is a drawer that slides in over the page. It replaces the top navigation bar and the hero banner.
 */
const Rail: React.FC<RailProps> = (props) => {
  const { theme, onToggleTheme, compact, onToggleCompact, isMobile, drawerOpen, onCloseDrawer, isAdmin, isGuest, userName, userEmail, onHelp, onLogout, conversations } = props;
  const navigate = useNavigate();
  const { pathname } = useLocation();

  const go = (path: string) => {
    navigate(path);
    if (isMobile) onCloseDrawer();
  };

  const pages: { path: string; label: string; icon: IconName; show: boolean }[] = [
    { path: '/chat', label: 'Chat', icon: 'chat', show: true },
    { path: '/self-service', label: 'Self Service', icon: 'folder', show: true },
    { path: '/tidy', label: 'Tidy up', icon: 'tidy', show: !isGuest },
    { path: '/memory', label: 'Memory', icon: 'memory', show: !isGuest },
    { path: '/tasks', label: 'Activities', icon: 'tasks', show: !isGuest },
    { path: '/meetings', label: 'Office Hours', icon: 'meeting', show: !isGuest },
    { path: '/integrations', label: 'Integrations', icon: 'link', show: !isGuest },
    { path: '/sonic-health', label: 'Sonic Health', icon: 'activity', show: isAdmin },
  ];

  return (
    <>
      {isMobile && drawerOpen && <div className="rail-backdrop" onClick={onCloseDrawer} aria-hidden="true" />}
      <aside
        className={`app-rail${compact ? ' app-rail-compact' : ''}${isMobile ? ' app-rail-drawer' : ''}${drawerOpen ? ' app-rail-open' : ''}`}
        aria-label="Navigation"
      >
        <div className="rail-head">
          <button type="button" className="rail-brand" onClick={() => go('/chat')} title={BRAND_NAME} aria-label={BRAND_NAME}>
            <LogoMark size={28} />
            {!compact && <span className="rail-wordmark">{BRAND_NAME}</span>}
          </button>
          {!isMobile && !compact && (
            <button type="button" className="rail-icon-btn" onClick={onToggleCompact} aria-label="Collapse sidebar" title="Collapse sidebar"><Icon name="panel" size={16} /></button>
          )}
          {isMobile && (
            <button type="button" className="rail-icon-btn" onClick={onCloseDrawer} aria-label="Close menu"><Icon name="close" size={18} /></button>
          )}
        </div>

        {!isMobile && compact && (
          <button type="button" className="rail-icon-btn rail-expand" onClick={onToggleCompact} aria-label="Expand sidebar" title="Expand sidebar"><Icon name="panel" size={16} /></button>
        )}

        <button type="button" className="rail-new" onClick={conversations.onNew} title="Start a new conversation">
          <Icon name="plus" size={16} />
          {!compact && <span className="rail-label">New chat</span>}
        </button>

        <nav className="rail-nav">
          {pages.filter(p => p.show).map(p => (
            <button
              key={p.path}
              type="button"
              className={`rail-item${pathname.startsWith(p.path) ? ' active' : ''}`}
              onClick={() => go(p.path)}
              aria-current={pathname.startsWith(p.path) ? 'page' : undefined}
              title={compact ? p.label : undefined}
            >
              <Icon name={p.icon} size={18} />
              {!compact && <span className="rail-label">{p.label}</span>}
            </button>
          ))}
        </nav>

        {!compact ? (
          <div className="rail-recent">
            <div className="rail-section">Recent</div>
            <div className="rail-recent-list">
              {conversations.loading && <div className="rail-empty">Loading…</div>}
              {!conversations.loading && conversations.items.length === 0 && <div className="rail-empty">No conversations yet.</div>}
              {conversations.items.map(c => (
                <div
                  key={c.session_id}
                  className={`rail-convo${c.session_id === conversations.activeId && pathname.startsWith('/chat') ? ' active' : ''}`}
                  title={`${c.title || 'Untitled'} · ${relativeTime(c.updated_at)}`}
                >
                  <button type="button" className="rail-convo-main" onClick={() => conversations.onSelect(c.session_id)}>
                    {c.title || 'New conversation'}
                  </button>
                  <button
                    type="button"
                    className="rail-convo-delete"
                    aria-label={`Delete ${c.title || 'conversation'}`}
                    title="Delete conversation"
                    onClick={() => conversations.onDelete(c.session_id, c.title)}
                  >
                    <Icon name="trash" size={14} />
                  </button>
                </div>
              ))}
            </div>
          </div>
        ) : (
          <div className="rail-spacer" />
        )}

        <UserMenu
          name={userName}
          email={userEmail}
          theme={theme}
          compact={compact}
          onToggleTheme={onToggleTheme}
          onAccount={() => go('/account')}
          onHelp={() => { onHelp(); if (isMobile) onCloseDrawer(); }}
          onLogout={onLogout}
        />
      </aside>
    </>
  );
};

export default Rail;

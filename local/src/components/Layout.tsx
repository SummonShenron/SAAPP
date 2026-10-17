// src/components/Layout.tsx
import { Outlet, useLocation, useNavigate } from "react-router-dom";
import { Suspense, useState, useEffect } from "react";
import { api, getEffectivePrincipal, isGuestPrincipal } from "../api";
import { useIsGlobalAdmin } from "../hooks/useIsGlobalAdmin";
import { useAuthReady } from "../hooks/useAuthReady";
import { useConversationList } from "../hooks/useConversationList";
import { useMediaQuery } from "../hooks/useMediaQuery";
import AuthLoadingScreen from "../components/AuthLoadingScreen";
import Spinner from "../components/Spinner";
import HelpPanel from "../components/HelpPanel";
import Rail from "./Rail";
import TopBar from "./TopBar";
import { useShell } from "./shellStore";
import { useAuth } from '@clerk/clerk-react';
import "./__styles__/AppShell.css";

interface LayoutProps {
  theme: string;
  toggleTheme: () => void;
  onExit: () => void;
}

const RAIL_PREF_KEY = "rail-collapsed";
const PAGE_TITLES: Record<string, string> = {
  "/self-service": "Self Service",
  "/tidy": "Tidy up",
  "/memory": "Memory",
  "/integrations": "Integrations",
  "/sonic-health": "Sonic Health",
  "/account": "Account settings",
};

function readRailPreference(): boolean | null {
  try {
    const stored = localStorage.getItem(RAIL_PREF_KEY);
    return stored === "true" ? true : stored === "false" ? false : null;
  } catch {
    return null;
  }
}

export function Layout({ theme, toggleTheme }: LayoutProps) {
  const { isLoaded, isSignedIn } = useAuth();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const [showHelp, setShowHelp] = useState(false);
  const [closing, setClosing] = useState(false);
  // The drawer remembers which page it was opened on, so it is open only there: going to another page (or the browser's back button) closes it.
  const [drawerOpenAt, setDrawerOpenAt] = useState<string | null>(null);
  const [railPreference, setRailPreference] = useState<boolean | null>(readRailPreference);

  // --- EMBED MODE DETECTION ---
  const hashSearch = window.location.hash.includes('?') ? window.location.hash.split('?')[1] : '';
  const searchParams = new URLSearchParams(window.location.search || hashSearch);
  const isEmbedded = searchParams.get('mode') === 'embed' || window.location.href.includes("mode=embed");

  // Nothing behind the nav renders, loads or can be sent until the person is fully signed in (hooks/useAuthReady.ts). The embedded widget has
  // its own guest identity and never waits.
  const auth = useAuthReady(isEmbedded);
  const authReady = auth.state === 'ready';
  const isAdmin = useIsGlobalAdmin(authReady);

  // The rail: a drawer on a phone; on a wide screen expanded unless the person collapsed it (and it stays as they left it), on a narrower
  // one icons only unless they expanded it.
  const isMobile = useMediaQuery("(max-width: 768px)");
  const isWide = useMediaQuery("(min-width: 1200px)");
  const compact = !isMobile && (railPreference ?? !isWide);
  const drawerOpen = isMobile && drawerOpenAt === pathname;
  const setDrawerOpen = (open: boolean) => setDrawerOpenAt(open ? pathname : null);
  const toggleCompact = () => {
    const next = !compact;
    setRailPreference(next);
    try { localStorage.setItem(RAIL_PREF_KEY, String(next)); } catch { /* the choice just lasts until the page is reloaded */ }
  };

  // What the chat page tells the shell: which conversation is open and how to switch or start one, and its top bar buttons.
  const { conversations: bridge, topBarActions } = useShell();
  const conversationList = useConversationList(authReady && !isEmbedded, bridge?.refreshKey ?? 0);

  const HELP_CLOSE_ANIMATION_MS = 200;

  const closeHelp = () => {
    setClosing(true);
    setTimeout(() => {
      setShowHelp(false);
      setClosing(false);
    }, HELP_CLOSE_ANIMATION_MS);
    // Fire-and-forget: dismissing it (by any means) means it should never auto-show again,
    // regardless of whether this was the automatic first-time popup or a manual reopen.
    api.updateHasSeenHelp(true).catch((err) => console.error("Failed to record help panel as seen:", err));
  };

  useEffect(() => {
    if (!showHelp) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") closeHelp();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showHelp]);

  useEffect(() => {
    if (!drawerOpen) return;
    const onKeyDown = (e: KeyboardEvent) => { if (e.key === "Escape") setDrawerOpenAt(null); };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [drawerOpen]);

  // Bootstrap guest identity synchronously so children see it on first render
  const principal = getEffectivePrincipal();
  const guest = isGuestPrincipal(principal);
  const userName = guest ? "Guest" : (principal.includes("@") ? principal.split("@")[0] : principal);
  const userEmail = guest ? null : principal;
  const activeKey = `conversation-id-${principal}`;

  const toggleHelp = () => {
    if (showHelp) {
      closeHelp();
    } else {
      setShowHelp(true);
    }
  };

  const handleLogout = async () => {
    try {
      await api.logout();
    } catch (error) {
      console.error("Logout failed:", error);
    }
  };

  // Auto-show the help/onboarding overlay once, the first time a user ever signs in — the
  // embed iframe never renders it at all (see the isEmbedded early return below), so skip the
  // check entirely there rather than fetching a setting nothing will use.
  useEffect(() => {
    if (!isLoaded || isEmbedded || !authReady) return;
    const hasAuth = isSignedIn || !!localStorage.getItem('guest_token');
    if (!hasAuth) return;

    api.getHasSeenHelp()
      .then((data) => {
        if (!data.has_seen_help) setShowHelp(true);
      })
      .catch((err) => console.warn("Could not fetch help-seen setting:", err));
  }, [isLoaded, isSignedIn, isEmbedded, authReady]);

  // If in Embed Mode, return ONLY the page content without top menus/sidebars
  if (isEmbedded) {
    return (
      <div className="portal-container bty-embed-container" style={{ minHeight: '100vh', background: '#121316', padding: 0 }}>
        <Suspense fallback={null}><Outlet /></Suspense>
      </div>
    );
  }

  // --- the rail's conversation list: on the chat page it drives the open chat; anywhere else it opens the chat on that conversation ---
  const selectConversation = (sessionId: string) => {
    if (bridge) {
      bridge.select(sessionId);
    } else {
      localStorage.setItem(activeKey, sessionId);
      navigate("/chat");
    }
    setDrawerOpen(false);
  };
  const newConversation = () => {
    if (bridge) {
      bridge.newChat();
    } else {
      localStorage.setItem(activeKey, crypto.randomUUID());
      navigate("/chat");
    }
    setDrawerOpen(false);
  };
  const deleteConversation = async (sessionId: string, title: string) => {
    if (!window.confirm(`Delete "${title || "this conversation"}"? This can't be undone.`)) return;
    const removed = await conversationList.remove(sessionId);
    if (!removed) return;
    if (bridge && bridge.activeSessionId === sessionId) bridge.newChat(); // the one on screen was deleted: start a fresh one
    else if (!bridge && localStorage.getItem(activeKey) === sessionId) localStorage.removeItem(activeKey);
  };

  const activeId = bridge?.activeSessionId ?? localStorage.getItem(activeKey);
  const onChat = pathname.startsWith("/chat");
  const openTitle = conversationList.items.find(c => c.session_id === activeId)?.title;
  const titledPath = Object.keys(PAGE_TITLES).find(p => pathname.startsWith(p));
  const title = onChat ? (openTitle || "New chat") : (titledPath ? PAGE_TITLES[titledPath] : "");

  // Regular Standalone Mode Layout
  return (
    <div className={`portal-container app-shell ${theme === "shadow" ? "theme-shadow" : ""}`}>
      <Rail
        theme={theme}
        onToggleTheme={toggleTheme}
        compact={compact}
        onToggleCompact={toggleCompact}
        isMobile={isMobile}
        drawerOpen={drawerOpen}
        onCloseDrawer={() => setDrawerOpen(false)}
        isAdmin={isAdmin}
        isGuest={guest}
        userName={userName}
        userEmail={userEmail}
        onHelp={toggleHelp}
        onLogout={handleLogout}
        conversations={{
          items: conversationList.items,
          loading: conversationList.loading,
          activeId,
          onSelect: selectConversation,
          onNew: newConversation,
          onDelete: deleteConversation,
        }}
      />

      <div className="app-main">
        <TopBar title={title} isMobile={isMobile} onOpenMenu={() => setDrawerOpen(true)} actions={onChat ? topBarActions : null} onHelp={toggleHelp} />
        <div className="app-content">
          {/* Main page content: only once the person is fully signed in */}
          {authReady ? (
            // A page that is fetched on first use (see App.tsx) shows a quiet spinner for the moment it takes, inside the same shell.
            <Suspense fallback={<div style={{ display: 'flex', justifyContent: 'center', padding: '4rem 0' }}><Spinner size={32} /></div>}>
              <Outlet />
            </Suspense>
          ) : (
            <AuthLoadingScreen state={auth.state as 'checking' | 'denied' | 'timeout'} slow={auth.slow} onRetry={auth.retry} />
          )}
        </div>
      </div>

      {/* Help / onboarding overlay */}
      {showHelp && (
        <div
          className={`help-panel-backdrop ${closing ? "closing" : ""}`}
          onClick={closeHelp}
        >
          <div
            className={`help-panel-container ${closing ? "closing" : ""}`}
            onClick={(e) => e.stopPropagation()}
          >
            <HelpPanel onClose={closeHelp} />
          </div>
        </div>
      )}
    </div>
  );
}

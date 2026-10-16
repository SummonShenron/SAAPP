import { useSyncExternalStore, type ReactNode } from 'react';

/**
 * What the page inside the app shell (components/Layout.tsx) tells the shell, and the shell shows. The rail and the top bar live outside the page, so
 * the chat page registers two things here instead of the shell reaching into it:
 *  - `conversations`: which conversation is open and how to switch to another or start a new one, so the rail's "Recent" list works on the chat page;
 *  - `topBarActions`: buttons the page wants in the top bar (the chat's trace button).
 * A small external store rather than React context: the page updates it from effects, and a context value changing there would re-render the whole page.
 */
export interface ConversationsBridge {
  activeSessionId: string;
  /** Bumped by the chat when the list may have changed (a reply finished, a conversation was cleared), so the rail reads it again. */
  refreshKey: number;
  select: (sessionId: string) => void;
  newChat: () => void;
}

export interface ShellState {
  conversations: ConversationsBridge | null;
  topBarActions: ReactNode | null;
}

let state: ShellState = { conversations: null, topBarActions: null };
const listeners = new Set<() => void>();

function update(partial: Partial<ShellState>): void {
  state = { ...state, ...partial };
  listeners.forEach(listener => listener());
}

export const shell = {
  setConversations: (bridge: ConversationsBridge | null) => update({ conversations: bridge }),
  setTopBarActions: (actions: ReactNode | null) => update({ topBarActions: actions }),
};

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

export function useShell(): ShellState {
  return useSyncExternalStore(subscribe, () => state, () => state);
}

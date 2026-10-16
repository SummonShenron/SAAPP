import { useCallback, useEffect, useState } from 'react';
import { api } from '../api';
import type { ConversationSummary } from '../api';

/**
 * The person's saved conversations, newest first, for the rail's "Recent" list and the top bar's title. It reads them again whenever `refreshKey`
 * changes (the chat bumps it when a reply finishes) and waits until `enabled` (the person is fully signed in) before asking at all.
 */
export function useConversationList(enabled: boolean, refreshKey: number) {
  const [items, setItems] = useState<ConversationSummary[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    api.listConversations()
      .then((data: ConversationSummary[]) => { if (!cancelled) setItems(Array.isArray(data) ? data : []); })
      .catch(() => { if (!cancelled) setItems([]); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [enabled, refreshKey]);

  /** Deletes one on the server and drops it from the list. Resolves true when it worked. */
  const remove = useCallback(async (sessionId: string): Promise<boolean> => {
    try {
      await api.deleteConversation(sessionId);
      setItems(prev => prev.filter(c => c.session_id !== sessionId));
      return true;
    } catch (err) {
      console.error('Failed to delete conversation:', err);
      return false;
    }
  }, []);

  return { items, loading, remove };
}

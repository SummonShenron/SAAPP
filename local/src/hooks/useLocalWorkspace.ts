import { useCallback, useEffect, useRef, useState } from 'react';
import { disconnectLocalWorkspace } from '../api';
import { applyProposal, latestUndoBatch, undoBatch, type ApplyResult, type UndoResult } from '../localEdits';
import type { EditProposal } from '../localEditsCore';
import {
  clearFolderHandle,
  hasReadPermission,
  isLocalWorkspaceSupported,
  loadFolderHandle,
  pickFolder,
  saveFolderHandle,
  serverSnapshotMatches,
  syncFolder,
  type FsDirectoryHandle,
} from '../localWorkspace';

export type LocalWorkspaceStatus = 'disconnected' | 'needs-permission' | 'connected' | 'syncing' | 'error';

// A message sent within this window of the last sync reuses it instead of re-scanning the folder.
const SYNC_REUSE_MS = 15_000;

interface UseLocalWorkspaceOptions {
  // False for guests and embedded mode, where connecting a folder isn't offered at all.
  enabled: boolean;
}

/**
 * State and actions for the connected local folder (see localWorkspace.ts). The folder is uploaded
 * lazily — on connect, and again just before a message is sent (only what changed) — never in the
 * background, so nothing leaves the browser except as part of the user actually chatting.
 */
export function useLocalWorkspace({ enabled }: UseLocalWorkspaceOptions) {
  const supported = enabled && isLocalWorkspaceSupported();
  const [status, setStatus] = useState<LocalWorkspaceStatus>('disconnected');
  const [folderName, setFolderName] = useState<string | null>(null);
  const [fileCount, setFileCount] = useState<number | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  // The most recent applied edit that can still be undone (survives reloads — it lives in IndexedDB).
  const [lastEdit, setLastEdit] = useState<{ id: string; fileCount: number } | null>(null);

  const handleRef = useRef<FsDirectoryHandle | null>(null);
  const permittedRef = useRef(false);
  const stampsRef = useRef<Map<string, string>>(new Map());
  const serverCountRef = useRef<number | null>(null);
  const lastSyncAtRef = useRef(0);
  const inFlightRef = useRef<Promise<void> | null>(null);

  // Restore a previously chosen folder. Permission is only *queried* here (never requested — that
  // needs a click), and nothing is uploaded until the next message or an explicit action.
  useEffect(() => {
    if (!supported) return;
    let cancelled = false;
    (async () => {
      const latest = await latestUndoBatch();
      if (!cancelled && latest) setLastEdit({ id: latest.id, fileCount: latest.files.length });
      const handle = await loadFolderHandle();
      if (!handle || cancelled) return;
      const granted = await hasReadPermission(handle, false);
      if (cancelled) return;
      handleRef.current = handle;
      permittedRef.current = granted;
      setFolderName(handle.name);
      setStatus(granted ? 'connected' : 'needs-permission');
    })();
    return () => {
      cancelled = true;
    };
  }, [supported]);

  const runSync = useCallback((forceReset: boolean): Promise<void> => {
    const handle = handleRef.current;
    if (!handle || !permittedRef.current) return Promise.resolve();
    if (inFlightRef.current) return inFlightRef.current;

    const job = (async () => {
      setStatus('syncing');
      setMessage(null);
      try {
        // A delta is only safe if the server still holds what the last sync left there; after a
        // restart it won't, and a delta alone would leave a partial snapshot.
        const reset = forceReset || !(await serverSnapshotMatches(serverCountRef.current));
        const outcome = await syncFolder(handle, reset ? new Map() : stampsRef.current, reset);
        stampsRef.current = outcome.stamps;
        if (outcome.serverFileCount !== null) serverCountRef.current = outcome.serverFileCount;
        lastSyncAtRef.current = Date.now();
        setFileCount(serverCountRef.current);
        setStatus('connected');
        const notes: string[] = [];
        if (outcome.rejected.length) notes.push(`${outcome.rejected.length} file(s) were not uploaded`);
        if (outcome.truncated) notes.push('the folder is larger than the sync limit, so part of it was left out');
        if (notes.length) setMessage(notes.join('; '));
      } catch (error) {
        serverCountRef.current = null; // forces a full re-sync next time
        setStatus('error');
        setMessage(error instanceof Error ? error.message : 'Could not sync the folder.');
      } finally {
        inFlightRef.current = null;
      }
    })();
    inFlightRef.current = job;
    return job;
  }, []);

  const connect = useCallback(async () => {
    let handle: FsDirectoryHandle;
    try {
      handle = await pickFolder(); // must be the first await: it needs the click's user activation
    } catch {
      return; // the user dismissed the picker
    }
    await saveFolderHandle(handle);
    handleRef.current = handle;
    permittedRef.current = true;
    stampsRef.current = new Map();
    serverCountRef.current = null;
    setFolderName(handle.name);
    await runSync(true);
  }, [runSync]);

  const reconnect = useCallback(async () => {
    const handle = handleRef.current;
    if (!handle) return;
    if (!(await hasReadPermission(handle, true))) return;
    permittedRef.current = true;
    stampsRef.current = new Map();
    serverCountRef.current = null;
    await runSync(true);
  }, [runSync]);

  const resync = useCallback(() => runSync(true), [runSync]);

  const disconnect = useCallback(async () => {
    handleRef.current = null;
    permittedRef.current = false;
    stampsRef.current = new Map();
    serverCountRef.current = null;
    setFolderName(null);
    setFileCount(null);
    setMessage(null);
    setStatus('disconnected');
    await clearFolderHandle();
    try {
      await disconnectLocalWorkspace();
    } catch {
      // The server copy also expires on its own; the folder is disconnected locally either way.
    }
  }, []);

  const refreshLastEdit = useCallback(async () => {
    const latest = await latestUndoBatch();
    setLastEdit(latest ? { id: latest.id, fileCount: latest.files.length } : null);
  }, []);

  /** Writes an approved proposal into the folder (the only way Sonic's edits reach disk). */
  const applyEdits = useCallback(async (proposal: EditProposal): Promise<ApplyResult> => {
    const handle = handleRef.current;
    if (!handle) return { ok: false, errors: ['Connect your local folder first.'] };
    const result = await applyProposal(handle, proposal);
    // Force the next message to re-scan the folder so Sonic sees what was just written.
    lastSyncAtRef.current = 0;
    await refreshLastEdit();
    return result;
  }, [refreshLastEdit]);

  const undoEdits = useCallback(async (proposalId: string): Promise<UndoResult> => {
    const handle = handleRef.current;
    if (!handle) return { ok: false, errors: ['Connect your local folder first.'] };
    const result = await undoBatch(handle, proposalId);
    lastSyncAtRef.current = 0;
    await refreshLastEdit();
    return result;
  }, [refreshLastEdit]);

  /** Called just before a chat message is sent. Never throws — a sync problem must not block the
   *  message; Sonic just falls back to GitHub for that turn. */
  const ensureSynced = useCallback(async () => {
    if (!handleRef.current || !permittedRef.current) return;
    if (serverCountRef.current !== null && Date.now() - lastSyncAtRef.current < SYNC_REUSE_MS) return;
    await runSync(false);
  }, [runSync]);

  return {
    supported, status, folderName, fileCount, message, lastEdit,
    // Applying only needs the folder handle: the write permission is requested at Apply time.
    canEdit: supported && folderName !== null,
    connect, reconnect, resync, disconnect, ensureSynced, applyEdits, undoEdits,
  };
}

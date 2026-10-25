import { useCallback, useEffect, useRef, useState } from 'react';
import { disconnectLocalWorkspace, getLocalWorkspaceStatus } from '../api';
import { applyProposal, latestUndoBatch, undoBatch, type ApplyResult, type UndoResult } from '../localEdits';
import type { EditProposal } from '../localEditsCore';
import { restorableStamps, SyncRequestError } from '../localWorkspaceCore';
import {
  clearFolderHandle,
  clearSyncState,
  hasReadPermission,
  isLocalWorkspaceSupported,
  loadFolderHandle,
  loadSyncState,
  pickFolder,
  readGitBranch,
  saveFolderHandle,
  saveSyncState,
  serverSnapshotMatches,
  syncFolder,
  type FsDirectoryHandle,
} from '../localWorkspace';

// 'not-uploaded': the folder is chosen and readable, but the server has no copy of it right now (a restart or expiry lost it, or it was never sent). The next message uploads it.
export type LocalWorkspaceStatus = 'disconnected' | 'needs-permission' | 'not-uploaded' | 'connected' | 'syncing' | 'error';

// A message sent within this window of the last sync reuses it instead of re-scanning the folder.
const SYNC_REUSE_MS = 15_000;
// Coming back to the tab re-checks that the server still holds the copy, at most this often.
const VERIFY_EVERY_MS = 30_000;

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
  const lastVerifyAtRef = useRef(0);

  /** Asks the server whether it holds the copy this browser thinks it uploaded, and makes the status say what is true: 'connected' only when the server has exactly those files.
   *  A server that lost the copy (restart, expiry) or holds another one gives 'not-uploaded'; after a reload the remembered stamps are adopted only when they match the server,
   *  so an intact copy is not uploaded again. If the server cannot be reached the status is left as it was. */
  const verify = useCallback(async () => {
    const handle = handleRef.current;
    if (!handle || !permittedRef.current || inFlightRef.current) return;
    lastVerifyAtRef.current = Date.now();
    let server;
    try {
      server = await getLocalWorkspaceStatus();
    } catch {
      return;
    }
    if (inFlightRef.current) return; // an upload started while the answer was on its way, and it will say what is true
    if (serverCountRef.current === null) {
      const stamps = restorableStamps(await loadSyncState(), handle.name, server);
      if (stamps && server.file_count !== undefined) {
        stampsRef.current = stamps;
        serverCountRef.current = server.file_count;
        lastSyncAtRef.current = Date.now();
        setFileCount(server.file_count);
        setStatus('connected');
      } else {
        setFileCount(null);
        setStatus(current => (current === 'error' ? current : 'not-uploaded'));
      }
      return;
    }
    if (server.connected && server.file_count === serverCountRef.current) {
      setStatus(current => (current === 'not-uploaded' ? 'connected' : current));
      return;
    }
    stampsRef.current = new Map();
    serverCountRef.current = null;
    setFileCount(null);
    setStatus(current => (current === 'error' ? current : 'not-uploaded'));
  }, []);

  // Restore a previously chosen folder. Permission is only *queried* here (never requested — that
  // needs a click), and nothing is uploaded until the next message or an explicit action. The
  // dot only says "connected" once the server has confirmed it holds the copy.
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
      if (!granted) {
        setStatus('needs-permission');
        return;
      }
      setStatus('not-uploaded');
      await verify();
    })();
    return () => {
      cancelled = true;
    };
  }, [supported, verify]);

  // Coming back to the tab (a phone woken from the lock screen, a laptop opened again) is when the server's copy is most likely to have gone.
  useEffect(() => {
    if (!supported) return;
    const recheck = () => {
      if (document.visibilityState === 'hidden') return;
      if (Date.now() - lastVerifyAtRef.current < VERIFY_EVERY_MS) return;
      void verify();
    };
    document.addEventListener('visibilitychange', recheck);
    window.addEventListener('focus', recheck);
    return () => {
      document.removeEventListener('visibilitychange', recheck);
      window.removeEventListener('focus', recheck);
    };
  }, [supported, verify]);

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
        let outcome;
        try {
          outcome = await syncFolder(handle, reset ? new Map() : stampsRef.current, reset);
        } catch (error) {
          // Another tab started its own upload and replaced this one (409): start over once, as the newer upload may already be done.
          if (!(error instanceof SyncRequestError) || error.status !== 409) throw error;
          outcome = await syncFolder(handle, new Map(), true);
        }
        stampsRef.current = outcome.stamps;
        if (outcome.serverFileCount !== null) serverCountRef.current = outcome.serverFileCount;
        lastSyncAtRef.current = Date.now();
        if (serverCountRef.current !== null) void saveSyncState(handle.name, serverCountRef.current, outcome.stamps);
        setFileCount(serverCountRef.current);
        setStatus('connected');
        const notes: string[] = [];
        if (outcome.rejected.length) notes.push(`${outcome.rejected.length} file(s) were not uploaded`);
        if (outcome.truncated) notes.push('the folder is larger than the sync limit, so part of it was left out');
        if (notes.length) setMessage(notes.join('; '));
      } catch (error) {
        // Nothing about the folder's contents: only which step failed and how, so a failure that happens on someone's machine can be seen in the browser console.
        console.warn('[folder] sync failed:', error instanceof SyncRequestError ? `${error.message} (status ${error.status ?? 'none'})` : error);
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
    await clearSyncState();
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

  /** The git branch the connected folder is on (from `.git/HEAD`), or null. Sent with each message so a commit can go to the branch the person is working on. */
  const currentGitBranch = useCallback(async (): Promise<string | null> => {
    const handle = handleRef.current;
    return handle && permittedRef.current ? readGitBranch(handle) : null;
  }, []);

  /** True when the browser has a readable folder, so the server is expected to hold a copy: the chat request says so, and the answer can then tell the person if it did not. */
  const folderExpected = useCallback(() => handleRef.current !== null && permittedRef.current, []);

  return {
    supported, status, folderName, fileCount, message, lastEdit, folderExpected, currentGitBranch,
    // Applying only needs the folder handle: the write permission is requested at Apply time.
    canEdit: supported && folderName !== null,
    connect, reconnect, resync, disconnect, ensureSynced, applyEdits, undoEdits,
  };
}

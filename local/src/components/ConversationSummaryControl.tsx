import React, { useCallback, useEffect, useRef, useState } from 'react';
import { api, type ConversationSummary, type ConversationSummaryItem } from '../api';
import Icon from './icons';
import { canCompact, compactMessage, itemCount, progressText, shouldShow } from './conversationSummaryLogic';
import './__styles__/ConversationSummaryControl.css';

interface Props {
  sessionId: string;
  /** True while a reply is being made; the summary is brought up to date after a reply, so it is read again a little after it ends. */
  busy: boolean;
  /** How many messages the person has sent in this conversation (nothing is folded in the first few). */
  turns: number;
}

/** Look again this long after a reply ends: a fold is one small model call that runs after the reply is sent. */
const RECHECK_MS = [6000, 16000];

/**
 * The chat header's "Conversation so far": Sonic's short, quoted summary of the older messages of this conversation (the newest ones are always kept word for word). It appears once there is a summary,
 * or once there is something to fold. Everything in it can be read, edited, deleted or added to here, and "Fold now" folds the older messages into it on request; what the person wrote or changed is
 * never touched by Sonic again. Nothing here leaves the conversation.
 */
export const ConversationSummaryControl: React.FC<Props> = ({ sessionId, busy, turns }) => {
  const [summary, setSummary] = useState<ConversationSummary | null>(null);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<{ id: string; text: string } | null>(null);
  const [adding, setAdding] = useState<{ kind: string; text: string } | null>(null);
  const [message, setMessage] = useState('');
  const [folding, setFolding] = useState(false);
  const wasBusy = useRef(false);
  const wrapRef = useRef<HTMLDivElement | null>(null);

  const load = useCallback(async () => {
    try {
      setSummary(await api.getConversationSummary(sessionId));
    } catch {
      /* no summary control is the normal outcome when there is nothing to show */
    }
  }, [sessionId]);

  useEffect(() => {
    setSummary(null);
    setOpen(false);
    setEditing(null);
    setAdding(null);
    setMessage('');
    void load();
  }, [sessionId, load]);

  useEffect(() => {
    if (busy) {
      wasBusy.current = true;
      return undefined;
    }
    if (!wasBusy.current || turns < 4) return undefined;
    wasBusy.current = false;
    const timers = RECHECK_MS.map(ms => window.setTimeout(() => void load(), ms));
    return () => timers.forEach(timer => window.clearTimeout(timer));
  }, [busy, turns, load]);

  useEffect(() => {
    if (!open) return undefined;
    const onDown = (event: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [open]);

  if (!shouldShow(summary) || !summary) return null;

  const count = itemCount(summary);

  const change = async (itemId: string, text: string | null, kind?: string) => {
    setMessage('');
    try {
      setSummary(await api.editConversationSummary(sessionId, itemId, text, kind));
      setEditing(null);
      setAdding(null);
    } catch (err) {
      setMessage(err instanceof Error ? err.message : 'Failed to change that item.');
    }
  };

  const foldNow = async () => {
    setMessage('');
    setFolding(true);
    try {
      const next = await api.compactConversationSummary(sessionId);
      setSummary(next);
      setMessage(compactMessage(!!next.compacted));
    } catch (err) {
      setMessage(err instanceof Error ? err.message : 'Failed to fold the conversation into a summary.');
    } finally {
      setFolding(false);
    }
  };

  const clear = async () => {
    if (!window.confirm('Clear the summary of this conversation? Your messages stay as they are.')) return;
    try {
      await api.clearConversationSummary(sessionId);
      await load();
      setOpen(false);
    } catch (err) {
      setMessage(err instanceof Error ? err.message : 'Failed to clear the summary.');
    }
  };

  const row = (item: ConversationSummaryItem) => (
    <li key={item.id} className="convo-summary-item">
      {editing?.id === item.id ? (
        <form className="convo-summary-edit" onSubmit={event => { event.preventDefault(); void change(item.id, editing.text); }}>
          <input autoFocus value={editing.text} maxLength={140} onChange={event => setEditing({ id: item.id, text: event.target.value })} aria-label="Edit this item" />
          <button type="submit" className="convo-summary-icon" aria-label="Save"><Icon name="check" size={14} /></button>
          <button type="button" className="convo-summary-icon" aria-label="Cancel" onClick={() => setEditing(null)}><Icon name="close" size={14} /></button>
        </form>
      ) : (
        <>
          <button type="button" className="convo-summary-text" onClick={() => setEditing({ id: item.id, text: item.text })}
            title={item.by === 'person' ? 'Written by you. Click to edit.' : item.evidence ? `From the conversation: "${item.evidence}". Click to edit.` : 'Click to edit.'}>
            {item.text}
            {item.by === 'person' && <span className="convo-summary-yours">yours</span>}
          </button>
          <button type="button" className="convo-summary-icon" aria-label="Delete this item" onClick={() => void change(item.id, null)}><Icon name="trash" size={14} /></button>
        </>
      )}
    </li>
  );

  return (
    <div className="convo-summary" ref={wrapRef}>
      <button type="button" className="topbar-btn convo-summary-btn" aria-expanded={open} aria-label="Conversation so far" title="Conversation so far" onClick={() => setOpen(v => !v)}>
        <Icon name="conversations" size={18} />
        {count > 0 && <span className="convo-summary-count" aria-hidden="true">{count}</span>}
      </button>
      {open && (
        <div className="convo-summary-panel" role="dialog" aria-label="Conversation so far">
          <p className="convo-summary-intro">
            Sonic's short summary of the older messages in this conversation. It always reads your newest messages word for word, and this for everything before them. Change anything and Sonic won't undo it.
          </p>
          {summary.sections.map(section => (
            <section key={section.kind}>
              <h4>{section.label}</h4>
              <ul>{section.items.map(row)}</ul>
              {adding?.kind === section.kind ? (
                <form className="convo-summary-edit" onSubmit={event => { event.preventDefault(); void change('new', adding.text, section.kind); }}>
                  <input autoFocus value={adding.text} maxLength={140} placeholder="Add a line" onChange={event => setAdding({ kind: section.kind, text: event.target.value })} aria-label={`Add to ${section.label}`} />
                  <button type="submit" className="convo-summary-icon" aria-label="Add"><Icon name="check" size={14} /></button>
                  <button type="button" className="convo-summary-icon" aria-label="Cancel" onClick={() => setAdding(null)}><Icon name="close" size={14} /></button>
                </form>
              ) : (
                <button type="button" className="convo-summary-add" onClick={() => setAdding({ kind: section.kind, text: '' })}>+ Add</button>
              )}
            </section>
          ))}
          <p className="convo-summary-progress">{progressText(summary)}</p>
          <div className="convo-summary-actions">
            <button type="button" className="convo-summary-fold" disabled={folding || !canCompact(summary)} onClick={() => void foldNow()}>{folding ? 'Folding…' : 'Fold now'}</button>
            {count > 0 && <button type="button" className="convo-summary-clear" onClick={() => void clear()}>Clear this summary</button>}
          </div>
          {message && <p className="convo-summary-msg" role="status">{message}</p>}
        </div>
      )}
    </div>
  );
};

export default ConversationSummaryControl;

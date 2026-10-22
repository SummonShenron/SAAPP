import React, { useEffect, useRef } from 'react';
import { Link } from 'react-router-dom';
import type { MemorySources } from '../api';
import './__styles__/MemorySourceDialog.css';

/**
 * What a "Source: Memory" citation opens: the notes from past conversations that matched the question, and the saved facts that relate to it
 * (backend/utils/memory_sources.py). It is the person's own data; each fact can be corrected on the Memory page. A reply saved before this existed has
 * nothing to show, and says so.
 */
const MemorySourceDialog: React.FC<{ sources: MemorySources | undefined; onClose: () => void }> = ({ sources, onClose }) => {
  const closeRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    closeRef.current?.focus();
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const notes = sources?.notes ?? [];
  const facts = sources?.facts ?? [];
  const empty = notes.length === 0 && facts.length === 0;

  return (
    <div className="memory-source-backdrop" onClick={onClose}>
      <div className="memory-source-dialog" role="dialog" aria-modal="true" aria-labelledby="memory-source-title" onClick={(event) => event.stopPropagation()}>
        <div className="memory-source-head">
          <h2 id="memory-source-title">What I drew on from your memory</h2>
          <button type="button" ref={closeRef} className="memory-source-close" aria-label="Close" onClick={onClose}>×</button>
        </div>

        {empty && (
          <p className="memory-source-empty">
            The notes behind this answer were not saved with it. Everything I remember about you is on the Memory page, where you can correct or delete any of it.
          </p>
        )}

        {notes.length > 0 && (
          <section>
            <h3>From past conversations</h3>
            <ul className="memory-source-list">
              {notes.map((note, index) => (
                <li key={index}>
                  {note.when && <span className="memory-source-when">{note.when}</span>}
                  <span>{note.text}</span>
                </li>
              ))}
            </ul>
          </section>
        )}

        {facts.length > 0 && (
          <section>
            <h3>Saved facts that relate</h3>
            <ul className="memory-source-list">
              {facts.map((fact, index) => (
                <li key={fact.id || index}>
                  {fact.category && <span className="memory-source-when">{fact.category}</span>}
                  <span>{fact.text}</span>
                </li>
              ))}
            </ul>
          </section>
        )}

        <div className="memory-source-foot">
          <Link to="/memory" onClick={onClose}>Open the Memory page</Link>
        </div>
      </div>
    </div>
  );
};

export default MemorySourceDialog;

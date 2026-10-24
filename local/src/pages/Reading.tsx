import React, { useEffect, useState } from 'react';
import { api, type PublicShelfItem } from '../api';
import { SHELF_TOPIC_LABELS, shortDate } from './sonicHealthFormat';
import './__styles__/SelfService.css';
import './__styles__/Reading.css';

/**
 * What Sonic has been reading: the pieces on its own reading shelf, with the summary it wrote for each. Read-only and the same for everyone,
 * because the shelf is Sonic's, not a person's: nothing you say here or in a chat chooses what it reads.
 */
export const ReadingPage: React.FC = () => {
  const [items, setItems] = useState<PublicShelfItem[] | null>(null);
  const [topics, setTopics] = useState<string[]>([]);
  const [topic, setTopic] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    api.getPublicReadingShelf()
      .then(out => { if (!cancelled) { setItems(out.items); setTopics(out.topics); } })
      .catch((err: unknown) => { if (!cancelled) setError(err instanceof Error ? err.message : 'Failed to load what Sonic has been reading.'); });
    return () => { cancelled = true; };
  }, []);

  if (error) return <div className="self-service-container"><div className="reading-notice" role="alert">{error}</div></div>;
  if (!items) return <div className="self-service-loading">Loading what Sonic has been reading...</div>;

  const shown = topic ? items.filter(i => i.topic === topic) : items;

  return (
    <div className="self-service-container">
      <header className="self-service-header">
        <h1>Reading</h1>
        <p>Pieces Sonic found and read on its own, about the things it is curious about, with a short note on each in its own words.</p>
      </header>

      <div className="vertical-card-stack">
        <section className="service-card">
          <div className="card-badge">{items.length} ON THE SHELF</div>
          <h2>Sonic's reading shelf</h2>
          <p className="card-description">
            This is Sonic's own reading. It picks the topics itself and reads about one new thing a day or so, so it is not something you can ask it to
            research. If it says it came across something in a chat, the piece is here. The notes are summaries, not the articles, so open the link for the real thing.
          </p>

          {topics.length > 1 && (
            <div className="reading-filters" role="group" aria-label="Filter by topic">
              <button type="button" className={`reading-chip${topic === '' ? ' active' : ''}`} onClick={() => setTopic('')}>All</button>
              {topics.map(t => (
                <button key={t} type="button" className={`reading-chip${topic === t ? ' active' : ''}`} onClick={() => setTopic(t)}>
                  {SHELF_TOPIC_LABELS[t] ?? t}
                </button>
              ))}
            </div>
          )}

          {items.length === 0 && <div className="reading-empty">Nothing on the shelf yet. Sonic adds to it as it reads.</div>}

          {shown.map(item => (
            <article key={item.url} className="reading-item">
              <div className="reading-title">
                <a href={item.url} target="_blank" rel="noopener noreferrer">{item.title}</a>
              </div>
              <p className="reading-summary">{item.summary}</p>
              {item.reflection && <p className="reading-takeaway"><strong>What I take from it:</strong> {item.reflection}</p>}
              <div className="reading-meta">
                <span className="reading-topic">{SHELF_TOPIC_LABELS[item.topic] ?? item.topic}</span>
                {item.domain} · read {shortDate(item.read_at)}
              </div>
            </article>
          ))}
        </section>
      </div>
    </div>
  );
};

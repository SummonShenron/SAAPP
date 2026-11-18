/* eslint-disable react-refresh/only-export-components -- a dev gallery, not part of the app */
import { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import '/src/index.css';
import '/src/components/__styles__/AppShell.css';
import MiniPatchy from './components/MiniPatchy';
import PatchyHero from './components/PatchyHero';
import TracePanel from './components/TracePanel';
import LogoMark from './components/LogoMark';
import type { PatchyBudget, PatchyEnergy, PatchyLook } from './components/patchyLook';

// A gallery of every Patchy look and mood, for checking them by eye (open /patchy-preview.html on the dev server).
// `bump` repeats a short-lived trigger every few seconds so its animation can be watched: a failure, a celebration, a steer nod, typing or a poke.
type Bump = 'fault' | 'cheer' | 'praise' | 'sad' | 'steer' | 'poke' | 'greet' | 'plan';

function Cell({ label, look = 'idle', energy = 'open', budget = 'ok', bump, drowsy, hero, engaged, typed = 0, loop, loopCheer }: {
  label: string; look?: PatchyLook; energy?: PatchyEnergy; budget?: PatchyBudget; bump?: Bump; drowsy?: boolean; hero?: boolean; engaged?: boolean; typed?: number; loop?: boolean; loopCheer?: boolean;
}) {
  const [n, setN] = useState(0);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!bump) return;
    const tick = () => {
      if (bump === 'poke') box.current?.querySelector('button')?.click();
      else setN(c => c + 1);
    };
    const first = setTimeout(tick, 600);
    const every = setInterval(tick, bump === 'fault' || bump === 'plan' ? 8000 : 3500);
    return () => { clearTimeout(first); clearInterval(every); };
  }, [bump]);
  const signals = {
    look, energy, budget, loop, loopCheer,
    cheer: bump === 'cheer' ? n : 0,
    fault: bump === 'fault' ? n : 0,
    praise: bump === 'praise' ? n : 0,
    sad: bump === 'sad' ? n : 0,
    steer: bump === 'steer' ? n : 0,
    greet: bump === 'greet' ? n : 0,
    planDone: bump === 'plan' ? n % 2 === 1 : false,
    engaged,
    typed,
    drowsyAfterMs: drowsy ? 400 : undefined,
  };
  return (
    <div ref={box} style={{ display: 'inline-block', verticalAlign: 'top', margin: '0 14px 46px 0', width: hero ? 170 : 150, textAlign: 'center', fontFamily: 'sans-serif', fontSize: 11 }}>
      {hero ? (
        <div style={{ height: 140, display: 'flex', justifyContent: 'center' }}><PatchyHero {...signals} /></div>
      ) : (
        <form className="chat-input-area" style={{ position: 'relative', height: 54 }}>
          <div className="chat-input-wrapper">
            <MiniPatchy {...signals} />
            <textarea className="chat-textarea" placeholder="Ask a question..." rows={1} style={{ minHeight: 40 }} />
          </div>
        </form>
      )}
      <div style={{ marginTop: 4 }}>{label}</div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section style={{ marginBottom: 8 }}>
      <h4 style={{ font: '700 12px sans-serif', margin: '14px 0 0', color: '#64748b' }}>{title}</h4>
      <div style={{ marginTop: 64 }}>{children}</div>
    </section>
  );
}

const params = new URLSearchParams(window.location.search);
// ?slow=5 makes every timer five times longer (the stretch's stages with it), so a stage lasts long enough to look at; the CSS eases stay in real time.
const slowBy = Number(params.get('slow')) || 1;
if (slowBy !== 1) {
  const realSetTimeout = window.setTimeout.bind(window);
  window.setTimeout = ((fn: TimerHandler, ms?: number, ...args: unknown[]) => realSetTimeout(fn, (ms ?? 0) * slowBy, ...args)) as typeof window.setTimeout;
  // and the animation clock, so the script-driven motion (the arms, the laptop being carried) slows with the stages
  const realNow = performance.now.bind(performance);
  const realRaf = window.requestAnimationFrame.bind(window);
  performance.now = () => realNow() / slowBy;
  window.requestAnimationFrame = ((cb: FrameRequestCallback) => realRaf(t => cb(t / slowBy))) as typeof window.requestAnimationFrame;
}
document.body.style.background = 'var(--bg-main)';
if (params.get('dark')) document.body.classList.add('theme-shadow');


// ?empty=1 shows the empty chat as the app lays it out: the full-size Patchy above the name and sub header, the input below.
const emptyChat = (
  <div style={{ height: '100vh', display: 'flex', flexDirection: 'column', background: 'var(--bg-main)' }}>
    <div className="chat-page" style={{ flex: '1 1 auto', minHeight: 0 }}>
      <main className="portal-body initial-state-view">
        <div className="empty-brand">
          <PatchyHero />
          <h2 className="empty-brand-title">Sonic Assistant</h2>
          <p className="empty-brand-sub">Powered by Sonic Graph Technologies.</p>
        </div>
        <div className="example-cards-container">
          {['What can you do?', 'Summarize my last conversation', 'Search the web for me'].map(q => (
            <div className="query-card" key={q}><p>{q}</p><span>→</span></div>
          ))}
        </div>
        <footer className="controls-footer">
          <form className="chat-input-area">
            <div className="chat-input-wrapper">
              <textarea className="chat-textarea" placeholder="Ask a question..." rows={1} />
            </div>
          </form>
        </footer>
      </main>
    </div>
  </div>
);


// ?chat=1: a conversation as the app lays it out (rail, messages, input with Patchy; &blade=1 adds the trace blade), for checking a theme. Add &dark=1 for the dark theme.
const chatMock = (
  <div className={`portal-container app-shell${params.get('dark') ? ' theme-shadow' : ''}`}>
    <aside className="app-rail" style={{ position: 'relative', width: 200, flex: '0 0 auto' }}>
      <div className="rail-brand" style={{ padding: 14, color: 'var(--text-main)', font: '700 14px sans-serif', display: 'flex', alignItems: 'center', gap: 8 }}><LogoMark size={26} /> Sonic Assistant</div>
      <div className="rail-item active" style={{ margin: '0 8px' }}>Chat</div>
      <div className="rail-item" style={{ margin: '0 8px' }}>Memory</div>
      <div className="rail-convo active" style={{ margin: '12px 8px 0' }}>Retry logic question</div>
      <div className="rail-convo" style={{ margin: '0 8px' }}>Budget guard notes</div>
      <div className="user-chip" style={{ position: 'absolute', left: 8, right: 8, bottom: 8 }}><span className="user-avatar">J</span> jack</div>
    </aside>
    <div className="app-main">
    <header className="app-topbar"><h1 className="topbar-title">Retry logic question</h1><span className="topbar-sub">Powered by Sonic Graph Technologies</span></header>
    <div className="app-content">
    <div className="chat-page">
      <main className="portal-body">
        <div className="chat-window" style={{ flex: '1 1 auto', minHeight: 0 }}>
          <div className="message-bubble user"><div className="message-sender">You</div><div className="message-text">How does the retry logic handle a slow database?</div></div>
          <div className="message-bubble ai"><div className="message-sender">Sonic</div><div className="message-text">It backs off, retries up to three times, and then reports the failure instead of hanging. Each attempt is logged with its duration so slow ones are easy to find later on.</div></div>
          <div className="message-bubble user"><div className="message-sender">You</div><div className="message-text">And what about the budget guard?</div></div>
          <div className="message-bubble ai"><div className="message-sender">Sonic</div><div className="message-text">It tracks usage in a fixed five hour window and pauses chat at the limit.</div></div>
        </div>
        <footer className="controls-footer">
          <form className="chat-input-area">
            <div className="chat-input-wrapper">
              <MiniPatchy />
              <textarea className="chat-textarea" placeholder="Ask a question..." rows={2} />
            </div>
          </form>
        </footer>
      </main>
      {params.get('blade') && <aside className="trace-blade">
        <TracePanel onClose={() => {}} running={!params.get('done')} steps={[
          { id: '1', title: 'Analyzing intent and planning route...', detail: 'Node: coordinator_node', status: 'complete' },
          { id: '2', title: 'GraphRAG Retrieval in progress...', detail: 'Node: retrieve_node', status: 'complete' },
          { id: '3', title: 'Generating response...', detail: 'Node: conversational_node', status: params.get('done') ? 'complete' : 'active' },
        ]} />
      </aside>}
    </div>
    </div>
    </div>
  </div>
);

// ?live=1: the small Patchy wired to a real textarea the way Chat.tsx does it (a click or typing engages him, leaving the box ends it; the page's own focus does not).
function Live() {
  const [engaged, setEngaged] = useState(false);
  const [text, setText] = useState('');
  return (
    <form className="chat-input-area" style={{ width: 420, margin: '120px 40px' }}>
      <div className="chat-input-wrapper">
        <MiniPatchy engaged={engaged} typed={text.length} />
        <textarea
          className="chat-textarea"
          placeholder="Click here"
          rows={2}
          autoFocus
          value={text}
          onChange={e => { setText(e.target.value); setEngaged(true); }}
          onPointerDown={() => setEngaged(true)}
          onBlur={() => setEngaged(false)}
        />
      </div>
    </form>
  );
}

// ?stretch=1: the small Patchy's stretch (patchyStretch.ts), shown big and started again every 12 seconds (he stretches once each time he appears, a moment after he is idle). &slow=3 plays it three times slower.
function StretchLoop() {
  const [n, setN] = useState(0);
  useEffect(() => {
    const every = setInterval(() => setN(c => c + 1), 12000 * (Number(params.get('slow')) || 1));
    return () => clearInterval(every);
  }, []);
  return (
    <form className="chat-input-area" style={{ width: 190, margin: '190px 0 0 20px', transform: 'scale(4)', transformOrigin: '0 0' }}>
      <div className="chat-input-wrapper">
        <MiniPatchy key={n} />
        <textarea className="chat-textarea" placeholder="Ask a question..." rows={2} />
      </div>
    </form>
  );
}

// ?one=yawn | loop | loop-search | think: one small Patchy, shown big, to look at a single look closely.
function One({ what }: { what: string }) {
  return (
    <div style={{ transform: 'scale(4.5)', transformOrigin: '0 0', margin: '230px 0 0 120px', width: 190 }}>
      {what === 'yawn' && <Cell label="" drowsy />}
      {what === 'loop' && <Cell label="" look="writing" loop />}
      {what === 'loop-search' && <Cell label="" look="searching" loop />}
      {what === 'think' && <Cell label="" look="thinking" />}
      {what === 'victory' && <Cell label="" bump="cheer" loopCheer />}
      {what === 'hello' && <Cell label="" bump="greet" />}
      {what === 'tada' && <Cell label="" bump="plan" />}
      {what === 'steerloop' && <Cell label="" look="writing" loop bump="steer" />}
    </div>
  );
}

createRoot(document.getElementById('root')!).render(
  params.get('chat') ? chatMock : params.get('live') ? <Live /> : params.get('stretch') ? <StretchLoop /> : params.get('one') ? <One what={params.get('one')!} /> : params.get('empty') ? emptyChat : <div>
    <Section title="New reactions: praise (fist pump) and sad (still, head down, eyes on his feet)">
      <Cell label="praise" bump="praise" />
      <Cell label="praise, subdued" bump="praise" energy="subdued" />
      <Cell label="sad" bump="sad" />
      <Cell label="sad, subdued" bump="sad" energy="subdued" />
    </Section>
    <Section title="What he does while a turn runs">
      <Cell label="idle" />
      <Cell label="thinking" look="thinking" />
      <Cell label="searching" look="searching" />
      <Cell label="puzzled" look="puzzled" />
      <Cell label="writing" look="writing" />
      <Cell label="tool loop (gears)" look="writing" loop />
      <Cell label="tool loop, searching" look="searching" loop />
    </Section>
    <Section title="After a turn, and your actions">
      <Cell label="done (cheer)" bump="cheer" />
      <Cell label="done after a tool loop (victory hop)" bump="cheer" loopCheer />
      <Cell label="steered in a tool loop (gears shift)" look="writing" loop bump="steer" />
      <Cell label="opened an old chat (hello)" bump="greet" />
      <Cell label="long plan finished (ta-da)" bump="plan" />
      <Cell label="error (fault)" bump="fault" />
      <Cell label="steered (got it)" look="writing" bump="steer" />
      <Cell label="box engaged" engaged />
      <Cell label="reading what you type" engaged typed={12} />
      <Cell label="poked" bump="poke" />
    </Section>
    <Section title="Quiet and the usage budget">
      <Cell label="drowsy (long quiet)" drowsy />
      <Cell label="budget: notice" budget="notice" />
      <Cell label="budget: throttled" budget="throttled" />
      <Cell label="budget: blocked (asleep)" budget="blocked" />
    </Section>
    <Section title="When the person is low (subdued): quieter, no alarm, no sweat, no giggle">
      <Cell label="idle" energy="subdued" />
      <Cell label="thinking" look="thinking" energy="subdued" />
      <Cell label="searching" look="searching" energy="subdued" />
      <Cell label="puzzled" look="puzzled" energy="subdued" />
      <Cell label="error" energy="subdued" bump="fault" />
      <Cell label="poked" energy="subdued" bump="poke" />
    </Section>
    <Section title="Full size (empty chat)">
      <Cell hero label="idle" />
      <Cell hero label="box engaged" engaged />
      <Cell hero label="reading" engaged typed={12} />
      <Cell hero label="drowsy" drowsy />
      <Cell hero label="blocked" budget="blocked" />
      <Cell hero label="poked" bump="poke" />
      <Cell hero label="subdued" energy="subdued" />
      <Cell hero label="thinking" look="thinking" />
      <Cell hero label="searching" look="searching" />
      <Cell hero label="puzzled" look="puzzled" />
    </Section>
  </div>
);

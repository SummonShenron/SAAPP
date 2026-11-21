/* eslint-disable -- a development page that is not part of the app: it reads refs while rendering and defines components beside the render on purpose, to measure the old and the new stream side by side */
// A development page (open /stream-preview.html from the dev server): streams one long reply into a chat window the OLD way and the NEW way, with the same bursty tokens, and measures both.
//   before: every chunk is appended to the message the moment it arrives, the page is snapped to the bottom on every chunk, the Markdown of every earlier message is re-parsed on every chunk (a fresh `components`
//           object each render, as the page used to do), and words fade in the old way.
//   after:  chunks go through the pacer (components/streamPacer.ts), the page follows with the eased follower (pages/streamFollow.ts), and the message text is the memoized MessageMarkdown.
// `await window.__run('before' | 'after')` plays one and resolves with the numbers; the buttons do the same by hand. Nothing here is used by the app.
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import './index.css';
import './components/__styles__/AppShell.css';
import './components/__styles__/ReplyText.css';
import { MessageMarkdown } from './components/MessageMarkdown';
import { rehypeStreamWords } from './components/streamFade';
import { StreamPacer } from './components/streamPacer';
import { StreamFollower } from './pages/streamFollow';

const REPLY = `Here is how the stream works, in plain steps, and where the jitter came from.

**First**, the server sends the reply in *bursts*: a few characters, then nothing for a moment, then forty at once. Showing each burst the moment it arrives makes the text land in uneven lumps, and a word that is still arriving gets drawn half finished, wraps onto the next line, and moves again when it grows.

Three things were wrong, and each one is fixed on its own:

- The text was shown as it arrived, so the lumps were visible. Now whole words come out a few at a time at a steady pace.
- The page was snapped to the bottom on every chunk, so it lurched down a line at a time. Now it glides with the growing text.
- Every earlier message was parsed again on every chunk, so a long thread stuttered. Now only the message being written is touched.

A small example of the kind of code a reply can carry:

\`\`\`python
def stream(tokens, show):
    buffer = ""
    for token in tokens:
        buffer += token
        words, buffer = take_whole_words(buffer)
        show(words)
\`\`\`

After the code the reply carries on with more ordinary text, because real answers do that. It keeps going for a while so there is enough length to scroll through, and enough words that a fade on each one can be seen working. The point of the fade is that each word appears softly once, in place, and then stays still, the way the replies used to arrive in Copilot in the browser.

**Last**, when the stream ends, the words that were still waiting are shown quickly, the full text replaces them, and the page settles exactly on the bottom. If you scroll up while it is writing, it lets go and stays where you put it.`;

const HISTORY: string[] = Array.from({ length: 14 }, (_, i) => (i % 2 === 0
  ? `Question ${i / 2 + 1}: can you explain how the nightly job decides what to retry, and what happens to the ones that keep failing after the third attempt?`
  : `**Answer ${(i + 1) / 2}.** The job keeps a list of what failed and tries each one again with a longer wait each time, up to three attempts. After the third it stops, writes the reason next to the item, and leaves it for a person to look at.\n\n- It never retries something that was refused.\n- It logs the kind of failure, not the content.\n\nIf you want the exact numbers they are in the settings file, and I can walk through them with you.`));

// ---- the same bursty stream for both ------------------------------------------------------------------------------------------------------------

function mulberry(seed: number) {
  return () => { seed |= 0; seed = (seed + 0x6D2B79F5) | 0; let t = Math.imul(seed ^ (seed >>> 15), 1 | seed); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
}
function chunksOf(text: string, seed = 7): { text: string; wait: number }[] {
  const rand = mulberry(seed);
  const out: { text: string; wait: number }[] = [];
  for (let i = 0; i < text.length;) {
    const size = Math.max(2, Math.round(3 + rand() * rand() * 70));
    out.push({ text: text.slice(i, i + size), wait: Math.round(8 + rand() * rand() * 170) });
    i += size;
  }
  return out;
}

type Mode = 'before' | 'after' | 'whole';      // whole: the reply arrives as one finished text (a final_generation with no tokens) and is revealed through the pacer
interface Metrics { mode: Mode; frames: number; maxFrameMs: number; p95FrameMs: number; slowFrames: number; maxScrollStep: number; lurches: number; reversals: number; scrollStepStdDev: number;
  maxCharsPerFrame: number; revealStdDev: number; midWordFrames: number; wordsShown: number; endGapPx: number; seconds: number; reactCommits: number }

function stats(values: number[]) {
  const n = Math.max(values.length, 1);
  const mean = values.reduce((a, b) => a + b, 0) / n;
  return { mean, sd: Math.sqrt(values.reduce((a, b) => a + (b - mean) ** 2, 0) / n) };
}

const CodePlain = ({ code }: { language: string; code: string }) => <pre className="reply-code-block">{code}</pre>;

function History({ legacy }: { legacy: boolean }) {
  return (
    <>
      {HISTORY.map((text, i) => (
        <div key={i} className={`message-bubble ${i % 2 === 0 ? 'user' : 'ai'}`}>
          <div className="message-sender">{i % 2 === 0 ? 'USER' : 'SONIC'}</div>
          <div className="message-text">
            {legacy
              // The old way: a new `components` object every render, so every message is parsed again on every render of the list.
              ? <ReactMarkdown remarkPlugins={[remarkGfm]} components={LEGACY_COMPONENTS}>{text}</ReactMarkdown>
              : <MessageMarkdown content={text} streaming={false} messageId={`h${i}`} onOpenMemory={noop} getTokenRef={tokenRef} CodeBlock={CodePlain} />}
          </div>
        </div>
      ))}
    </>
  );
}
const noop = () => {};
const LEGACY_COMPONENTS = { code: ({ children }: { children?: React.ReactNode }) => <code className="reply-code">{children}</code> };
const tokenRef = { current: undefined } as React.MutableRefObject<(() => Promise<string | null>) | undefined>;

function App() {
  const [mode, setMode] = useState<Mode>('after');
  const [text, setText] = useState('');
  const [streaming, setStreaming] = useState(false);
  const [runId, setRunId] = useState(0);
  const [result, setResult] = useState<Metrics | null>(null);
  const winRef = useRef<HTMLDivElement>(null);
  const commits = useRef(0);
  const resolveRef = useRef<((m: Metrics) => void) | null>(null);

  commits.current += 1;

  const play = useCallback((chosen: Mode): Promise<Metrics> => new Promise(resolve => {
    resolveRef.current = resolve;
    setMode(chosen);
    setText('');
    setStreaming(false);
    setResult(null);
    setRunId(n => n + 1);
  }), []);
  useEffect(() => { (window as unknown as { __run: typeof play }).__run = play; }, [play]);

  useEffect(() => {
    if (runId === 0) return undefined;
    const win = winRef.current!;
    win.scrollTop = 0;
    const source = chunksOf(REPLY);
    let shown = '';
    const frameTimes: number[] = [];
    const tops: number[] = [];
    const lens: number[] = [];
    let midWord = 0;
    let lastFrame = performance.now();
    const startedAt = lastFrame;
    let raf = 0;
    let finished = false;
    let doneAt = 0;
    commits.current = 0;
    const startCommits = 0;

    const pacer = mode !== 'before' ? new StreamPacer(words => { shown += words; setText(shown); setStreaming(true); }) : null;
    const follower = mode !== 'before' ? new StreamFollower(() => winRef.current) : null;
    follower?.start();

    const measure = () => {
      const now = performance.now();
      frameTimes.push(now - lastFrame);
      lastFrame = now;
      tops.push(win.scrollTop);
      const el = win.querySelector('.stream-target');
      const len = el?.textContent?.length ?? 0;
      lens.push(len);
      const nextChar = REPLY[shown.length];
      if (shown && !/\s$/.test(shown) && nextChar !== undefined && !/\s/.test(nextChar)) midWord += 1;      // the text on screen ends in the middle of a word
      if (finished && now - doneAt > 700) { finish(); return; }
      raf = requestAnimationFrame(measure);
    };

    const finish = () => {
      cancelAnimationFrame(raf);
      follower?.stop();
      const steps = tops.slice(1).map((t, i) => t - tops[i]);
      const moving = steps.filter(s => Math.abs(s) > 0.01);
      const reveal = lens.slice(1).map((l, i) => l - lens[i]).filter(d => d !== 0);
      const sorted = [...frameTimes].sort((a, b) => a - b);
      const metrics: Metrics = {
        mode, frames: frameTimes.length, maxFrameMs: Math.round(Math.max(...frameTimes)), p95FrameMs: Math.round(sorted[Math.floor(sorted.length * 0.95)] ?? 0),
        slowFrames: frameTimes.filter(t => t > 34).length, maxScrollStep: Math.round(Math.max(...moving.map(Math.abs), 0)), lurches: moving.filter(s => s > 30).length,
        reversals: steps.filter(s => s < -1).length, scrollStepStdDev: Math.round(stats(moving).sd * 10) / 10, maxCharsPerFrame: Math.max(...reveal, 0),
        revealStdDev: Math.round(stats(reveal).sd * 10) / 10, midWordFrames: midWord, wordsShown: shown.trim().split(/\s+/).length,
        endGapPx: Math.round(win.scrollHeight - win.clientHeight - win.scrollTop), seconds: Math.round((performance.now() - startedAt) / 100) / 10, reactCommits: commits.current - startCommits,
      };
      setResult(metrics);
      resolveRef.current?.(metrics);
    };

    const end = () => {
      finished = true;
      doneAt = performance.now();
      setStreaming(false);
    };

    let index = 0;
    let timer = 0;
    const next = () => {
      const chunk = source[index];
      if (!chunk) {
        if (pacer) pacer.finish(() => { shown = REPLY; setText(REPLY); end(); });
        else end();
        return;
      }
      index += 1;
      timer = window.setTimeout(() => {
        if (pacer) pacer.push(chunk.text);
        else {
          shown += chunk.text;
          setText(shown);
          setStreaming(true);
          // the old behaviour: snap to the bottom on every chunk
          requestAnimationFrame(() => { if (win.scrollHeight - win.scrollTop - win.clientHeight <= 160) win.scrollTo({ top: win.scrollHeight, behavior: 'instant' as ScrollBehavior }); });
        }
        next();
      }, chunk.wait);
    };
    raf = requestAnimationFrame(measure);
    if (mode === 'whole' && pacer) {
      // Nothing streams: the finished text arrives at once. Before this change it appeared in one go; now it is revealed a word at a time.
      pacer.push(REPLY);
      pacer.drain(() => { shown = REPLY; setText(REPLY); end(); });
    } else next();
    return () => { window.clearTimeout(timer); cancelAnimationFrame(raf); pacer?.dispose(); follower?.stop(); };
  }, [runId, mode]);

  return (
    <div className="chat-page" style={{ height: '100vh', display: 'flex', flexDirection: 'column', background: 'var(--bg-main, #fff)' }}>
      <div style={{ display: 'flex', gap: 8, padding: 8, alignItems: 'center', fontFamily: 'sans-serif', fontSize: 13 }}>
        <button onClick={() => void play('before')}>Play BEFORE (old)</button>
        <button onClick={() => void play('after')}>Play AFTER (new)</button>
        <button onClick={() => void play('whole')}>Play WHOLE reply (new)</button>
        <span id="status">{streaming ? `streaming (${mode})…` : result ? `done (${result.mode})` : 'idle'}</span>
        <pre id="metrics" style={{ margin: 0, fontSize: 11 }}>{result ? JSON.stringify(result) : ''}</pre>
      </div>
      <div className="chat-window" ref={winRef} style={{ minHeight: 0, overflowY: 'auto', flex: 1 }}>
        <History legacy={mode === 'before'} />
        {runId > 0 && (
          <div className="message-bubble ai">
            <div className="message-sender">SONIC</div>
            <div className="message-text stream-target">
              {mode === 'before'
                ? <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={streaming ? [rehypeStreamWords] : undefined} components={LEGACY_COMPONENTS}>{text}</ReactMarkdown>
                : <MessageMarkdown content={text} streaming={streaming} messageId="live" onOpenMemory={noop} getTokenRef={tokenRef} CodeBlock={CodePlain} />}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

createRoot(document.getElementById('root')!).render(<App />);

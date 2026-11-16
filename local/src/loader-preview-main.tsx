/* eslint-disable react-refresh/only-export-components -- a dev page, not part of the app */
import { useEffect, useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import '/src/index.css';
import '/src/components/__styles__/AppShell.css';
import StepsDisclosure from './components/StepsDisclosure';
import type { TraceStep } from './components/TraceSteps';

// DEV ONLY (open /loader-preview.html on the dev server). The REAL StepsDisclosure, driven by one simulated turn: nothing here is a copy of the loader, so what it shows is what the chat shows.
// Options in the address (all optional): ?theme=shadow (dark), ?long=1 (long step text), ?still=1 (reduced motion), ?pause=1 (hold the simulated turn), ?phase=N (start at that point: 0 is "nothing yet",
// 1 to 5 steps, 6 the finished turn), ?hold=1 (keep a landing or a merge on screen instead of letting it finish).
const SCRIPT: Array<{ title: string; detail: string; long: string }> = [
  { title: 'Evaluating request context...', detail: "Classifying workflow intent for: 'i just feel like a fraud.....'", long: "Classifying workflow intent for: 'i just feel like a fraud.....' and reading what the conversation so far says about how this should be answered" },
  { title: 'Writing the answer...', detail: 'Synthesizing final answer for i just feel like a fraud.....', long: 'Synthesizing final answer for i just feel like a fraud..... from the saved context, the voice examples and what was said earlier in this chat' },
  { title: 'Injected saved preferences/facts into context prompt.', detail: '', long: '' },
  { title: 'Found semantically relevant past context.', detail: '', long: '' },
  { title: 'Adjusting the reply to fit what you need', detail: 'The draft said the same reassurance as a recent reply, so it was written again', long: 'The draft said the same reassurance as a recent reply, so it was written again and the new draft was checked against the same rules' },
];
const PHASES = SCRIPT.length + 2;          // 0: nothing yet, 1..5: that many steps, 6: the turn finished, then round again
const PHASE_MS = 2600;

function stepsFor(phase: number, long: boolean): TraceStep[] {
  const count = Math.min(Math.max(phase, 0), SCRIPT.length);
  return SCRIPT.slice(0, count).map((s, i) => ({ id: `s${i}`, title: s.title, detail: long ? s.long : s.detail, status: i === count - 1 && phase <= SCRIPT.length ? 'active' : 'complete' }) as TraceStep);
}

const bar = { display: 'flex', flexWrap: 'wrap', gap: '8px 14px', alignItems: 'center', padding: '10px 12px', marginBottom: 14, border: '1px solid var(--border-color)', borderRadius: 10, background: 'var(--bg-alt, transparent)', font: '13px var(--font-sans, sans-serif)', color: 'var(--text-main)' } as const;
const button = (on: boolean) => ({ font: 'inherit', padding: '4px 10px', border: `1px solid ${on ? 'var(--accent-primary)' : 'var(--border-color)'}`, borderRadius: 6, background: 'var(--bg-main, #fff)', color: on ? 'var(--accent-primary)' : 'var(--text-main)', fontWeight: on ? 600 : 400, cursor: 'pointer' }) as const;

function Preview() {
  const params = new URLSearchParams(window.location.search);
  const [dark, setDark] = useState(params.get('theme') === 'shadow');
  const [long, setLong] = useState(params.get('long') === '1');
  const [still, setStill] = useState(params.get('still') === '1');
  const hold = params.get('hold') === '1';
  const [playing, setPlaying] = useState(params.get('pause') !== '1');
  const [phase, setPhase] = useState(Number(params.get('phase') ?? 0) || 0);

  useEffect(() => {
    if (!playing) return undefined;
    const timer = window.setInterval(() => setPhase(p => (p + 1) % PHASES), PHASE_MS);
    return () => window.clearInterval(timer);
  }, [playing]);
  useEffect(() => { document.body.style.background = dark ? '#1b0f0f' : '#f1f5f9'; }, [dark]);

  const steps = useMemo(() => stepsFor(phase, long), [phase, long]);
  const label = phase === 0 ? 'nothing yet' : phase > SCRIPT.length ? 'finished' : `step ${phase} of ${SCRIPT.length}`;
  return (
    <div className={dark ? 'theme-shadow' : 'theme-sonic'} style={{ minHeight: '100vh', padding: 16, boxSizing: 'border-box' }}>
      <div style={bar} role="toolbar" aria-label="Preview controls">
        <span>Theme</span><button style={button(!dark)} onClick={() => setDark(false)}>Light</button><button style={button(dark)} onClick={() => setDark(true)}>Dark</button>
        <label><input type="checkbox" checked={long} onChange={e => setLong(e.target.checked)} /> Long step text</label>
        <label><input type="checkbox" checked={still} onChange={e => setStill(e.target.checked)} /> Reduced motion</label>
        <button style={button(false)} onClick={() => setPlaying(p => !p)}>{playing ? 'Pause' : 'Play'}</button>
        <button style={button(false)} onClick={() => setPhase(p => (p + 1) % PHASES)}>Next step</button>
        <span style={{ font: '11px monospace', color: 'var(--text-muted)' }}>{label}</span>
      </div>
      <div style={{ maxWidth: 520, border: '1px solid var(--border-color)', borderRadius: 12, padding: '10px 14px 14px', background: 'var(--bg-main, #fff)', minHeight: 360 }}>
        <div style={{ font: '700 11px monospace', letterSpacing: '.08em', color: 'var(--text-muted)', margin: '6px 0 2px' }}>AI</div>
        <StepsDisclosure steps={steps} running={phase <= SCRIPT.length} idPrefix="preview" latest reducedMotion={still ? true : undefined} hold={hold} />
      </div>
    </div>
  );
}

createRoot(document.getElementById('root')!).render(<Preview />);

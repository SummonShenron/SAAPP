import { createRoot } from 'react-dom/client';
import '/src/index.css';
import MiniPatchy from './components/MiniPatchy';
function Box({ energy, working }: { energy: 'open' | 'easing' | 'subdued'; working?: boolean }) {
  return (
    <form className="chat-input-area" style={{ width: 120, display: 'inline-block', margin: '0 8px 40px 0' }}>
      <div className="chat-input-wrapper">
        <MiniPatchy energy={energy} working={working} />
        <textarea className="chat-textarea" placeholder={energy} rows={1} style={{ minHeight: 40 }} />
      </div>
    </form>
  );
}
createRoot(document.getElementById('root')!).render(
  <div><Box energy="open" /><Box energy="subdued" /><Box energy="subdued" working /></div>
);

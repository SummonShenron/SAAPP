/* eslint-disable react-refresh/only-export-components -- a dev page, not part of the app */
import { createRoot } from 'react-dom/client';
import '/src/index.css';
import HelixLogo from './components/HelixLogo';

// DEV ONLY (open /loader-preview.html on the dev server). The REAL ThinkingLogo at a large size, so the loader can be checked before it goes into the app.
const root = document.getElementById('root');
if (root) {
  createRoot(root).render(
    <div style={{ minHeight: '100vh', padding: 32, boxSizing: 'border-box', display: 'flex', alignItems: 'center', justifyContent: 'center', background: '#f1f5f9' }}>
      <HelixLogo size={200} />
    </div>,
  );
}

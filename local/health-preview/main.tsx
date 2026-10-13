import React from 'react';
import ReactDOM from 'react-dom/client';
import { HashRouter, Route, Routes } from 'react-router-dom';
import { SonicHealthPage } from '../src/pages/SonicHealth';
import '../src/index.css';
import { health, suggestions } from './mock';

// DEV ONLY (see health-preview.html). The guest identity needs no Clerk, so the page's own request code runs unchanged; only the network is faked.
localStorage.setItem('principal', 'guest');
const query = new URLSearchParams(window.location.search);
const respond = (body: unknown) => Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } }));
window.fetch = ((input: RequestInfo | URL) => {
  const url = String(input);
  if (url.includes('/api/admin/sonic-health')) return respond(query.get('empty') ? { ...health, usage: undefined, budget: undefined, observations: [] } : health);
  if (url.includes('/api/admin/ops-suggestions')) return respond(suggestions);
  return respond({});
}) as typeof window.fetch;

const dark = query.get('theme') === 'shadow';
ReactDOM.createRoot(document.getElementById('root')!).render(
  <div className={dark ? 'theme-shadow' : 'theme-sonic'} style={{ minHeight: '100vh', background: dark ? '#1b0f0f' : '#f1f5f9' }}>
    <HashRouter>
      <Routes>
        <Route path="/sonic-health/:tab?" element={<SonicHealthPage />} />
        <Route path="*" element={<SonicHealthPage />} />
      </Routes>
    </HashRouter>
  </div>,
);

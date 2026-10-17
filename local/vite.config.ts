import { defineConfig, loadEnv, type Plugin } from 'vite'
import react, { reactCompilerPreset } from '@vitejs/plugin-react'
import babel from '@rolldown/plugin-babel'

/**
 * Starts the two connections signing in needs, our server and Clerk, while the page's own JavaScript is still downloading. Without this each one only
 * begins (DNS lookup, then the secure handshake) after the page has run, which on a phone is a noticeable part of the "Signing you in" wait. Only added
 * to the built site: the development server has no use for it.
 */
function preconnectHints(env: Record<string, string>): Plugin {
  return {
    name: 'preconnect-hints',
    apply: 'build',
    transformIndexHtml() {
      const origins = new Set<string>()
      try {
        origins.add(new URL(env.VITE_API_BASE || 'https://saapp.onrender.com').origin)
      } catch { /* a bad value is left to the app to report */ }
      try {
        // A Clerk publishable key is "pk_test_" or "pk_live_" followed by the base64 of the Clerk host, ending in "$".
        const encoded = (env.VITE_CLERK_PUBLISHABLE_KEY || '').split('_').slice(2).join('_')
        const host = atob(encoded).replace(/\$$/, '')
        if (/^[a-z0-9.-]+\.[a-z]{2,}$/i.test(host)) origins.add(`https://${host}`)
      } catch { /* no usable key: only the server hint is added */ }
      return [...origins].map(href => ({
        tag: 'link',
        attrs: { rel: 'preconnect', href, crossorigin: '' },
        injectTo: 'head-prepend' as const,
      }))
    },
  }
}

export default defineConfig(({ mode }) => ({
  plugins: [
    react(),
    babel({ presets: [reactCompilerPreset()] }),
    preconnectHints(loadEnv(mode, process.cwd(), 'VITE_')),
  ],
  resolve: {
    dedupe: ['react', 'react-dom'],
  },
  build: {
    // Setting this to false bypasses the CSS minifier entirely
    cssMinify: false,
  },
  server: {
    proxy: {
      "/api": {
        target: "https://saapp.onrender.com/",
        changeOrigin: true,
        secure: false
      }
    }
  }
}))

import { defineConfig } from 'vite'
import react, { reactCompilerPreset } from '@vitejs/plugin-react'
import babel from '@rolldown/plugin-babel'

export default defineConfig({
  plugins: [
    react(),
    babel({ presets: [reactCompilerPreset()] })
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
})
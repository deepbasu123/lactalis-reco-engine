import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Frontend builds into ./dist, which the Express server serves in production.
// The dev proxy forwards /api to the local Node server on :8000.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
});

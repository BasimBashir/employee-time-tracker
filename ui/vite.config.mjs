import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'node:path';

export default defineConfig({
  plugins: [react()],
  root: __dirname,
  base: './',
  // Build the renderer as plain static files; the backend (Electron main) lives in
  // main.js and serves the UI from disk, so we never need a dev server in production.
  build: {
    outDir: path.resolve(__dirname, 'dist/renderer'),
    emptyOutDir: true,
    rollupOptions: { input: path.resolve(__dirname, 'index.html') },
  },
  server: {
    port: 5173,
    strictPort: true,
  },
});

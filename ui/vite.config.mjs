import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'node:path';

const dir = import.meta.dirname;

export default defineConfig({
  plugins: [react()],
  root: dir,
  base: './',
  // Build the renderer as plain static files; the backend (Electron main) lives in
  // main.js and serves the UI from disk, so we never need a dev server in production.
  build: {
    outDir: path.resolve(dir, 'dist/renderer'),
    emptyOutDir: true,
    rollupOptions: { input: path.resolve(dir, 'index.html') },
  },
  server: {
    port: 5173,
    strictPort: true,
  },
});

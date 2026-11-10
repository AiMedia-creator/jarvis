import { defineConfig } from 'vite';

// Kartenansicht: MapLibre GL JS (keine statischen Zusatzdateien nötig; Cesium wurde entfernt).
export default defineConfig({
  base: '/',
  build: { target:'es2022', sourcemap:true },
  worker: { format: 'es' },
  server: { host:'0.0.0.0', port:4173, proxy: { '/api': 'http://127.0.0.1:8080', '/ws': { target: 'ws://127.0.0.1:8080', ws: true }, '/health': 'http://127.0.0.1:8080' } }
});

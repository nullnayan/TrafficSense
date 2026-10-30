// @file: frontend/vite.config.ts
// @exports: Vite configuration
// @depends_on: vite, @vitejs/plugin-react
// @purpose: React development server with local backend proxy and production build.
import {defineConfig, loadEnv} from 'vite';
import react from '@vitejs/plugin-react';
export default defineConfig(({mode}) => {
  const target = loadEnv(mode, '.', 'TRAFFICSENSE_').TRAFFICSENSE_API_URL || 'http://127.0.0.1:8000';
  return {plugins: [react()], server: {proxy: {
    '/api': {target, changeOrigin: true}, '/health': {target, changeOrigin: true}
  }}};
});

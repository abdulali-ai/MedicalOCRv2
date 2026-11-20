import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

const API_TARGET = process.env.VITE_API_TARGET || 'http://127.0.0.1:5000';
const WS_TARGET = API_TARGET.replace(/^http/, 'ws');

export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 3000,
    proxy: {
      '/api': {
        target: API_TARGET,
        changeOrigin: true,
      },
      '/process-pdf': {
        target: API_TARGET,
        changeOrigin: true,
      },
      '/batch-process-pdf': {
        target: API_TARGET,
        changeOrigin: true,
      },
      '/ws': {
        target: WS_TARGET,
        ws: true,
      },
    },
  },
});

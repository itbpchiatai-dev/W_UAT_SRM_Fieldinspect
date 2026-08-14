import path from 'node:path';
import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), 'VITE_');
  return {
    plugins: [react()],
    resolve: { alias: { '@': path.resolve(__dirname, './src') } },
    server: {
      port: 5173,
      // strictPort: the whole stack pins the dev origin to :5173 (backend
      // API_CORS_ORIGINS, Azure redirect URI, AUTH_FRONTEND_BASE_URL). If
      // 5173 is busy, fail loudly here instead of silently drifting to 5174
      // and dying on a confusing CORS preflight 400.
      strictPort: true,
      proxy: { '/api': { target: env.VITE_API_BASE_URL || 'http://localhost:8000', changeOrigin: true } },
    },
  };
});

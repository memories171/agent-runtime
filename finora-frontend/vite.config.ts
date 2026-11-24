import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const projectRoot = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  const useApi = (env.VITE_DATA_MODE || 'mock').toLowerCase() === 'api';
  return {
    plugins: [react()],
    optimizeDeps: {
      include: ['oidc-client-ts'],
    },
    server: {
      host: '0.0.0.0',
      port: 5173,
      strictPort: true,
      https: {
        key: fs.readFileSync(path.resolve(projectRoot, '../certs/key.pem')),
        cert: fs.readFileSync(path.resolve(projectRoot, '../certs/cert.pem')),
      },
      ...(useApi ? {
        proxy: {
          '/api': {
            target: env.VITE_API_PROXY_TARGET || 'http://localhost:8000',
            changeOrigin: true,
            rewrite: (path: string) => path.replace(/^\/api/, ''),
          },
        },
      } : {}),
    },
  };
});

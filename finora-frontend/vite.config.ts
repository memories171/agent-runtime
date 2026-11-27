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
          // 后端认证/上下文/图路由真实路径自带 /api/v1，必须原样转发。
          '/api/v1': {
            target: env.VITE_API_PROXY_TARGET || 'http://localhost:8000',
            changeOrigin: true,
          },
          // 业务路由在后端没有 /api 前缀，由代理去掉挂载点。
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

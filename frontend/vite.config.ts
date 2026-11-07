import path from 'node:path';
import { defineConfig } from 'vitest/config';
import { loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

// The services don't allow cross-origin calls, so the browser talks to them through this proxy.
// The API key stays on the server: it comes from IFIC_API_KEY (the environment or the repo's .env)
// and is never sent to the browser, because only VITE_* variables reach client code.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, path.resolve(__dirname, '..'), 'IFIC_');
  const service = (name: string, target: string) => ({
    [`/api/${name}`]: {
      target,
      changeOrigin: true,
      rewrite: (p: string) => p.replace(`/api/${name}`, ''),
      headers: env.IFIC_API_KEY ? { 'X-API-Key': env.IFIC_API_KEY } : {},
    },
  });
  return {
    plugins: [react()],
    server: {
      port: 5173,
      proxy: {
        ...service('sync', env.IFIC_SYNC_URL ?? 'http://localhost:8001'),
        ...service('screening', env.IFIC_SCREENING_URL ?? 'http://localhost:8002'),
      },
    },
    test: { environment: 'node' },
  };
});

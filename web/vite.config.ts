// `defineConfig` comes from vitest, not vite, because the `test` block below is vitest's.
// Vite's own `defineConfig` does not know that key; it only appears to accept it when some
// other file in the project has imported vitest and pulled in its type augmentation. Test
// files do that locally — and `.dockerignore` excludes them, so the container build failed
// on a config that typechecks fine on a developer's machine. Importing it from here makes
// the type correct on its own terms rather than by accident.
import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import path from 'node:path';

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@': path.resolve(__dirname, './src') },
  },
  server: {
    port: 5173,
    // The API is same-origin in production; in development it is proxied so the browser
    // never has to care about CORS or a second base URL.
    proxy: {
      '/api': {
        target: process.env.CURVEVISION_API_URL ?? 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    target: 'es2022',
    sourcemap: true,
    rollupOptions: {
      output: {
        // The canvas engine and the React shell change at different rates; splitting them
        // keeps an editor tweak from invalidating the vendor bundle.
        manualChunks: {
          react: ['react', 'react-dom', 'react-router-dom'],
          query: ['@tanstack/react-query'],
        },
      },
    },
  },
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts'],
    benchmark: { include: ['src/**/*.bench.ts'] },
  },
});

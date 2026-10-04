import path from 'node:path';
import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { '@': path.resolve(__dirname, './src') } },
  server: {
    // Cloudflare quick tunnels get a random subdomain per run.
    allowedHosts: ['.trycloudflare.com'],
    proxy: { '/api': { target: process.env.MANDATE_API_ORIGIN ?? 'http://127.0.0.1:8000', changeOrigin: true, ws: true } },
  },
});

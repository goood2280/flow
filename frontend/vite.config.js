import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
export default defineConfig({
  plugins: [react()],
  build: { emptyOutDir: false },
  publicDir: false,
  server: { proxy: { '/api': 'http://localhost:8080', '/version.json': 'http://localhost:8080' } }
});

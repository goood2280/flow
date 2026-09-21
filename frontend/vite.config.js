import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  const target = env.FLOW_DEV_API_URL || 'http://127.0.0.1:8080';
  return {
  plugins: [react()],
  build: { emptyOutDir: true },
  // index.html 이 /favicon.svg 를 참조한다. publicDir 를 끄면 frontend/public 이
  // dist 로 복사되지 않아 모든 접속이 favicon 404 를 한 번씩 낸다 — 정상 배포인데도
  // 네트워크 탭이 "정적 자산이 막힌 것처럼" 보이는 원인이었다.
  publicDir: 'public',
  server: { proxy: { '/api': { target, changeOrigin: true }, '/version.json': { target, changeOrigin: true } } }
  };
});

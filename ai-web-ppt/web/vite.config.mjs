import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const webDir = path.dirname(fileURLToPath(import.meta.url))
const apiPort = process.env.PORT || '8787'

export default defineConfig({
  root: webDir,
  plugins: [react()],
  build: {
    outDir: path.join(webDir, 'dist'),
    emptyOutDir: true,
  },
  server: {
    // 显式绑 127.0.0.1：默认的 'localhost' 在本机会只解析到 ::1，
    // 于是 http://127.0.0.1:5173 打不开（排查时很费时间）。
    // 不要用 host: true —— 那会暴露到局域网，而 /api/generate 是无鉴权的。
    host: '127.0.0.1',
    port: 5173,
    // Vite/rolldown writes temporary bundle dirs (".<name>.<pid>.<uuid>.tmpdir")
    // and then watches them; on Windows that watch fails with EBUSY and takes the
    // dev server down. They are build artifacts, never sources — don't watch them.
    watch: {
      ignored: ['**/.*.tmpdir/**', '**/*.tmp', '**/.test-tmp/**', '**/dist/**'],
    },
    proxy: {
      '/api': {
        target: `http://localhost:${apiPort}`,
        changeOrigin: false,
      },
    },
  },
})

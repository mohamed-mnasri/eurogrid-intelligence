import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  // Pages are lazy-loaded; the one large chunk left is the ECharts library (~670 kB, ~220 kB gzipped),
  // shared by every chart page. Warn only if something grows beyond it.
  build: { chunkSizeWarningLimit: 700 },
})

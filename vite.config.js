import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // Proxy all API routes to the FastAPI backend
      '/livekit': 'http://localhost:8000',
      '/ivr': 'http://localhost:8000',
      '/tts': 'http://localhost:8000',
      '/routing': 'http://localhost:8000',
      '/cc': 'http://localhost:8000',
      '/ws': {
        target: 'http://localhost:8000',
        ws: true,
      },
    },
  },
})

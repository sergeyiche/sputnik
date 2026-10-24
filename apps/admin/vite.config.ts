import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  base: '/admin/',
  plugins: [vue()],
  server: {
    host: '0.0.0.0',
    port: 5174,
    allowedHosts: ['.spootnik.local'],
    proxy: {
      '/v1': {
        target: process.env.VITE_API_PROXY || 'http://localhost:8000',
        changeOrigin: false,
      },
    },
  },
})

import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'node:path'

export default defineConfig(({ command }) => {
  if (command === 'serve') {
    return {
      plugins: [vue()],
      server: {
        host: '0.0.0.0',
        port: 5173,
      },
    }
  }

  return {
    plugins: [vue()],
    define: {
      'process.env.NODE_ENV': JSON.stringify('production'),
    },
    build: {
      lib: {
        entry: resolve(__dirname, 'src/embed.ts'),
        name: 'ParkinsonChatWidget',
        formats: ['iife'],
        fileName: () => 'parkinson-chat-widget.js',
      },
      cssCodeSplit: false,
      rollupOptions: {
        output: {
          inlineDynamicImports: true,
          assetFileNames: 'parkinson-chat-widget.[ext]',
        },
      },
    },
  }
})

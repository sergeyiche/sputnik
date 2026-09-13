/**
 * Production embed entry — loads as a single IIFE script on any site.
 *
 * Usage:
 *   <link rel="stylesheet" href="/widget/parkinson-chat-widget.css" />
 *   <script src="/widget/parkinson-chat-widget.js" defer></script>
 *   <parkinson-chat-widget api-url="https://api.example.com"></parkinson-chat-widget>
 */
import { createApp, type App } from 'vue'
import FloatingChat from './components/FloatingChat.vue'

export interface MountOptions {
  apiUrl?: string
  title?: string
  placeholder?: string
}

function readAttrs(el: Element): MountOptions {
  return {
    apiUrl: el.getAttribute('api-url') || undefined,
    title: el.getAttribute('title') || undefined,
    placeholder: el.getAttribute('placeholder') || undefined,
  }
}

function mountWidget(el: Element, options?: MountOptions): App {
  const attrs = { ...readAttrs(el), ...options }
  const app = createApp(FloatingChat, attrs)
  app.mount(el)
  return app
}

function autoMount(): void {
  document.querySelectorAll('parkinson-chat-widget').forEach((el) => {
    if ((el as HTMLElement).dataset.mounted === '1') return
    mountWidget(el)
    ;(el as HTMLElement).dataset.mounted = '1'
  })
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', autoMount)
} else {
  autoMount()
}

declare global {
  interface Window {
    ParkinsonChatWidget?: {
      mount: typeof mountWidget
      autoMount: typeof autoMount
    }
  }
}

window.ParkinsonChatWidget = { mount: mountWidget, autoMount }

export { FloatingChat, mountWidget, autoMount }

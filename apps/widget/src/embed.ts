/**
 * Production embed entry — loads as a single IIFE script on any site.
 *
 * Usage:
 *   <link rel="stylesheet" href="https://mycompany.spootnik.ru/widget/sputnik.css" />
 *   <script src="https://mycompany.spootnik.ru/widget/sputnik.js" defer></script>
 *   <sputnik-widget></sputnik-widget>
 */
import { createApp, type App } from 'vue'
import FloatingChat from './components/FloatingChat.vue'

export interface MountOptions {
  apiUrl?: string
  title?: string
  placeholder?: string
}

function readAttrs(el: Element): MountOptions {
  const options: MountOptions = {}
  const apiUrl = el.getAttribute('api-url')
  const title = el.getAttribute('title')
  const placeholder = el.getAttribute('placeholder')
  if (apiUrl) options.apiUrl = apiUrl
  if (title) options.title = title
  if (placeholder) options.placeholder = placeholder
  return options
}

const scriptOrigin = (() => {
  const current = document.currentScript as HTMLScriptElement | null
  if (current?.src) return new URL(current.src).origin
  return ''
})()

function mountWidget(el: Element, options?: MountOptions): App {
  const attrs = { apiUrl: scriptOrigin || undefined, ...readAttrs(el), ...options }
  const app = createApp(FloatingChat, attrs)
  app.mount(el)
  return app
}

function autoMount(): void {
  document.querySelectorAll('sputnik-widget, parkinson-chat-widget').forEach((el) => {
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

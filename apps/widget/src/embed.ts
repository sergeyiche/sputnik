/**
 * Production embed entry — loads as a single IIFE script on any site.
 *
 * Usage:
 *   <link rel="stylesheet" href="https://mycompany.spootnik.ru/widget/sputnik.css" />
 *   <link rel="stylesheet" href="https://mycompany.spootnik.ru/v1/widget/theme.css" />
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

let mountedInstances = 0
let pendingOpen = false

/** Открыть окно виджета из внешней кнопки на сайте. */
function openWidget(): void {
  if (mountedInstances > 0) {
    window.dispatchEvent(new CustomEvent('sputnik-open'))
  } else {
    pendingOpen = true
  }
}

// Событие `sputnik-open` диспатчится и напрямую; пока виджет не смонтирован,
// запоминаем его и воспроизводим после mount, чтобы клик по внешней кнопке
// не терялся из-за порядка загрузки скриптов.
window.addEventListener('sputnik-open', () => {
  if (mountedInstances > 0) return
  pendingOpen = true
})

// Кнопка на сайте могла быть нажата ещё до загрузки самого скрипта виджета:
// страница выставляет этот флаг, embed проверяет его сразу после загрузки.
const openRequested = (
  window as unknown as { __sputnikOpenRequested?: boolean }
).__sputnikOpenRequested
if (openRequested) {
  pendingOpen = true
  ;(window as unknown as { __sputnikOpenRequested?: boolean }).__sputnikOpenRequested = false
}

function mountWidget(el: Element, options?: MountOptions): App {
  const attrs = { apiUrl: scriptOrigin || undefined, ...readAttrs(el), ...options }
  const app = createApp(FloatingChat, attrs)
  app.mount(el)
  mountedInstances += 1
  if (pendingOpen) {
    pendingOpen = false
    window.dispatchEvent(new CustomEvent('sputnik-open'))
  }
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
      open: typeof openWidget
    }
  }
}

window.ParkinsonChatWidget = { mount: mountWidget, autoMount, open: openWidget }

export { FloatingChat, mountWidget, autoMount, openWidget }

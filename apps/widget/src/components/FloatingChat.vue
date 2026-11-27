<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { useChat, type ChatMessage, type ChatSource } from '../composables/useChat'

const props = withDefaults(
  defineProps<{
    apiUrl?: string
    title?: string
    placeholder?: string
  }>(),
  {
    apiUrl: '',
    title: 'Спутник',
    placeholder: 'Задайте вопрос…',
  },
)

type WidgetPosition = 'bottom-right' | 'bottom-left' | 'top-right' | 'top-left'

const WIDGET_POSITIONS: readonly WidgetPosition[] = [
  'bottom-right',
  'bottom-left',
  'top-right',
  'top-left',
]

const position = ref<WidgetPosition>('bottom-right')

function isWidgetPosition(value: unknown): value is WidgetPosition {
  return typeof value === 'string' && (WIDGET_POSITIONS as readonly string[]).includes(value)
}

const disclaimer = ref(
  'Этот ассистент предоставляет общую справочную информацию и не заменяет консультацию специалиста.',
)
const brand = ref('#fc7f03')

function colorScheme(value: string): Record<string, string> {
  const match = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(value.trim())
  const fallback = '#fc7f03'
  const hex = match ? match[1] : fallback.slice(1)
  const full = hex.length === 3 ? hex.split('').map((char) => char + char).join('') : hex
  const rgb = [0, 2, 4].map((index) => Number.parseInt(full.slice(index, index + 2), 16))
  const mix = (toward: number, amount: number) =>
    rgb.map((channel) => Math.round(channel + (toward - channel) * amount))
  const hexOf = (channels: number[]) =>
    `#${channels.map((channel) => channel.toString(16).padStart(2, '0')).join('')}`
  const dark = mix(0, 0.12)
  const soft = mix(255, 0.9)
  return {
    '--pcw-brand': `#${full}`,
    '--pcw-brand-dark': hexOf(dark),
    '--pcw-brand-soft': hexOf(soft),
    '--pcw-brand-shadow': `rgba(${rgb[0]}, ${rgb[1]}, ${rgb[2]}, 0.45)`,
  }
}

const brandStyle = computed(() => colorScheme(brand.value))
const heading = ref(props.title)
const subtitle = ref('')
const legalNotes = ref('')
const greeting = ref('')
const legalOpen = ref(false)

const resolvedApiUrl =
  props.apiUrl ||
  (import.meta.env.DEV ? (import.meta.env.VITE_API_URL as string) || 'http://localhost:8000' : '')

const {
  messages,
  isOpen,
  isLoading,
  restoring,
  error,
  suggestions,
  sourceUrl,
  toggle,
  open,
  close,
  clearChat,
  sendMessage,
} = useChat({
  apiUrl: resolvedApiUrl,
})

onMounted(async () => {
  window.addEventListener('sputnik-open', () => open())
  if (!resolvedApiUrl) return
  try {
    const response = await fetch(`${resolvedApiUrl}/v1/widget/config`, { credentials: 'omit' })
    if (!response.ok) return
    const data = await response.json()
    if (data.title) heading.value = data.title
    if (typeof data.description === 'string') subtitle.value = data.description
    if (data.disclaimer) disclaimer.value = data.disclaimer
    if (typeof data.legal_notes === 'string') legalNotes.value = data.legal_notes
    if (typeof data.greeting === 'string') greeting.value = data.greeting
    if (data.primary_color) brand.value = data.primary_color
    if (isWidgetPosition(data.position)) {
      position.value = data.position
      placeInitially()
    }
  } catch {
    /* the widget still opens with the built-in title */
  }
})

const inputText = ref('')
const messagesEl = ref<HTMLElement | null>(null)

function onGlobalKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape' && legalOpen.value) {
    legalOpen.value = false
    return
  }
  if (e.key === 'Escape' && isOpen.value) {
    close()
  }
}

watch(
  isOpen,
  (open) => {
    if (open) {
      window.addEventListener('keydown', onGlobalKeydown)
    } else {
      window.removeEventListener('keydown', onGlobalKeydown)
    }
  },
)

function renderMarkdown(text: string): string {
  const html = marked.parse(text, { async: false }) as string
  return DOMPurify.sanitize(html)
}

const CITATION_RE = /\[(\d+)\](?!\()/g

function renderAnswer(msg: ChatMessage): string {
  const known = new Set((msg.sources ?? []).map((s) => s.index))
  const withCitations = msg.content.replace(CITATION_RE, (_, raw: string) => {
    const index = Number(raw)
    if (!known.has(index)) return ''
    return `<sup class="pcw-cite" data-cite="${index}" role="button" tabindex="0" title="Показать источник ${index}">${index}</sup>`
  })
  return renderMarkdown(withCitations)
}

const expandedSources = reactive<Record<string, boolean>>({})
const highlightedSource = ref<string | null>(null)
let highlightTimer: ReturnType<typeof setTimeout> | undefined

function sourceKey(msgId: string, index: number): string {
  return `${msgId}:${index}`
}

function toggleSources(msgId: string) {
  expandedSources[msgId] = !expandedSources[msgId]
}

async function revealSource(msgId: string, index: number) {
  expandedSources[msgId] = true
  highlightedSource.value = sourceKey(msgId, index)
  clearTimeout(highlightTimer)
  highlightTimer = setTimeout(() => (highlightedSource.value = null), 2000)
  await nextTick()
  messagesEl.value
    ?.querySelector(`[data-source-key="${sourceKey(msgId, index)}"]`)
    ?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
}

function onAnswerClick(msg: ChatMessage, event: Event) {
  const target = (event.target as HTMLElement | null)?.closest<HTMLElement>('[data-cite]')
  if (!target) return
  if (event instanceof KeyboardEvent && event.key !== 'Enter' && event.key !== ' ') return
  event.preventDefault()
  revealSource(msg.id, Number(target.dataset.cite))
}

const SIZE_UNITS = ['Б', 'КБ', 'МБ', 'ГБ']

function sourceMeta(source: ChatSource): string {
  const parts: string[] = []
  if (source.format) parts.push(source.format.toUpperCase())
  if (source.size_bytes) {
    let size = source.size_bytes
    let unit = 0
    while (size >= 1024 && unit < SIZE_UNITS.length - 1) {
      size /= 1024
      unit += 1
    }
    parts.push(`${size.toLocaleString('ru-RU', { maximumFractionDigits: unit ? 1 : 0 })} ${SIZE_UNITS[unit]}`)
  }
  return parts.join(' · ')
}

async function submit() {
  const text = inputText.value
  inputText.value = ''
  await sendMessage(text)
  await scrollToBottom()
}

async function askSuggestion(text: string) {
  inputText.value = ''
  await sendMessage(text)
  await scrollToBottom()
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    submit()
  }
}

async function scrollToBottom() {
  await nextTick()
  if (messagesEl.value) {
    messagesEl.value.scrollTop = messagesEl.value.scrollHeight
  }
}

watch(messages, scrollToBottom, { deep: true })

const hasMessages = computed(() => messages.value.length > 0)

const SCREEN_GAP = 16
const INITIAL_WIDTH = 400
const INITIAL_HEIGHT = 560
const MAX_WIDTH = 800
const MAX_HEIGHT = 600
const MIN_WIDTH = 280
const MIN_HEIGHT = 320

const RESIZE_EDGES = ['n', 'e', 's', 'w', 'ne', 'nw', 'se', 'sw'] as const
type ResizeEdge = (typeof RESIZE_EDGES)[number]

const panelWidth = ref(INITIAL_WIDTH)
const panelHeight = ref(INITIAL_HEIGHT)
const panelX = ref(SCREEN_GAP)
const panelY = ref(SCREEN_GAP)
const dragging = ref(false)
const viewportTick = ref(0)

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value))
}

function viewportLimits() {
  viewportTick.value
  const availableWidth = Math.max(160, window.innerWidth - SCREEN_GAP * 2)
  const availableHeight = Math.max(160, window.innerHeight - SCREEN_GAP * 2)
  const maxWidth = Math.min(MAX_WIDTH, availableWidth)
  const maxHeight = Math.min(MAX_HEIGHT, availableHeight)
  return {
    minWidth: Math.min(MIN_WIDTH, maxWidth),
    minHeight: Math.min(MIN_HEIGHT, maxHeight),
    maxWidth,
    maxHeight,
  }
}

function clampSize(width: number, height: number) {
  const limits = viewportLimits()
  return {
    width: Math.round(clamp(width, limits.minWidth, limits.maxWidth)),
    height: Math.round(clamp(height, limits.minHeight, limits.maxHeight)),
  }
}

function clampPosition(x: number, y: number, width: number, height: number) {
  const maxX = Math.max(SCREEN_GAP, window.innerWidth - width - SCREEN_GAP)
  const maxY = Math.max(SCREEN_GAP, window.innerHeight - height - SCREEN_GAP)
  return {
    x: Math.round(clamp(x, SCREEN_GAP, maxX)),
    y: Math.round(clamp(y, SCREEN_GAP, maxY)),
  }
}

function placeInitially() {
  const size = clampSize(INITIAL_WIDTH, INITIAL_HEIGHT)
  panelWidth.value = size.width
  panelHeight.value = size.height
  const offset = window.innerWidth <= 480 ? 12 : 24
  const atRight = window.innerWidth - offset - size.width
  const atBottom = window.innerHeight - offset - size.height
  const x = position.value.endsWith('left') ? offset : atRight
  const y = position.value.startsWith('top') ? offset : atBottom
  const pos = clampPosition(x, y, size.width, size.height)
  panelX.value = pos.x
  panelY.value = pos.y
}

const panelStyle = computed(() => ({
  left: `${panelX.value}px`,
  top: `${panelY.value}px`,
  width: `${panelWidth.value}px`,
  height: `${panelHeight.value}px`,
}))

const isMaximized = computed(() => {
  const { maxWidth, maxHeight } = viewportLimits()
  return panelWidth.value === maxWidth && panelHeight.value === maxHeight
})

const sizeToggleLabel = computed(() =>
  isMaximized.value ? 'Исходный размер' : 'На максимальный размер',
)

function applyPanelRect(width: number, height: number) {
  const size = clampSize(width, height)
  const pos = clampPosition(panelX.value, panelY.value, size.width, size.height)
  panelWidth.value = size.width
  panelHeight.value = size.height
  panelX.value = pos.x
  panelY.value = pos.y
}

function toggleMaxSize() {
  if (isMaximized.value) {
    placeInitially()
    return
  }
  const { maxWidth, maxHeight } = viewportLimits()
  applyPanelRect(maxWidth, maxHeight)
}

function onViewportResize() {
  viewportTick.value += 1
  applyPanelRect(panelWidth.value, panelHeight.value)
}

function edgeSides(edge: ResizeEdge) {
  return {
    north: edge.includes('n'),
    south: edge.includes('s'),
    east: edge.includes('e'),
    west: edge.includes('w'),
  }
}

function onHeaderPointerDown(event: PointerEvent) {
  if (event.button !== 0) return
  const target = event.target as HTMLElement | null
  if (target?.closest('button, a, input, textarea')) return
  const handle = event.currentTarget as HTMLElement
  const offsetX = event.clientX - panelX.value
  const offsetY = event.clientY - panelY.value
  dragging.value = true
  try {
    handle.setPointerCapture(event.pointerId)
  } catch {
    /* the pointer may already be gone */
  }
  const onMove = (ev: PointerEvent) => {
    const pos = clampPosition(
      ev.clientX - offsetX,
      ev.clientY - offsetY,
      panelWidth.value,
      panelHeight.value,
    )
    panelX.value = pos.x
    panelY.value = pos.y
  }
  const onUp = () => {
    dragging.value = false
    handle.removeEventListener('pointermove', onMove)
    handle.removeEventListener('pointerup', onUp)
    handle.removeEventListener('pointercancel', onUp)
  }
  handle.addEventListener('pointermove', onMove)
  handle.addEventListener('pointerup', onUp)
  handle.addEventListener('pointercancel', onUp)
}

function onResizePointerDown(event: PointerEvent, edge: ResizeEdge) {
  if (event.button !== 0) return
  event.stopPropagation()
  const handle = event.currentTarget as HTMLElement
  const origin = {
    x: event.clientX,
    y: event.clientY,
    left: panelX.value,
    top: panelY.value,
    right: panelX.value + panelWidth.value,
    bottom: panelY.value + panelHeight.value,
  }
  const sides = edgeSides(edge)
  dragging.value = true
  try {
    handle.setPointerCapture(event.pointerId)
  } catch {
    /* the pointer may already be gone */
  }
  const onMove = (ev: PointerEvent) => {
    const dx = ev.clientX - origin.x
    const dy = ev.clientY - origin.y
    const limits = viewportLimits()
    let left = sides.west ? origin.left + dx : origin.left
    let top = sides.north ? origin.top + dy : origin.top
    let right = sides.east ? origin.right + dx : origin.right
    let bottom = sides.south ? origin.bottom + dy : origin.bottom
    const viewRight = window.innerWidth - SCREEN_GAP
    const viewBottom = window.innerHeight - SCREEN_GAP
    left = clamp(left, SCREEN_GAP, viewRight)
    top = clamp(top, SCREEN_GAP, viewBottom)
    right = clamp(right, SCREEN_GAP, viewRight)
    bottom = clamp(bottom, SCREEN_GAP, viewBottom)

    let width = right - left
    let height = bottom - top
    if (width > limits.maxWidth) {
      width = limits.maxWidth
      if (sides.west) left = right - width
      else right = left + width
    } else if (width < limits.minWidth) {
      width = limits.minWidth
      if (sides.west) left = right - width
      else right = left + width
    }
    if (height > limits.maxHeight) {
      height = limits.maxHeight
      if (sides.north) top = bottom - height
      else bottom = top + height
    } else if (height < limits.minHeight) {
      height = limits.minHeight
      if (sides.north) top = bottom - height
      else bottom = top + height
    }
    if (left < SCREEN_GAP) left = SCREEN_GAP
    if (top < SCREEN_GAP) top = SCREEN_GAP
    if (left + width > viewRight) left = Math.max(SCREEN_GAP, viewRight - width)
    if (top + height > viewBottom) top = Math.max(SCREEN_GAP, viewBottom - height)

    panelX.value = Math.round(left)
    panelY.value = Math.round(top)
    panelWidth.value = Math.round(width)
    panelHeight.value = Math.round(height)
  }
  const onUp = () => {
    dragging.value = false
    handle.removeEventListener('pointermove', onMove)
    handle.removeEventListener('pointerup', onUp)
    handle.removeEventListener('pointercancel', onUp)
  }
  handle.addEventListener('pointermove', onMove)
  handle.addEventListener('pointerup', onUp)
  handle.addEventListener('pointercancel', onUp)
}

if (typeof window !== 'undefined') {
  placeInitially()
}

onMounted(() => {
  window.addEventListener('resize', onViewportResize)
})

onUnmounted(() => {
  window.removeEventListener('resize', onViewportResize)
  window.removeEventListener('keydown', onGlobalKeydown)
})
</script>

<template>
  <div class="pcw-root" :data-position="position" aria-live="polite" :style="brandStyle">
    <button
      v-if="!isOpen"
      class="pcw-launcher"
      type="button"
      aria-label="Открыть чат ассистента"
      @click="toggle"
    >
      <svg viewBox="0 0 24 24" width="28" height="28" fill="currentColor" aria-hidden="true">
        <path
          d="M20 2H4c-1.1 0-2 .9-2 2v18l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2zm0 14H5.2L4 17.2V4h16v12z"
        />
      </svg>
    </button>

    <div
      v-else
      class="pcw-panel"
      :class="{ 'is-dragging': dragging }"
      :style="panelStyle"
      role="dialog"
      aria-label="Чат ассистента"
    >
      <div
        v-for="edge in RESIZE_EDGES"
        :key="edge"
        class="pcw-resize"
        :class="`pcw-resize--${edge}`"
        @pointerdown="onResizePointerDown($event, edge)"
      />
      <div class="pcw-frame">
      <header class="pcw-header" @pointerdown="onHeaderPointerDown" @dragstart.prevent>
        <div class="pcw-heading">
          <h2 class="pcw-title">{{ heading }}</h2>
          <p v-if="subtitle" class="pcw-subtitle">{{ subtitle }}</p>
        </div>
        <div class="pcw-header-actions">
          <button
            v-if="hasMessages"
            class="pcw-icon-btn"
            type="button"
            title="Очистить диалог"
            @click="clearChat"
          >
            ↺
          </button>
          <button
            class="pcw-icon-btn"
            type="button"
            :title="sizeToggleLabel"
            :aria-label="sizeToggleLabel"
            :aria-pressed="isMaximized"
            @click="toggleMaxSize"
          >
            <svg v-if="isMaximized" viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
              <path
                fill="none"
                stroke="currentColor"
                stroke-width="2"
                stroke-linecap="round"
                stroke-linejoin="round"
                d="M9 9H4v11h11v-5M15 15h5V4H9v5"
              />
            </svg>
            <svg v-else viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
              <path
                fill="none"
                stroke="currentColor"
                stroke-width="2"
                stroke-linecap="round"
                stroke-linejoin="round"
                d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"
              />
            </svg>
          </button>
          <button class="pcw-icon-btn" type="button" title="Закрыть" aria-label="Закрыть" @click="toggle">
            <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
              <path
                fill="none"
                stroke="currentColor"
                stroke-width="2"
                stroke-linecap="round"
                d="M6 6l12 12M18 6L6 18"
              />
            </svg>
          </button>
        </div>
      </header>

      <div ref="messagesEl" class="pcw-messages">
        <div v-if="restoring && !hasMessages" class="pcw-empty">
          <p>Загрузка диалога…</p>
        </div>
        <div v-else-if="!hasMessages" class="pcw-empty">
          <p v-if="greeting">{{ greeting }}</p>
          <div v-if="suggestions.length" class="pcw-suggestions">
            <button
              v-for="item in suggestions"
              :key="item"
              type="button"
              class="pcw-suggestion"
              :disabled="isLoading"
              @click="askSuggestion(item)"
            >
              {{ item }}
            </button>
          </div>
        </div>

        <div
          v-for="msg in messages"
          :key="msg.id"
          class="pcw-message"
          :class="`pcw-message--${msg.role}`"
        >
          <div
            v-if="msg.role === 'assistant' && msg.streaming && !msg.content"
            class="pcw-bubble pcw-bubble--assistant pcw-typing"
            role="status"
            aria-label="Спутник печатает ответ"
          >
            <span class="pcw-typing__dot" />
            <span class="pcw-typing__dot" />
            <span class="pcw-typing__dot" />
          </div>
          <div v-else-if="msg.role === 'assistant'" class="pcw-bubble pcw-bubble--assistant">
            <div
              class="pcw-answer"
              @click="onAnswerClick(msg, $event)"
              @keydown="onAnswerClick(msg, $event)"
              v-html="renderAnswer(msg)"
            />
            <div v-if="msg.sources?.length" class="pcw-sources">
              <button
                type="button"
                class="pcw-sources__toggle"
                :aria-expanded="!!expandedSources[msg.id]"
                @click="toggleSources(msg.id)"
              >
                <span>Источники ({{ msg.sources.length }})</span>
                <span class="pcw-sources__action">
                  {{ expandedSources[msg.id] ? 'Свернуть' : 'Развернуть' }}
                  <span class="pcw-sources__chevron" :class="{ 'is-open': expandedSources[msg.id] }">▾</span>
                </span>
              </button>
              <ol v-if="expandedSources[msg.id]" class="pcw-sources__list">
                <li
                  v-for="source in msg.sources"
                  :key="source.index"
                  class="pcw-sources__item"
                  :class="{ 'is-highlighted': highlightedSource === sourceKey(msg.id, source.index) }"
                  :data-source-key="sourceKey(msg.id, source.index)"
                >
                  <span class="pcw-sources__num">{{ source.index }}</span>
                  <span class="pcw-sources__body">
                    <a
                      v-if="source.url"
                      :href="sourceUrl(source.url)"
                      target="_blank"
                      rel="noopener noreferrer"
                      class="pcw-sources__link"
                    >{{ source.title }}</a>
                    <span v-else class="pcw-sources__title">{{ source.title }}</span>
                    <span v-if="sourceMeta(source)" class="pcw-sources__meta">{{ sourceMeta(source) }}</span>
                  </span>
                </li>
              </ol>
            </div>
          </div>
          <div v-else class="pcw-bubble pcw-bubble--user">
            {{ msg.content }}
          </div>
        </div>

        <div
          v-if="hasMessages && suggestions.length && !isLoading"
          class="pcw-suggestions pcw-suggestions--followup"
        >
          <button
            v-for="item in suggestions"
            :key="item"
            type="button"
            class="pcw-suggestion"
            :disabled="isLoading"
            @click="askSuggestion(item)"
          >
            {{ item }}
          </button>
        </div>

        <p v-if="error" class="pcw-error">{{ error }}</p>
      </div>

      <footer class="pcw-input-area">
        <div v-if="disclaimer || legalNotes" class="pcw-legal">
          <p v-if="disclaimer" class="pcw-disclaimer">{{ disclaimer }}</p>
          <button
            class="pcw-legal-help"
            type="button"
            aria-label="Подробная юридическая информация"
            @click="legalOpen = true"
          >
            ?
          </button>
        </div>
        <div v-if="legalOpen" class="pcw-legal-layer" @click.self="legalOpen = false">
          <div class="pcw-legal-card" role="dialog" aria-label="Подробная юридическая информация">
            <button class="pcw-legal-close" type="button" aria-label="Закрыть" @click="legalOpen = false">✕</button>
            <p class="pcw-legal-card__text">{{ legalNotes || 'Подробная информация пока не добавлена.' }}</p>
          </div>
        </div>
        <textarea
          v-model="inputText"
          class="pcw-input"
          :placeholder="placeholder"
          rows="2"
          :disabled="isLoading"
          @keydown="onKeydown"
        />
        <button
          class="pcw-send"
          type="button"
          :disabled="isLoading || !inputText.trim()"
          @click="submit"
        >
          <span v-if="isLoading" class="pcw-spinner" aria-label="Отправка" />
          <template v-else>Отправить</template>
        </button>
      </footer>
      </div>
    </div>
  </div>
</template>

<style scoped>
.pcw-root {
  --pcw-brand: #fc7f03;
  --pcw-brand-dark: #e06e00;
  --pcw-brand-soft: #fff4e8;
  --pcw-brand-shadow: rgba(252, 127, 3, 0.45);

  position: fixed;
  bottom: 24px;
  right: 24px;
  top: auto;
  left: auto;
  z-index: 9999;
  font-family: system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif;
  font-size: 16px;
  line-height: 1.5;
  color: #1e293b;
}

/* Положение иконки выбирается в кабинете (вкладка «Виджет») и приходит
   в /v1/widget/config; data-position дублирует серверный theme.css. */
.pcw-root[data-position='top-right'] {
  top: 24px;
  right: 24px;
  bottom: auto;
  left: auto;
}

.pcw-root[data-position='bottom-left'] {
  bottom: 24px;
  left: 24px;
  top: auto;
  right: auto;
}

.pcw-root[data-position='top-left'] {
  top: 24px;
  left: 24px;
  bottom: auto;
  right: auto;
}

/* Защита от глобальных стилей сайта клиента: темы WordPress и сборки часто
   задают всем <button> большие padding, uppercase, серый фон и overflow.
   Сбрасываем их для кнопок виджета; конкретные классы ниже задают свой вид. */
.pcw-launcher,
.pcw-icon-btn,
.pcw-send,
.pcw-suggestion,
.pcw-sources__toggle,
.pcw-legal-help,
.pcw-legal-close {
  font-family: inherit;
  font-size: inherit;
  font-weight: inherit;
  letter-spacing: normal;
  text-transform: none;
  line-height: 1.4;
  padding: 0;
  margin: 0;
  border: 0;
  border-radius: 0;
  background: none;
  box-shadow: none;
  -webkit-appearance: none;
  appearance: none;
  box-sizing: border-box;
  min-width: 0;
  max-width: none;
  overflow: visible;
  vertical-align: baseline;
}

.pcw-launcher {
  width: 60px;
  height: 60px;
  border-radius: 50%;
  border: none;
  background: var(--pcw-brand);
  color: #fff;
  cursor: pointer;
  box-shadow: 0 4px 20px var(--pcw-brand-shadow);
  display: flex;
  align-items: center;
  justify-content: center;
  transition: transform 0.15s ease;
}

.pcw-launcher:hover {
  transform: scale(1.05);
  background-color: var(--pcw-brand);
}

.pcw-panel {
  position: fixed;
  z-index: 9999;
  padding: 0;
}

.pcw-frame {
  position: relative;
  width: 100%;
  height: 100%;
  background: var(--pcw-brand);
  border-radius: 16px;
  box-shadow: 0 8px 40px rgba(0, 0, 0, 0.18);
  display: flex;
  flex-direction: column;
  align-items: stretch;
  overflow: hidden;
}

.pcw-resize {
  position: absolute;
  z-index: 4;
  touch-action: none;
}

.pcw-resize--n,
.pcw-resize--s {
  left: 14px;
  right: 14px;
  height: 8px;
  cursor: ns-resize;
}

.pcw-resize--n {
  top: -4px;
}

.pcw-resize--s {
  bottom: -4px;
}

.pcw-resize--e,
.pcw-resize--w {
  top: 14px;
  bottom: 14px;
  width: 8px;
  cursor: ew-resize;
}

.pcw-resize--e {
  right: -4px;
}

.pcw-resize--w {
  left: -4px;
}

.pcw-resize--ne,
.pcw-resize--nw,
.pcw-resize--se,
.pcw-resize--sw {
  width: 14px;
  height: 14px;
}

.pcw-resize--ne {
  top: -4px;
  right: -4px;
  cursor: nesw-resize;
}

.pcw-resize--nw {
  top: -4px;
  left: -4px;
  cursor: nwse-resize;
}

.pcw-resize--se {
  right: -4px;
  bottom: -4px;
  cursor: nwse-resize;
}

.pcw-resize--sw {
  left: -4px;
  bottom: -4px;
  cursor: nesw-resize;
}

.pcw-resize--se::after {
  content: '';
  position: absolute;
  right: 5px;
  bottom: 5px;
  width: 7px;
  height: 7px;
  border-right: 2px solid #94a3b8;
  border-bottom: 2px solid #94a3b8;
  pointer-events: none;
}

.pcw-header {
  align-self: stretch;
  box-sizing: border-box;
  width: 100%;
  margin: 0;
  align-items: center;
  background: var(--pcw-brand);
  color: #fff;
  padding: 14px 16px;
  display: flex;
  justify-content: space-between;
  gap: 8px;
  flex-shrink: 0;
  border-radius: 0;
  cursor: grab;
  touch-action: none;
  user-select: none;
}

.pcw-panel.is-dragging .pcw-header {
  cursor: grabbing;
}

.pcw-panel.is-dragging .pcw-frame {
  user-select: none;
}

.pcw-heading {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
  flex: 1;
}

.pcw-title {
  margin: 0;
  flex: 0 0 auto;
  /* px вместо rem: на сайтах с html { font-size: 62.5% } rem меньше задуманного */
  font-family: inherit;
  font-size: 17px;
  font-weight: 600;
  letter-spacing: normal;
  line-height: 1.25;
  color: inherit;
}

.pcw-subtitle {
  margin: 0;
  flex: 1 1 auto;
  min-width: 0;
  font-family: inherit;
  font-size: 13px;
  font-weight: 400;
  letter-spacing: normal;
  line-height: 1.35;
  opacity: 0.92;
}

.pcw-legal {
  width: 100%;
  display: flex;
  align-items: flex-start;
  gap: 8px;
}

.pcw-disclaimer {
  flex: 1;
  margin: 0;
  font-family: inherit;
  font-size: 12px;
  line-height: 1.35;
  color: #64748b;
}

.pcw-legal-help {
  flex-shrink: 0;
  width: 18px;
  height: 18px;
  margin-top: 1px;
  border: 1px solid #94a3b8;
  border-radius: 50%;
  background: transparent;
  color: #64748b;
  font: 12px/16px system-ui, sans-serif;
  cursor: pointer;
  padding: 0;
}

.pcw-legal-help:hover {
  border-color: #475569;
  color: #334155;
}

.pcw-legal-layer {
  position: absolute;
  inset: 0;
  z-index: 2;
  display: flex;
  align-items: flex-end;
  padding: 12px;
  background: rgba(15, 23, 42, 0.35);
}

.pcw-legal-card {
  position: relative;
  width: 100%;
  max-height: 70%;
  overflow: auto;
  background: #fff;
  border-radius: 12px;
  padding: 12px 36px 14px 14px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.16);
}

.pcw-legal-close {
  position: absolute;
  top: 8px;
  right: 8px;
  border: 0;
  background: transparent;
  color: #64748b;
  font-size: 16px;
  line-height: 1;
  cursor: pointer;
}

.pcw-legal-card__text {
  margin: 0;
  white-space: pre-wrap;
  font-family: inherit;
  font-size: 14px;
  line-height: 1.45;
  color: #1e293b;
}

.pcw-header-actions {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
}

.pcw-icon-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: rgba(255, 255, 255, 0.2);
  border: none;
  color: #fff;
  width: 32px;
  height: 32px;
  padding: 0;
  border-radius: 8px;
  cursor: pointer;
  line-height: 0;
}

.pcw-icon-btn:hover {
  background: rgba(255, 255, 255, 0.35);
}

.pcw-messages {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
  background: #f8fafc;
}

.pcw-empty {
  color: #64748b;
  font-family: inherit;
  font-size: 15px;
}

.pcw-empty p {
  margin: 0;
}

.pcw-suggestions--followup {
  margin: 4px 0 8px;
  padding: 0 4px;
}

.pcw-suggestions {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 12px;
}

.pcw-suggestion {
  text-align: left;
  background: #fff;
  border: 1px solid #cbd5e1;
  border-radius: 10px;
  padding: 10px 12px;
  cursor: pointer;
  font: inherit;
  color: var(--pcw-brand-dark);
}

.pcw-suggestion:hover:not(:disabled) {
  border-color: var(--pcw-brand);
  background: var(--pcw-brand-soft);
}

.pcw-suggestion:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.pcw-message {
  margin-bottom: 12px;
  display: flex;
}

.pcw-message--user {
  justify-content: flex-end;
}

.pcw-bubble {
  padding: 10px 14px;
  border-radius: 12px;
  max-width: 90%;
  word-break: break-word;
}

.pcw-bubble--user {
  background: var(--pcw-brand);
  color: #fff;
  border-bottom-right-radius: 4px;
}

.pcw-bubble--assistant {
  background: #fff;
  color: #1e293b;
  border: 1px solid #e2e8f0;
  border-bottom-left-radius: 4px;
  font-family: inherit;
  font-size: 16px;
}

.pcw-bubble--assistant :deep(p) {
  margin: 0 0 0.5em;
}

.pcw-bubble--assistant :deep(p:last-child) {
  margin-bottom: 0;
}

/* Медицинский дисклеймер после --- в ответе */
.pcw-bubble--assistant :deep(hr) {
  border: none;
  border-top: 1px solid #e2e8f0;
  margin: 0.75em 0 0.5em;
}

.pcw-bubble--assistant :deep(hr ~ *) {
  font-size: 12px;
  line-height: 1.4;
  color: #94a3b8;
}

/* Защита содержимого ответа от типографики сайта клиента:
   темы WordPress задают h1-h6, a, ul/ol, blockquote и т.п. глобально. */
.pcw-answer :deep(h1),
.pcw-answer :deep(h2),
.pcw-answer :deep(h3),
.pcw-answer :deep(h4),
.pcw-answer :deep(h5),
.pcw-answer :deep(h6) {
  font-family: inherit;
  font-size: 1em;
  font-weight: 600;
  letter-spacing: normal;
  line-height: 1.35;
  color: inherit;
  margin: 0.8em 0 0.4em;
}

.pcw-answer :deep(a) {
  display: inline;
  color: var(--pcw-brand-dark);
  text-decoration: underline;
  text-underline-offset: 2px;
}

.pcw-answer :deep(a:hover) {
  color: var(--pcw-brand);
}

.pcw-answer :deep(ul),
.pcw-answer :deep(ol) {
  margin: 0.5em 0;
  padding-left: 1.4em;
}

.pcw-answer :deep(ul) {
  list-style: disc;
}

.pcw-answer :deep(ol) {
  list-style: decimal;
}

.pcw-answer :deep(li) {
  margin: 0.25em 0;
  padding: 0;
}

.pcw-answer :deep(li > ul),
.pcw-answer :deep(li > ol) {
  margin: 0;
}

.pcw-answer :deep(blockquote) {
  margin: 0.6em 0;
  padding: 0.2em 1em;
  border-left: 3px solid var(--pcw-brand);
  color: inherit;
}

.pcw-answer :deep(code) {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 0.9em;
  background: var(--pcw-brand-soft);
  padding: 0.1em 0.3em;
  border-radius: 4px;
}

.pcw-answer :deep(pre) {
  margin: 0.6em 0;
  white-space: pre-wrap;
  background: #f1f5f9;
  padding: 10px 12px;
  border-radius: 8px;
}

.pcw-answer :deep(table) {
  border-collapse: collapse;
  margin: 0.6em 0;
}

.pcw-answer :deep(th),
.pcw-answer :deep(td) {
  border: 1px solid #e2e8f0;
  padding: 6px 10px;
}

.pcw-answer :deep(.pcw-cite) {
  display: inline-block;
  min-width: 1.35em;
  margin: 0 1px;
  padding: 0 4px;
  border-radius: 6px;
  background: var(--pcw-brand-soft);
  color: var(--pcw-brand-dark);
  font-size: 0.7em;
  font-weight: 600;
  line-height: 1.5;
  text-align: center;
  vertical-align: super;
  cursor: pointer;
}

.pcw-answer :deep(.pcw-cite:hover),
.pcw-answer :deep(.pcw-cite:focus-visible) {
  background: var(--pcw-brand);
  color: #fff;
  outline: none;
}

.pcw-sources {
  margin-top: 10px;
  border-top: 1px solid #e2e8f0;
  padding-top: 6px;
}

.pcw-sources__toggle {
  display: flex;
  width: 100%;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 4px 0;
  background: none;
  border: none;
  color: #64748b;
  font: inherit;
  font-size: 13px;
  cursor: pointer;
}

.pcw-sources__action {
  color: var(--pcw-brand-dark);
  font-weight: 600;
}

.pcw-sources__chevron {
  display: inline-block;
  transition: transform 0.2s ease;
}

.pcw-sources__chevron.is-open {
  transform: rotate(180deg);
}

.pcw-sources__list {
  list-style: none;
  margin: 6px 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.pcw-sources__item {
  display: flex;
  gap: 8px;
  align-items: flex-start;
  padding: 6px 8px;
  border-radius: 8px;
  font-size: 13px;
  line-height: 1.35;
  transition: background-color 0.3s ease;
}

.pcw-sources__item.is-highlighted {
  background: var(--pcw-brand-soft);
}

.pcw-sources__num {
  flex: none;
  min-width: 1.5em;
  padding: 0 4px;
  border-radius: 6px;
  background: var(--pcw-brand-soft);
  color: var(--pcw-brand-dark);
  font-weight: 600;
  text-align: center;
}

.pcw-sources__body {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.pcw-sources__link {
  color: #1e293b;
  text-decoration: underline;
  text-decoration-color: var(--pcw-brand);
  text-underline-offset: 2px;
}

.pcw-sources__link:hover {
  color: var(--pcw-brand-dark);
}

.pcw-sources__meta {
  color: #94a3b8;
  font-size: 12px;
}

.pcw-error {
  color: #b91c1c;
  font-size: 14px;
  margin: 8px 0 0;
}

.pcw-input-area {
  padding: 12px;
  border-top: 1px solid #e2e8f0;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  background: #fff;
}

.pcw-input {
  flex: 1;
  border: 1px solid #cbd5e1;
  border-radius: 10px;
  padding: 10px 12px;
  resize: none;
  font-family: inherit;
  font-size: 16px;
  line-height: 1.4;
  color: #1e293b;
  background: #fff;
  overflow: auto;
}

.pcw-input:focus {
  outline: 2px solid var(--pcw-brand);
  outline-offset: 0;
}

.pcw-send {
  background: var(--pcw-brand);
  color: #fff;
  border: none;
  border-radius: 10px;
  padding: 0 16px;
  font-weight: 600;
  cursor: pointer;
  white-space: nowrap;
}

.pcw-send:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.pcw-typing {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 14px 16px;
}

.pcw-typing__dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--pcw-brand);
  opacity: 0.4;
  animation: pcw-typing-bounce 1.2s infinite ease-in-out;
}

.pcw-typing__dot:nth-child(2) {
  animation-delay: 0.15s;
}

.pcw-typing__dot:nth-child(3) {
  animation-delay: 0.3s;
}

@keyframes pcw-typing-bounce {
  0%,
  60%,
  100% {
    transform: translateY(0);
    opacity: 0.4;
  }
  30% {
    transform: translateY(-5px);
    opacity: 1;
  }
}

.pcw-spinner {
  display: inline-block;
  width: 16px;
  height: 16px;
  border: 2px solid rgba(255, 255, 255, 0.4);
  border-top-color: #fff;
  border-radius: 50%;
  vertical-align: middle;
  animation: pcw-spin 0.8s linear infinite;
}

@keyframes pcw-spin {
  to {
    transform: rotate(360deg);
  }
}

@media (prefers-reduced-motion: reduce) {
  .pcw-typing__dot,
  .pcw-spinner {
    animation-duration: 2.4s;
  }
}

@media (max-width: 480px) {
  .pcw-root {
    bottom: 12px;
    right: 12px;
    top: auto;
    left: auto;
  }

  .pcw-root[data-position='top-right'] {
    top: 12px;
    right: 12px;
    bottom: auto;
    left: auto;
  }

  .pcw-root[data-position='bottom-left'] {
    bottom: 12px;
    left: 12px;
    top: auto;
    right: auto;
  }

  .pcw-root[data-position='top-left'] {
    top: 12px;
    left: 12px;
    bottom: auto;
    right: auto;
  }
}
</style>

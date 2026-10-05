<script setup lang="ts">
import { computed, nextTick, reactive, ref, watch } from 'vue'
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
    title: 'Ассистент по болезни Паркинсона',
    placeholder: 'Задайте вопрос о болезни Паркинсона…',
  },
)

const DISCLAIMER =
  'Информация носит справочный характер и не заменяет консультацию врача.'

const FALLBACK_SUGGESTIONS = [
  'Что такое болезнь Паркинсона?',
  'Какие бывают симптомы на ранних стадиях?',
  'Как правильно принимать лекарства?',
]

const resolvedApiUrl =
  props.apiUrl ||
  (import.meta.env.DEV ? (import.meta.env.VITE_API_URL as string) || 'http://localhost:8000' : '')

const {
  messages,
  isOpen,
  isLoading,
  error,
  suggestions,
  sourceUrl,
  toggle,
  close,
  clearChat,
  sendMessage,
} = useChat({
  apiUrl: resolvedApiUrl,
  fallbackSuggestions: FALLBACK_SUGGESTIONS,
})

const inputText = ref('')
const messagesEl = ref<HTMLElement | null>(null)

function onGlobalKeydown(e: KeyboardEvent) {
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
</script>

<template>
  <div class="pcw-root" aria-live="polite">
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

    <div v-else class="pcw-panel" role="dialog" aria-label="Чат ассистента">
      <header class="pcw-header">
        <div>
          <h2 class="pcw-title">{{ title }}</h2>
          <p class="pcw-disclaimer">{{ DISCLAIMER }}</p>
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
          <button class="pcw-icon-btn" type="button" title="Закрыть" @click="toggle">✕</button>
        </div>
      </header>

      <div ref="messagesEl" class="pcw-messages">
        <div v-if="!hasMessages" class="pcw-empty">
          <p>
            Здравствуйте! Я помогу найти информацию о болезни Паркинсона на основе проверенных
            материалов.
          </p>
          <div class="pcw-suggestions">
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

        <p v-if="error" class="pcw-error">{{ error }}</p>
      </div>

      <footer class="pcw-input-area">
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
  z-index: 9999;
  font-family: system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif;
  font-size: 16px;
  line-height: 1.5;
  color: #1e293b;
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
}

.pcw-panel {
  width: min(400px, calc(100vw - 32px));
  height: min(560px, calc(100vh - 48px));
  background: #fff;
  border-radius: 16px;
  box-shadow: 0 8px 40px rgba(0, 0, 0, 0.18);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.pcw-header {
  background: var(--pcw-brand);
  color: #fff;
  padding: 14px 16px;
  display: flex;
  justify-content: space-between;
  gap: 8px;
}

.pcw-title {
  margin: 0;
  font-size: 1.05rem;
  font-weight: 600;
}

.pcw-disclaimer {
  margin: 4px 0 0;
  font-size: 0.72rem;
  opacity: 0.75;
  line-height: 1.35;
}

.pcw-header-actions {
  display: flex;
  gap: 4px;
  flex-shrink: 0;
}

.pcw-icon-btn {
  background: rgba(255, 255, 255, 0.2);
  border: none;
  color: #fff;
  width: 32px;
  height: 32px;
  border-radius: 8px;
  cursor: pointer;
  font-size: 1rem;
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
  font-size: 0.95rem;
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
  font-size: 0.75rem;
  line-height: 1.4;
  color: #94a3b8;
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
  font-size: 0.8rem;
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
  font-size: 0.8rem;
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
  font-size: 0.72rem;
}

.pcw-error {
  color: #b91c1c;
  font-size: 0.85rem;
  margin: 8px 0 0;
}

.pcw-input-area {
  padding: 12px;
  border-top: 1px solid #e2e8f0;
  display: flex;
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
  font-size: 1rem;
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
    left: 12px;
  }

  .pcw-panel {
    width: 100%;
  }
}
</style>

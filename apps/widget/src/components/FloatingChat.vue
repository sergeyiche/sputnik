<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { useChat } from '../composables/useChat'

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

const SUGGESTIONS = [
  'Что такое болезнь Паркинсона?',
  'Какие бывают симптомы на ранних стадиях?',
  'Как правильно принимать лекарства?',
]

const resolvedApiUrl =
  props.apiUrl ||
  (import.meta.env.DEV ? (import.meta.env.VITE_API_URL as string) || 'http://localhost:8000' : '')

const { messages, isOpen, isLoading, error, toggle, close, clearChat, sendMessage } = useChat({
  apiUrl: resolvedApiUrl,
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
              v-for="item in SUGGESTIONS"
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
            v-if="msg.role === 'assistant'"
            class="pcw-bubble pcw-bubble--assistant"
            v-html="renderMarkdown(msg.content || (msg.streaming ? '…' : ''))"
          />
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
          {{ isLoading ? '…' : 'Отправить' }}
        </button>
      </footer>
    </div>
  </div>
</template>

<style scoped>
.pcw-root {
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
  background: #2563eb;
  color: #fff;
  cursor: pointer;
  box-shadow: 0 4px 20px rgba(37, 99, 235, 0.45);
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
  background: #1e40af;
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
  font-size: 0.8rem;
  opacity: 0.9;
  line-height: 1.35;
}

.pcw-header-actions {
  display: flex;
  gap: 4px;
  flex-shrink: 0;
}

.pcw-icon-btn {
  background: rgba(255, 255, 255, 0.15);
  border: none;
  color: #fff;
  width: 32px;
  height: 32px;
  border-radius: 8px;
  cursor: pointer;
  font-size: 1rem;
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
  color: #1e40af;
}

.pcw-suggestion:hover:not(:disabled) {
  border-color: #2563eb;
  background: #eff6ff;
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
  background: #2563eb;
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
  outline: 2px solid #2563eb;
  outline-offset: 0;
}

.pcw-send {
  background: #2563eb;
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

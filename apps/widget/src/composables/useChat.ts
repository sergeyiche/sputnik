import { ref } from 'vue'

export interface ChatSource {
  index: number
  title: string
  url: string | null
  format?: string | null
  size_bytes?: number | null
}

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  streaming?: boolean
  sources?: ChatSource[]
}

export interface UseChatOptions {
  apiUrl?: string
  fallbackSuggestions?: string[]
  suggestionCount?: number
}

function generateId(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`
}

function resolveApiUrl(apiUrl?: string): string {
  const raw = (apiUrl ?? '').trim()
  if (!raw) {
    return window.location.origin
  }
  return raw.replace(/\/$/, '')
}

export function useChat(options: UseChatOptions) {
  const messages = ref<ChatMessage[]>([])
  const isOpen = ref(false)
  const isLoading = ref(false)
  const error = ref<string | null>(null)
  const suggestions = ref<string[]>([])
  const fallbackSuggestions = options.fallbackSuggestions ?? []
  const suggestionCount = options.suggestionCount ?? 3

  function apiBase(): string {
    return resolveApiUrl(options.apiUrl)
  }

  function sourceUrl(path: string): string {
    return /^https?:\/\//i.test(path) ? path : `${apiBase()}${path}`
  }

  async function loadStarterSuggestions() {
    try {
      const response = await fetch(
        `${apiBase()}/v1/suggestions/starter?count=${suggestionCount}`,
        { credentials: 'include' },
      )
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      const data = await response.json()
      const items = Array.isArray(data?.suggestions) ? data.suggestions : []
      suggestions.value = items.length ? items : fallbackSuggestions
    } catch {
      suggestions.value = fallbackSuggestions
    }
  }

  function toggle() {
    isOpen.value = !isOpen.value
    if (isOpen.value && !messages.value.length && !suggestions.value.length) {
      loadStarterSuggestions()
    }
  }

  function open() {
    isOpen.value = true
    if (!messages.value.length && !suggestions.value.length) {
      loadStarterSuggestions()
    }
  }

  function close() {
    isOpen.value = false
  }

  function clearChat() {
    messages.value = []
    error.value = null
    fetch(`${apiBase()}/v1/session`, {
      method: 'DELETE',
      credentials: 'include',
    }).catch(() => {})
    loadStarterSuggestions()
  }

  async function sendMessage(text: string) {
    const trimmed = text.trim()
    if (!trimmed || isLoading.value) return

    error.value = null
    isLoading.value = true

    const userMsg: ChatMessage = {
      id: generateId(),
      role: 'user',
      content: trimmed,
    }
    messages.value.push(userMsg)

    const assistantMsg: ChatMessage = {
      id: generateId(),
      role: 'assistant',
      content: '',
      streaming: true,
    }
    messages.value.push(assistantMsg)

    try {
      // stream:false — надёжнее для встраивания; ответ целиком как JSON
      const response = await fetch(`${apiBase()}/v1/chat`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: trimmed, stream: false }),
      })

      if (!response.ok) {
        let detail = `Ошибка сервера: ${response.status}`
        try {
          const errBody = await response.json()
          if (errBody?.detail) detail = String(errBody.detail)
        } catch {
          /* ignore */
        }
        throw new Error(detail)
      }

      const data = await response.json()
      assistantMsg.content = data.answer || 'Пустой ответ от сервера.'
      assistantMsg.sources = Array.isArray(data.sources) ? data.sources : []
      assistantMsg.streaming = false
    } catch (e) {
      assistantMsg.content =
        'Не удалось получить ответ. Проверьте подключение к API и попробуйте снова.'
      assistantMsg.streaming = false
      error.value = e instanceof Error ? e.message : 'Unknown error'
    } finally {
      isLoading.value = false
    }
  }

  return {
    messages,
    isOpen,
    isLoading,
    error,
    suggestions,
    sourceUrl,
    toggle,
    open,
    close,
    clearChat,
    sendMessage,
  }
}

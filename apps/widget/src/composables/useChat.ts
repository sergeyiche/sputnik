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

const SESSION_KEY = 'sputnik_session'

function newSessionId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  // randomUUID есть только на HTTPS и localhost. На http://*.spootnik.local его нет.
  const bytes = new Uint8Array(16)
  if (typeof crypto !== 'undefined' && typeof crypto.getRandomValues === 'function') {
    crypto.getRandomValues(bytes)
  } else {
    for (let i = 0; i < bytes.length; i += 1) bytes[i] = Math.floor(Math.random() * 256)
  }
  bytes[6] = (bytes[6] & 0x0f) | 0x40
  bytes[8] = (bytes[8] & 0x3f) | 0x80
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('')
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}

function sessionHeaders(json: boolean): HeadersInit {
  let id = localStorage.getItem(SESSION_KEY)
  if (!id) {
    id = newSessionId()
    localStorage.setItem(SESSION_KEY, id)
  }
  const headers: Record<string, string> = { 'X-Session-Id': id }
  if (json) headers['Content-Type'] = 'application/json'
  return headers
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
  const restoring = ref(true)
  const error = ref<string | null>(null)
  const suggestions = ref<string[]>([])
  const fallbackSuggestions = options.fallbackSuggestions ?? []
  const suggestionCount = options.suggestionCount ?? 3
  let historyToken = 0

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
        { credentials: 'omit', headers: sessionHeaders(false) },
      )
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      const data = await response.json()
      const items = Array.isArray(data?.suggestions) ? data.suggestions : []
      suggestions.value = items
    } catch {
      suggestions.value = []
    }
  }

  function showSuggestionsIfEmpty() {
    if (isOpen.value && !restoring.value && !messages.value.length && !suggestions.value.length) {
      loadStarterSuggestions()
    }
  }

  function toggle() {
    isOpen.value = !isOpen.value
    showSuggestionsIfEmpty()
  }

  function open() {
    isOpen.value = true
    showSuggestionsIfEmpty()
  }

  function close() {
    isOpen.value = false
  }

  function rememberSession(id: unknown) {
    if (typeof id === 'string' && id) localStorage.setItem(SESSION_KEY, id)
  }

  function clearChat() {
    historyToken += 1
    messages.value = []
    error.value = null
    restoring.value = false
    localStorage.setItem(SESSION_KEY, newSessionId())
    suggestions.value = []
    loadStarterSuggestions()
  }

  async function loadHistory() {
    const token = ++historyToken
    restoring.value = true
    try {
      const response = await fetch(`${apiBase()}/v1/session`, {
        credentials: 'omit',
        headers: sessionHeaders(false),
      })
      if (token !== historyToken) return
      if (!response.ok) return
      const data = await response.json()
      rememberSession(data?.session_id)
      const items = Array.isArray(data?.messages) ? data.messages : []
      if (messages.value.length) return
      messages.value = items
        .filter(
          (item: { role?: string; content?: unknown }) =>
            (item?.role === 'user' || item?.role === 'assistant') && typeof item.content === 'string',
        )
        .map((item: { role: 'user' | 'assistant'; content: string; sources?: ChatSource[] }) => ({
          id: generateId(),
          role: item.role,
          content: item.content,
          sources: Array.isArray(item.sources) ? item.sources : [],
        }))
    } catch {
      /* the panel stays empty and the next message starts from what the server still has */
    } finally {
      if (token === historyToken) {
        restoring.value = false
        showSuggestionsIfEmpty()
      }
    }
  }

  const historyReady = loadHistory()

  async function sendMessage(text: string) {
    const trimmed = text.trim()
    if (!trimmed || isLoading.value) return
    await historyReady

    error.value = null
    isLoading.value = true
    suggestions.value = []

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
        credentials: 'omit',
        headers: sessionHeaders(true),
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
      rememberSession(data?.session_id)
      assistantMsg.content = data.answer || 'Пустой ответ от сервера.'
      assistantMsg.sources = Array.isArray(data.sources) ? data.sources : []
      assistantMsg.streaming = false
      const next = Array.isArray(data.suggestions) ? data.suggestions : []
      suggestions.value = next
        .filter((item: unknown): item is string => typeof item === 'string' && item.trim().length > 0)
        .slice(0, suggestionCount)
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
    restoring,
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

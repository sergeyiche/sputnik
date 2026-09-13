import { ref } from 'vue'

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  streaming?: boolean
}

export interface UseChatOptions {
  apiUrl?: string
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

  function apiBase(): string {
    return resolveApiUrl(options.apiUrl)
  }

  function toggle() {
    isOpen.value = !isOpen.value
  }

  function open() {
    isOpen.value = true
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
    toggle,
    open,
    close,
    clearChat,
    sendMessage,
  }
}

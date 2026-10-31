import type {
  ConvertResultItem,
  DocumentsResponse,
  IngestState,
  PromptState,
  SourceItem,
  SourcesResponse,
} from './types'

const BASE = '/v1/admin'

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message)
  }
}

type UnauthorizedHandler = () => void
let onUnauthorized: UnauthorizedHandler = () => {}

export function setUnauthorizedHandler(handler: UnauthorizedHandler) {
  onUnauthorized = handler
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${BASE}${path}`, { credentials: 'same-origin', ...init })
  } catch {
    throw new ApiError('Нет связи с сервером', 0)
  }

  if (response.status === 401 && path !== '/login' && !path.startsWith('/password/')) {
    onUnauthorized()
  }
  if (!response.ok) {
    let detail = `Ошибка сервера (${response.status})`
    try {
      const body = await response.json()
      if (typeof body?.detail === 'string') detail = body.detail
    } catch {
      /* body is not JSON */
    }
    throw new ApiError(detail, response.status)
  }
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

function json(method: string, body?: unknown): RequestInit {
  return {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  }
}

export const api = {
  login: (username: string, password: string) =>
    request<{ username: string }>('/login', json('POST', { username, password })),
  forgotPassword: (email: string) =>
    request<{ detail: string }>('/password/forgot', json('POST', { email })),
  resetPassword: (token: string, password: string) =>
    request<{ detail: string }>('/password/reset', json('POST', { token, password })),
  logout: () => request<void>('/logout', { method: 'POST' }),
  me: () => request<{ username: string }>('/me'),

  sources: () => request<SourcesResponse>('/sources'),
  upload(file: File, overwrite = false) {
    const form = new FormData()
    form.append('file', file)
    form.append('overwrite', String(overwrite))
    return request<SourceItem>('/sources', { method: 'POST', body: form })
  },
  deleteSource: (name: string) =>
    request<{ deleted: string[] }>(`/sources?name=${encodeURIComponent(name)}`, { method: 'DELETE' }),
  convert: (name?: string) =>
    request<{ results: ConvertResultItem[] }>('/convert', json('POST', { name: name ?? null })),

  documents: () => request<DocumentsResponse>('/documents'),
  ingestState: () => request<IngestState>('/ingest'),
  startIngest: () => request<IngestState>('/ingest', { method: 'POST' }),

  prompt: () => request<PromptState>('/prompt'),
  savePrompt: (content: string, note: string) =>
    request<PromptState>('/prompt', json('PUT', { content, note })),
  activatePrompt: (versionId: string) =>
    request<PromptState>('/prompt/activate', json('POST', { version_id: versionId })),
  restoreDefaultPrompt: () => request<PromptState>('/prompt/restore-default', { method: 'POST' }),

  widget: () => request<WidgetSettings>('/widget'),
  saveWidget: (body: WidgetSettings) => request<WidgetSettings>('/widget', json('PUT', body)),

  questions: () => request<{ items: StarterQuestion[] }>('/questions'),
  addQuestion: (text: string) => request<StarterQuestion>('/questions', json('POST', { text })),
  updateQuestion: (id: number, text: string) =>
    request<StarterQuestion>(`/questions/${id}`, json('PUT', { text })),
  deleteQuestion: (id: number) => request<void>(`/questions/${id}`, { method: 'DELETE' }),
}

export interface StarterQuestion {
  id: number
  text: string
}

export interface WidgetSettings {
  title: string
  description: string
  primary_color: string
  show_sources: boolean
  greeting: string
  disclaimer: string
  legal_notes: string
  logo_url: string
  origin: string
  snippet: string
}

async function platformRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`/v1/platform${path}`, { credentials: 'same-origin', ...init })
  if (!response.ok) {
    let detail = `Ошибка сервера (${response.status})`
    try {
      const body = await response.json()
      if (typeof body?.detail === 'string') detail = body.detail
    } catch {
      /* not json */
    }
    throw new ApiError(detail, response.status)
  }
  return (await response.json()) as T
}

export interface PlatformTenant {
  id: number
  slug: string
  name: string
  email: string
  phone: string
  status: string
  documents: number
  disk_bytes: number
  questions_7d: number
  tokens_7d: number
  questions_today: number
  tokens_today: number
  errors_today: number
  anomalies: string[]
}

export const platformApi = {
  login: (username: string, password: string) =>
    platformRequest<{ username: string }>('/login', json('POST', { username, password })),
  tenants: () => platformRequest<{ items: PlatformTenant[] }>('/tenants'),
  approve: (id: number) => platformRequest<{ status: string }>(`/tenants/${id}/approve`, { method: 'POST' }),
  reject: (id: number) => platformRequest<{ status: string }>(`/tenants/${id}/reject`, { method: 'POST' }),
  setEnabled: (id: number, enabled: boolean) =>
    platformRequest<{ status: string }>(`/tenants/${id}/enabled`, json('POST', { enabled })),
  gigachat: () =>
    platformRequest<{
      model: string
      scope: string
      questions_7d: number
      tokens_7d: number
      balance: unknown
      balance_note: string
      alerts: string[]
    }>('/gigachat'),
}

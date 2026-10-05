import type {
  ConvertResultItem,
  DocumentsResponse,
  IngestState,
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

  if (response.status === 401 && path !== '/login') {
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
}

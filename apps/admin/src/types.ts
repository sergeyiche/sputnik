export type ConversionStatus = 'converted' | 'outdated' | 'not_converted' | 'unsupported'

export interface SourceItem {
  name: string
  title: string
  format: string
  size_bytes: number
  modified_at: string
  supported: boolean
  conversion_status: ConversionStatus
  document: string | null
}

export interface SourcesResponse {
  items: SourceItem[]
  supported_formats: string[]
  max_upload_mb: number
}

export interface ConvertResultItem {
  source: string
  document: string
  status: 'created' | 'updated' | 'skipped' | 'failed'
  message: string
}

export interface DocumentItem {
  name: string
  size_bytes: number
  modified_at: string
  converted_at: string | null
  source_file: string | null
  in_index: boolean
}

export interface DocumentsResponse {
  items: DocumentItem[]
  chunk_count: number
}

export interface IngestState {
  status: 'idle' | 'running' | 'succeeded' | 'failed'
  phase: 'preparing' | 'embedding' | 'swapping' | null
  started_at: string | null
  finished_at: string | null
  documents: number
  chunks_total: number
  chunks_done: number
  duration_seconds: number | null
  error: string | null
}

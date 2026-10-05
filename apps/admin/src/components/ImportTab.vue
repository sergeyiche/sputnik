<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { api, ApiError } from '../api'
import { formatDate, formatSize } from '../format'
import type { DocumentItem, IngestState } from '../types'

const POLL_INTERVAL_MS = 1500

const documents = ref<DocumentItem[]>([])
const chunkCount = ref(0)
const ingest = ref<IngestState | null>(null)
const loading = ref(false)
const error = ref<string | null>(null)
let pollTimer: ReturnType<typeof setTimeout> | undefined

const PHASE_LABELS: Record<string, string> = {
  preparing: 'Чтение и разбиение документов…',
  embedding: 'Векторизация фрагментов…',
  swapping: 'Подключение новой базы…',
}

const running = computed(() => ingest.value?.status === 'running')
const notIndexed = computed(() => documents.value.filter((d) => !d.in_index).length)
const progress = computed(() => {
  const state = ingest.value
  if (!state?.chunks_total) return 0
  return Math.round((state.chunks_done / state.chunks_total) * 100)
})

function messageOf(e: unknown): string {
  return e instanceof ApiError ? e.message : 'Неизвестная ошибка'
}

async function loadDocuments() {
  loading.value = true
  try {
    const data = await api.documents()
    documents.value = data.items
    chunkCount.value = data.chunk_count
  } catch (e) {
    error.value = messageOf(e)
  } finally {
    loading.value = false
  }
}

async function poll() {
  clearTimeout(pollTimer)
  try {
    const wasRunning = running.value
    ingest.value = await api.ingestState()
    if (running.value) {
      pollTimer = setTimeout(poll, POLL_INTERVAL_MS)
    } else if (wasRunning) {
      await loadDocuments()
    }
  } catch (e) {
    error.value = messageOf(e)
  }
}

async function startImport() {
  const warning =
    'Запустить полный импорт?\n\nИндекс будет пересобран из всех файлов вкладки «Импорт». ' +
    'Ассистент продолжит отвечать по текущей базе, пока новая не будет готова.'
  if (!confirm(warning)) return
  error.value = null
  try {
    ingest.value = await api.startIngest()
    pollTimer = setTimeout(poll, POLL_INTERVAL_MS)
  } catch (e) {
    error.value = messageOf(e)
    await poll()
  }
}

onMounted(async () => {
  await Promise.all([loadDocuments(), poll()])
})
onBeforeUnmount(() => clearTimeout(pollTimer))
</script>

<template>
  <section class="card">
    <div class="toolbar">
      <button class="btn btn--primary" type="button" :disabled="running" @click="startImport">
        <span v-if="running" class="spinner" />
        {{ running ? 'Импорт выполняется…' : 'Импорт' }}
      </button>
      <span class="toolbar__hint">
        Документов: {{ documents.length }} · фрагментов в индексе: {{ chunkCount }}
        <template v-if="notIndexed"> · не в базе: {{ notIndexed }}</template>
      </span>
    </div>

    <div v-if="running && ingest" class="notice notice--info">
      {{ PHASE_LABELS[ingest.phase ?? ''] ?? 'Импорт…' }}
      <template v-if="ingest.chunks_total">{{ ingest.chunks_done }} из {{ ingest.chunks_total }} ({{ progress }}%)</template>
      <div class="progress"><div class="progress__bar" :style="{ width: `${progress}%` }" /></div>
    </div>
    <p v-else-if="ingest?.status === 'succeeded'" class="notice notice--success">
      Последний импорт завершён {{ formatDate(ingest.finished_at) }}: {{ ingest.documents }} документов,
      {{ ingest.chunks_total }} фрагментов за {{ ingest.duration_seconds }} с.
    </p>
    <p v-else-if="ingest?.status === 'failed'" class="notice notice--error">
      Импорт {{ formatDate(ingest.finished_at) }} завершился ошибкой: {{ ingest.error }}. Текущая база не изменена.
    </p>
    <p v-if="error" class="notice notice--error" role="alert">{{ error }}</p>

    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Название файла</th>
            <th class="num">Размер</th>
            <th>Дата конвертации</th>
            <th>Есть в Базе знаний</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="doc in documents" :key="doc.name">
            <td>
              {{ doc.name }}
              <span v-if="doc.source_file" class="sub">из {{ doc.source_file }}</span>
              <span v-else class="sub">создан вручную</span>
            </td>
            <td class="num">{{ formatSize(doc.size_bytes) }}</td>
            <td class="nowrap">
              {{ formatDate(doc.converted_at) }}
              <span v-if="!doc.converted_at" class="sub">изменён {{ formatDate(doc.modified_at) }}</span>
            </td>
            <td>
              <span class="badge" :class="doc.in_index ? 'badge--ok' : 'badge--no'">
                {{ doc.in_index ? 'Да' : 'Нет' }}
              </span>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-if="!documents.length && !loading" class="empty">Нет сконвертированных документов</p>
    </div>
  </section>
</template>

<style scoped>
.progress {
  margin-top: 8px;
  height: 6px;
  border-radius: 999px;
  background: #fff;
  overflow: hidden;
}

.progress__bar {
  height: 100%;
  background: var(--brand);
  transition: width 0.4s ease;
}
</style>

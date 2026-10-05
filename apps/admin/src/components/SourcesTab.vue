<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api, ApiError } from '../api'
import { formatDate, formatSize } from '../format'
import type { ConversionStatus, ConvertResultItem, SourceItem } from '../types'

const items = ref<SourceItem[]>([])
const supportedFormats = ref<string[]>([])
const maxUploadMb = ref(0)
const loading = ref(false)
const busy = ref<string | null>(null)
const error = ref<string | null>(null)
const success = ref<string | null>(null)
const dragActive = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)

const STATUS_LABELS: Record<ConversionStatus, { text: string; tone: string }> = {
  converted: { text: 'Да', tone: 'ok' },
  outdated: { text: 'Устарел', tone: 'warn' },
  not_converted: { text: 'Нет', tone: 'no' },
  unsupported: { text: 'Формат не поддерживается', tone: 'muted' },
}

const acceptAttr = computed(() => supportedFormats.value.map((f) => `.${f}`).join(','))
const pendingCount = computed(
  () => items.value.filter((i) => i.conversion_status === 'outdated' || i.conversion_status === 'not_converted').length,
)

function notify(kind: 'error' | 'success', message: string) {
  error.value = kind === 'error' ? message : null
  success.value = kind === 'success' ? message : null
}

function messageOf(e: unknown): string {
  return e instanceof ApiError ? e.message : 'Неизвестная ошибка'
}

async function load() {
  loading.value = true
  try {
    const data = await api.sources()
    items.value = data.items
    supportedFormats.value = data.supported_formats
    maxUploadMb.value = data.max_upload_mb
  } catch (e) {
    notify('error', messageOf(e))
  } finally {
    loading.value = false
  }
}

async function uploadFiles(files: FileList | File[]) {
  const list = Array.from(files)
  if (!list.length) return
  busy.value = 'upload'
  const uploaded: string[] = []
  const failed: string[] = []

  for (const file of list) {
    try {
      await api.upload(file)
      uploaded.push(file.name)
    } catch (e) {
      if (e instanceof ApiError && e.status === 409 && confirm(`Файл «${file.name}» уже есть. Заменить его?`)) {
        try {
          await api.upload(file, true)
          uploaded.push(file.name)
          continue
        } catch (retryError) {
          failed.push(`${file.name}: ${messageOf(retryError)}`)
          continue
        }
      }
      if (!(e instanceof ApiError && e.status === 409)) failed.push(`${file.name}: ${messageOf(e)}`)
    }
  }

  busy.value = null
  if (fileInput.value) fileInput.value.value = ''
  await load()
  if (failed.length) notify('error', `Не загружено — ${failed.join('; ')}`)
  else if (uploaded.length) notify('success', `Загружено: ${uploaded.join(', ')}. Не забудьте сконвертировать.`)
}

function onFileChange(event: Event) {
  const input = event.target as HTMLInputElement
  if (input.files) uploadFiles(input.files)
}

function onDrop(event: DragEvent) {
  dragActive.value = false
  if (event.dataTransfer?.files) uploadFiles(event.dataTransfer.files)
}

async function remove(item: SourceItem) {
  const extra = item.document ? `\nТакже будет удалён сконвертированный файл «${item.document}».` : ''
  if (!confirm(`Удалить «${item.name}»?${extra}\nИз ответов ассистента документ исчезнет после следующего импорта.`)) return
  busy.value = item.name
  try {
    const result = await api.deleteSource(item.name)
    notify('success', `Удалено: ${result.deleted.join(', ')}`)
    await load()
  } catch (e) {
    notify('error', messageOf(e))
  } finally {
    busy.value = null
  }
}

function describeResults(results: ConvertResultItem[]): void {
  const failed = results.filter((r) => r.status === 'failed')
  const changed = results.filter((r) => r.status === 'created' || r.status === 'updated')
  if (failed.length) {
    notify('error', `Ошибка конвертации — ${failed.map((r) => `${r.source}: ${r.message}`).join('; ')}`)
  } else if (changed.length) {
    notify('success', `Сконвертировано: ${changed.map((r) => `${r.source} → ${r.document}`).join(', ')}. Для обновления ответов запустите импорт.`)
  } else {
    notify('success', 'Все файлы уже сконвертированы')
  }
}

async function convert(item?: SourceItem) {
  if (item?.conversion_status === 'converted' && !confirm(`Файл уже сконвертирован. Перезаписать «${item.document}»?\nРучные правки в нём будут потеряны.`)) return
  busy.value = item ? item.name : 'convert-all'
  try {
    const { results } = await api.convert(item?.name)
    describeResults(results)
    await load()
  } catch (e) {
    notify('error', messageOf(e))
  } finally {
    busy.value = null
  }
}

onMounted(load)
</script>

<template>
  <section
    class="card"
    :class="{ 'is-drag': dragActive }"
    @dragover.prevent="dragActive = true"
    @dragleave.self="dragActive = false"
    @drop.prevent="onDrop"
  >
    <div class="toolbar">
      <button class="btn btn--primary" type="button" :disabled="!!busy" @click="fileInput?.click()">
        <span v-if="busy === 'upload'" class="spinner" />
        Загрузить
      </button>
      <input ref="fileInput" type="file" multiple hidden :accept="acceptAttr" @change="onFileChange" />
      <button class="btn" type="button" :disabled="!!busy || !pendingCount" @click="convert()">
        <span v-if="busy === 'convert-all'" class="spinner" />
        Конвертировать новые и изменённые ({{ pendingCount }})
      </button>
      <span class="toolbar__spacer" />
      <span class="toolbar__hint">
        {{ supportedFormats.join(', ').toUpperCase() }} · до {{ maxUploadMb }} МБ · можно перетащить файлы сюда
      </span>
    </div>

    <p v-if="error" class="notice notice--error" role="alert">{{ error }}</p>
    <p v-if="success" class="notice notice--success">{{ success }}</p>

    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Название документа</th>
            <th>Тип</th>
            <th>Дата</th>
            <th class="num">Размер</th>
            <th>Сконвертирован</th>
            <th />
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in items" :key="item.name">
            <td>
              {{ item.title }}
              <span class="sub">{{ item.name }}</span>
            </td>
            <td><span class="badge badge--format">{{ item.format }}</span></td>
            <td class="nowrap">{{ formatDate(item.modified_at) }}</td>
            <td class="num">{{ formatSize(item.size_bytes) }}</td>
            <td>
              <span class="badge" :class="`badge--${STATUS_LABELS[item.conversion_status].tone}`">
                {{ STATUS_LABELS[item.conversion_status].text }}
              </span>
              <span v-if="item.document" class="sub">→ {{ item.document }}</span>
            </td>
            <td class="actions">
              <button
                class="btn btn--small"
                type="button"
                :disabled="!!busy || !item.supported"
                @click="convert(item)"
              >
                <span v-if="busy === item.name" class="spinner" />
                Конвертировать
              </button>
              <button class="btn btn--small btn--danger" type="button" :disabled="!!busy" @click="remove(item)">
                Удалить
              </button>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-if="!items.length && !loading" class="empty">Файлов пока нет — загрузите первый документ</p>
      <p v-if="loading && !items.length" class="empty"><span class="spinner" /></p>
    </div>
  </section>
</template>

<style scoped>
.is-drag {
  outline: 2px dashed var(--brand);
  outline-offset: -6px;
  background: var(--brand-soft);
}
</style>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api, ApiError } from '../api'
import { formatDate } from '../format'
import type { PromptState, PromptVersion } from '../types'

const MAX_CHARS = 20_000

const state = ref<PromptState | null>(null)
const draft = ref('')
const note = ref('')
const loading = ref(true)
const busy = ref<'save' | 'activate' | 'restore' | null>(null)
const error = ref<string | null>(null)
const info = ref<string | null>(null)

const dirty = computed(() => state.value !== null && draft.value !== state.value.content)
const isBusy = computed(() => busy.value !== null)
const statusLabel = computed(() => {
  const current = state.value
  if (!current) return ''
  if (current.mode === 'default') return 'Сейчас используется промпт по умолчанию'
  const active = current.versions.find((item) => item.active)
  const when = active ? formatDate(active.created_at) : 'сохранённой версии'
  return `Сейчас используется версия от ${when}`
})

function messageOf(e: unknown): string {
  return e instanceof ApiError ? e.message : 'Неизвестная ошибка'
}

function applyState(next: PromptState) {
  state.value = next
  draft.value = next.content
  note.value = ''
}

async function load() {
  loading.value = true
  error.value = null
  try {
    applyState(await api.prompt())
  } catch (e) {
    error.value = messageOf(e)
  } finally {
    loading.value = false
  }
}

function confirmDirty(action: string): boolean {
  if (!dirty.value) return true
  return confirm(`${action}\n\nВ редакторе есть несохранённые правки — они будут потеряны.`)
}

async function save() {
  if (!draft.value.trim()) {
    error.value = 'Промпт не может быть пустым'
    return
  }
  busy.value = 'save'
  error.value = null
  info.value = null
  try {
    applyState(await api.savePrompt(draft.value, note.value))
    info.value = 'Новая версия сохранена и уже используется ассистентом'
  } catch (e) {
    error.value = messageOf(e)
  } finally {
    busy.value = null
  }
}

function openDefault() {
  const current = state.value
  if (!current) return
  if (!confirmDirty('Открыть промпт по умолчанию в редакторе?')) return
  draft.value = current.default_content
  note.value = ''
  info.value =
    current.mode === 'default'
      ? null
      : 'В редакторе текст промпта по умолчанию. Чтобы ассистент перешёл на него, нажмите «Восстановить промпт по умолчанию».'
}

function openVersion(version: PromptVersion) {
  if (!confirmDirty(`Открыть версию от ${formatDate(version.created_at)} в редакторе?`)) return
  draft.value = version.content
  note.value = ''
  info.value = version.active
    ? null
    : `В редакторе текст версии от ${formatDate(version.created_at)}. Чтобы ассистент перешёл на неё без новой копии, нажмите «Сделать текущей».`
}

async function activate(version: PromptVersion) {
  if (version.active) return
  if (!confirmDirty(`Сделать текущей версию от ${formatDate(version.created_at)}?`)) return
  busy.value = 'activate'
  error.value = null
  info.value = null
  try {
    applyState(await api.activatePrompt(version.id))
    info.value = `Ассистент переключён на версию от ${formatDate(version.created_at)}`
  } catch (e) {
    error.value = messageOf(e)
  } finally {
    busy.value = null
  }
}

async function restoreDefault() {
  const current = state.value
  if (!current) return
  const extra = dirty.value ? '\n\nНесохранённые правки в редакторе будут потеряны.' : ''
  if (
    !confirm(
      `Вернуть промпт по умолчанию из файла конфигурации?${extra}\nСохранённые версии останутся в истории.`,
    )
  ) {
    return
  }
  busy.value = 'restore'
  error.value = null
  info.value = null
  try {
    applyState(await api.restoreDefaultPrompt())
    info.value = 'Ассистент снова использует промпт по умолчанию'
  } catch (e) {
    error.value = messageOf(e)
  } finally {
    busy.value = null
  }
}

onMounted(load)
</script>

<template>
  <div v-if="loading" class="empty"><span class="spinner" /> Загрузка промпта…</div>
  <div v-else-if="state" class="prompt">
    <section class="card">
      <div class="toolbar">
        <button class="btn btn--primary" type="button" :disabled="isBusy || !dirty" @click="save">
          <span v-if="busy === 'save'" class="spinner" />
          Сохранить версию
        </button>
        <button class="btn" type="button" :disabled="isBusy || state.mode === 'default'" @click="restoreDefault">
          Восстановить промпт по умолчанию
        </button>
        <span class="toolbar__spacer" />
        <span class="toolbar__hint">{{ draft.length.toLocaleString('ru-RU') }} / {{ MAX_CHARS.toLocaleString('ru-RU') }}</span>
      </div>

      <div class="prompt__body">
        <p v-if="error" class="notice notice--error">{{ error }}</p>
        <p v-if="info" class="notice notice--success">{{ info }}</p>
        <p v-if="dirty" class="notice notice--info">Есть несохранённые правки. Ассистент их ещё не видит.</p>
        <p class="prompt__status">{{ statusLabel }}</p>
        <label class="prompt__label" for="prompt-text">Текст системного промпта</label>
        <textarea
          id="prompt-text"
          v-model="draft"
          class="prompt__editor"
          :maxlength="MAX_CHARS"
          spellcheck="true"
          :disabled="isBusy"
        />
        <label class="prompt__label" for="prompt-note">Комментарий к версии (необязательно, сохранится вместе с изменённым текстом)</label>
        <input
          id="prompt-note"
          v-model="note"
          class="prompt__note"
          type="text"
          maxlength="200"
          placeholder="Например: смягчил тон про лекарства"
          :disabled="isBusy"
        />
      </div>
    </section>

    <section class="card">
      <div class="toolbar">
        <strong>История</strong>
        <span class="toolbar__hint">откат не удаляет версии</span>
      </div>
      <ul class="versions">
        <li class="versions__item" :class="{ 'is-active': state.mode === 'default' }">
          <div class="versions__meta">
            <span class="badge" :class="state.mode === 'default' ? 'badge--ok' : 'badge--muted'">
              {{ state.mode === 'default' ? 'текущий' : 'по умолчанию' }}
            </span>
            <span>Файл конфигурации</span>
          </div>
          <div class="versions__actions">
            <button class="btn btn--small" type="button" :disabled="isBusy" @click="openDefault">Открыть</button>
            <button
              class="btn btn--small"
              type="button"
              :disabled="isBusy || state.mode === 'default'"
              @click="restoreDefault"
            >
              Сделать текущим
            </button>
          </div>
        </li>
        <li v-if="!state.versions.length" class="empty">Сохранённых версий пока нет</li>
        <li
          v-for="version in state.versions"
          :key="version.id"
          class="versions__item"
          :class="{ 'is-active': version.active }"
        >
          <div class="versions__meta">
            <span class="badge" :class="version.active ? 'badge--ok' : 'badge--muted'">
              {{ version.active ? 'текущая' : 'версия' }}
            </span>
            <span>{{ formatDate(version.created_at) }}</span>
            <span v-if="version.note" class="sub">{{ version.note }}</span>
          </div>
          <div class="versions__actions">
            <button class="btn btn--small" type="button" :disabled="isBusy" @click="openVersion(version)">Открыть</button>
            <button
              class="btn btn--small"
              type="button"
              :disabled="isBusy || version.active"
              @click="activate(version)"
            >
              Сделать текущей
            </button>
          </div>
        </li>
      </ul>
    </section>
  </div>
  <p v-else class="notice notice--error">{{ error }}</p>
</template>

<style scoped>
.prompt {
  display: grid;
  gap: 16px;
}

.prompt__body {
  padding: 16px;
}

.prompt__status {
  margin: 0 0 12px;
  color: var(--muted);
}

.prompt__label {
  display: block;
  margin: 12px 0 6px;
  font-size: 0.85rem;
  font-weight: 600;
}

.prompt__editor {
  width: 100%;
  min-height: 420px;
  padding: 12px;
  border: 1px solid var(--border);
  border-radius: 8px;
  resize: vertical;
  font: 14px/1.5 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  color: var(--text);
}

.prompt__note {
  width: 100%;
  padding: 8px 12px;
  border: 1px solid var(--border);
  border-radius: 8px;
  font: inherit;
}

.versions {
  list-style: none;
  margin: 0;
  padding: 0;
}

.versions__item {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: center;
  padding: 12px 16px;
  border-top: 1px solid var(--border);
}

.versions__item.is-active {
  background: var(--success-soft);
}

.versions__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: baseline;
  min-width: 0;
}

.versions__actions {
  display: flex;
  gap: 6px;
  flex-shrink: 0;
}
</style>

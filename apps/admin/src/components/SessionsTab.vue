<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api, type SessionSummary, type SessionTranscript } from '../api'
import { formatDate } from '../format'

const items = ref<SessionSummary[]>([])
const nextBefore = ref<string | null>(null)
const selectedId = ref<string | null>(null)
const transcript = ref<SessionTranscript | null>(null)
const loadingList = ref(false)
const loadingTranscript = ref(false)
const error = ref('')

onMounted(() => {
  void loadList()
})

async function loadList(before?: string) {
  loadingList.value = true
  error.value = ''
  try {
    const page = await api.sessions(before)
    items.value = before ? [...items.value, ...page.items] : page.items
    nextBefore.value = page.next_before
    if (!selectedId.value && items.value.length) {
      await openSession(items.value[0].session_id)
    }
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : 'Не удалось загрузить диалоги'
  } finally {
    loadingList.value = false
  }
}

async function openSession(id: string) {
  selectedId.value = id
  loadingTranscript.value = true
  error.value = ''
  try {
    transcript.value = await api.session(id)
  } catch (exc) {
    transcript.value = null
    error.value = exc instanceof Error ? exc.message : 'Не удалось открыть диалог'
  } finally {
    loadingTranscript.value = false
  }
}

function sourceTitle(source: { title?: string; index?: number }): string {
  return source.title || (source.index ? `Документ ${source.index}` : 'Документ')
}
</script>

<template>
  <div class="sessions">
    <p class="hint">
      Переписка виджета сохраняется без срока давности. Здесь её можно читать, чтобы разбирать ответы и инциденты.
    </p>
    <p v-if="error" class="err">{{ error }}</p>
    <div class="board">
      <aside class="list card">
        <p v-if="!items.length && !loadingList" class="empty">Диалогов пока нет</p>
        <button
          v-for="item in items"
          :key="item.session_id"
          type="button"
          class="list__item"
          :class="{ 'is-active': item.session_id === selectedId }"
          @click="openSession(item.session_id)"
        >
          <span class="list__preview">{{ item.preview }}</span>
          <span class="list__meta">{{ formatDate(item.updated_at) }} · {{ item.message_count }}</span>
        </button>
        <button
          v-if="nextBefore"
          class="btn btn--small list__more"
          type="button"
          :disabled="loadingList"
          @click="loadList(nextBefore || undefined)"
        >
          {{ loadingList ? 'Загрузка…' : 'Ещё' }}
        </button>
      </aside>

      <section class="thread card">
        <p v-if="loadingTranscript" class="empty">Загрузка…</p>
        <p v-else-if="!transcript" class="empty">Выберите диалог</p>
        <template v-else>
          <p v-if="transcript.topic" class="topic">Тема: {{ transcript.topic }}</p>
          <article
            v-for="(message, index) in transcript.messages"
            :key="`${message.created_at}-${index}`"
            class="turn"
            :class="`turn--${message.role}`"
          >
            <header>{{ message.role === 'user' ? 'Пользователь' : 'Ассистент' }}</header>
            <p>{{ message.content }}</p>
            <ul v-if="message.sources.length">
              <li v-for="source in message.sources" :key="`${source.index}-${source.title}`">
                {{ sourceTitle(source) }}
              </li>
            </ul>
          </article>
        </template>
      </section>
    </div>
  </div>
</template>

<style scoped>
.sessions {
  display: grid;
  gap: 12px;
}

.hint,
.empty {
  margin: 0;
  color: var(--muted);
}

.err {
  margin: 0;
  color: var(--danger);
}

.board {
  display: grid;
  grid-template-columns: 320px 1fr;
  gap: 16px;
  align-items: start;
}

.list,
.thread {
  min-height: 240px;
}

.list {
  display: grid;
  align-content: start;
  padding: 8px;
}

.list__item {
  display: grid;
  gap: 4px;
  width: 100%;
  padding: 10px 12px;
  border: none;
  border-radius: 8px;
  background: none;
  text-align: left;
  cursor: pointer;
}

.list__item:hover,
.list__item.is-active {
  background: var(--brand-soft);
}

.list__preview {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.list__meta,
.topic {
  color: var(--muted);
  font-size: 0.85rem;
}

.list__more {
  margin: 8px;
}

.thread {
  display: grid;
  gap: 12px;
  padding: 16px;
}

.topic {
  margin: 0;
}

.turn {
  display: grid;
  gap: 6px;
}

.turn header {
  font-size: 0.8rem;
  font-weight: 600;
  color: var(--muted);
}

.turn p {
  margin: 0;
  white-space: pre-wrap;
}

.turn--user p {
  padding: 10px 12px;
  border-radius: 10px;
  background: var(--bg);
}

.turn ul {
  margin: 0;
  padding-left: 18px;
  color: var(--muted);
  font-size: 0.85rem;
}

@media (max-width: 800px) {
  .board {
    grid-template-columns: 1fr;
  }
}
</style>

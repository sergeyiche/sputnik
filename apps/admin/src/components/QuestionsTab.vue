<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api, type StarterQuestion } from '../api'

const items = ref<StarterQuestion[]>([])
const draft = ref('')
const editing = ref<number | null>(null)
const editText = ref('')
const message = ref('')
const error = ref('')

onMounted(load)

async function load() {
  error.value = ''
  items.value = (await api.questions()).items
}

async function add() {
  error.value = ''
  message.value = ''
  try {
    const item = await api.addQuestion(draft.value)
    items.value = [...items.value, item]
    draft.value = ''
    message.value = 'Вопрос добавлен'
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : 'Не удалось добавить'
  }
}

function startEdit(item: StarterQuestion) {
  editing.value = item.id
  editText.value = item.text
  error.value = ''
  message.value = ''
}

async function saveEdit(id: number) {
  error.value = ''
  message.value = ''
  try {
    const item = await api.updateQuestion(id, editText.value)
    items.value = items.value.map((row) => (row.id === id ? item : row))
    editing.value = null
    message.value = 'Сохранено'
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : 'Не удалось сохранить'
  }
}

async function remove(id: number) {
  error.value = ''
  message.value = ''
  try {
    await api.deleteQuestion(id)
    items.value = items.value.filter((row) => row.id !== id)
    if (editing.value === id) editing.value = null
    message.value = 'Удалено'
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : 'Не удалось удалить'
  }
}
</script>

<template>
  <div class="questions">
    <p class="hint">В виджете показываются не больше трёх случайных вопросов. Если список пуст, блок с примерами скрыт.</p>
    <form class="add" @submit.prevent="add">
      <input v-model="draft" maxlength="240" required placeholder="Новый вопрос" />
      <button class="btn btn--primary" type="submit">Добавить</button>
    </form>
    <p v-if="message" class="ok">{{ message }}</p>
    <p v-if="error" class="err">{{ error }}</p>
    <p v-if="!items.length" class="empty">Вопросов пока нет</p>
    <ul v-else>
      <li v-for="item in items" :key="item.id">
        <template v-if="editing === item.id">
          <input v-model="editText" maxlength="240" />
          <button class="btn btn--small btn--primary" type="button" @click="saveEdit(item.id)">Сохранить</button>
          <button class="btn btn--small" type="button" @click="editing = null">Отмена</button>
        </template>
        <template v-else>
          <span>{{ item.text }}</span>
          <button class="btn btn--small" type="button" @click="startEdit(item)">Изменить</button>
          <button class="btn btn--small btn--danger" type="button" @click="remove(item.id)">Удалить</button>
        </template>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.questions { display: grid; gap: 12px; max-width: 720px; }
.hint, .empty { color: var(--muted); margin: 0; }
.add { display: flex; gap: 8px; }
.add input, li input { flex: 1; font: inherit; padding: 8px; }
ul { list-style: none; margin: 0; padding: 0; display: grid; gap: 8px; }
li {
  display: flex;
  gap: 8px;
  align-items: center;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 8px 10px;
}
li span { flex: 1; }
.ok { color: var(--success); margin: 0; }
.err { color: var(--danger); margin: 0; }
</style>

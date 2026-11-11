<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { api, type SkillItem, type SkillMatch, type SkillWriteBody } from '../api'

type Mode = 'list' | 'create' | 'edit'

const items = ref<SkillItem[]>([])
const previewQ = ref('Какие побочные эффекты у леводопы?')
const matches = ref<SkillMatch[]>([])
const message = ref('')
const error = ref('')
const busy = ref(false)
const mode = ref<Mode>('list')
const editingId = ref<number | null>(null)

const form = reactive({
  name: '',
  title: '',
  type: 'domain',
  activation: 'match',
  description: '',
  body: '',
  priority: 50,
  sticky_turns: 2,
  enabled: true,
  retrieval_boost: '',
  query_hints: '',
  examples: '',
  suggestions: '',
})

const formTitle = computed(() => (mode.value === 'create' ? 'Новый навык' : 'Редактирование навыка'))

onMounted(load)

function linesOf(text: string): string[] {
  return text
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
}

function joinLines(items: string[] | undefined): string {
  return (items ?? []).join('\n')
}

function suggestionsOf(item: SkillItem): string[] {
  const raw = item.config?.suggestions
  return Array.isArray(raw) ? raw.map((x) => String(x)).filter((x) => x.trim()) : []
}

function resetForm() {
  form.name = ''
  form.title = ''
  form.type = 'domain'
  form.activation = 'match'
  form.description = ''
  form.body = ''
  form.priority = 50
  form.sticky_turns = 2
  form.enabled = true
  form.retrieval_boost = ''
  form.query_hints = ''
  form.examples = ''
  form.suggestions = ''
}

function fillForm(item: SkillItem) {
  form.name = item.name
  form.title = item.title
  form.type = item.type || 'domain'
  form.activation = item.activation || 'match'
  form.description = item.description
  form.body = item.body
  form.priority = item.priority
  form.sticky_turns = item.sticky_turns
  form.enabled = item.enabled
  form.retrieval_boost = joinLines(item.retrieval_boost)
  form.query_hints = joinLines(item.query_hints)
  form.examples = joinLines(item.examples)
  form.suggestions = joinLines(suggestionsOf(item))
}

function toPayload(includeName: boolean): SkillWriteBody {
  const existing = editingId.value != null ? items.value.find((row) => row.id === editingId.value) : null
  const config: Record<string, unknown> = { ...(existing?.config ?? {}) }
  const suggestions = linesOf(form.suggestions)
  if (suggestions.length) config.suggestions = suggestions
  else delete config.suggestions
  const body: SkillWriteBody = {
    title: form.title.trim(),
    type: form.type,
    activation: form.activation,
    description: form.description.trim(),
    body: form.body.trim(),
    priority: Number(form.priority),
    sticky_turns: Number(form.sticky_turns),
    enabled: form.enabled,
    retrieval_boost: linesOf(form.retrieval_boost),
    query_hints: linesOf(form.query_hints),
    examples: linesOf(form.examples),
    config,
  }
  if (includeName) body.name = form.name.trim().toLowerCase()
  return body
}

async function load() {
  error.value = ''
  items.value = (await api.skills()).items
}

function startCreate() {
  mode.value = 'create'
  editingId.value = null
  resetForm()
  message.value = ''
  error.value = ''
}

function startEdit(item: SkillItem) {
  mode.value = 'edit'
  editingId.value = item.id
  fillForm(item)
  message.value = ''
  error.value = ''
}

function cancelEdit() {
  mode.value = 'list'
  editingId.value = null
  resetForm()
}

async function save() {
  busy.value = true
  error.value = ''
  message.value = ''
  try {
    if (mode.value === 'create') {
      const created = await api.createSkill(toPayload(true))
      items.value = [...items.value, created].sort((a, b) => b.priority - a.priority || a.name.localeCompare(b.name))
      message.value = 'Навык создан'
    } else if (editingId.value != null) {
      const updated = await api.updateSkill(editingId.value, toPayload(false))
      items.value = items.value.map((row) => (row.id === updated.id ? updated : row))
      message.value = 'Сохранено'
    }
    cancelEdit()
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : 'Не удалось сохранить'
  } finally {
    busy.value = false
  }
}

async function remove(item: SkillItem) {
  if (!confirm(`Удалить навык «${item.title}» (${item.name})?`)) return
  error.value = ''
  message.value = ''
  try {
    await api.deleteSkill(item.id)
    items.value = items.value.filter((row) => row.id !== item.id)
    if (editingId.value === item.id) cancelEdit()
    message.value = 'Удалено'
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : 'Не удалось удалить'
  }
}

async function seed() {
  busy.value = true
  error.value = ''
  message.value = ''
  try {
    const result = await api.seedSkills()
    await load()
    message.value = result.count
      ? `Добавлено шаблонов: ${result.count}`
      : 'Шаблоны фонда уже на месте (пустые suggestions при необходимости обновлены)'
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : 'Не удалось загрузить шаблоны'
  } finally {
    busy.value = false
  }
}

async function toggle(item: SkillItem) {
  error.value = ''
  try {
    const updated = await api.setSkillEnabled(item.id, !item.enabled)
    items.value = items.value.map((row) => (row.id === item.id ? updated : row))
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : 'Не удалось изменить'
  }
}

async function preview() {
  error.value = ''
  matches.value = []
  try {
    matches.value = (await api.previewSkills(previewQ.value)).skills
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : 'Превью недоступно'
  }
}
</script>

<template>
  <div class="skills">
    <p class="hint">
      Навыки уточняют ответ и поиск. После сохранения роутер перечитывает examples.
      В списках — по одной строке на значение.
    </p>

    <div v-if="mode === 'list'" class="toolbar">
      <button class="btn btn--primary" type="button" :disabled="busy" @click="startCreate">Новый навык</button>
      <button class="btn" type="button" :disabled="busy" @click="seed">Загрузить шаблоны фонда</button>
    </div>

    <p v-if="message" class="ok">{{ message }}</p>
    <p v-if="error" class="err">{{ error }}</p>

    <section v-if="mode !== 'list'" class="editor">
      <h3>{{ formTitle }}</h3>
      <form class="fields" @submit.prevent="save">
        <label>
          Имя (латиница)
          <input v-model="form.name" required pattern="[a-z0-9-]{1,64}" maxlength="64" :disabled="mode === 'edit'" />
        </label>
        <label>
          Заголовок
          <input v-model="form.title" required maxlength="120" />
        </label>
        <div class="row">
          <label>
            Тип
            <select v-model="form.type">
              <option value="domain">Тема (domain)</option>
              <option value="context">Контекст (context)</option>
              <option value="workflow">Сценарий (workflow)</option>
            </select>
          </label>
          <label>
            Активация
            <select v-model="form.activation">
              <option value="match">По совпадению</option>
              <option value="always">Всегда</option>
              <option value="intent">По намерению</option>
              <option value="manual">Вручную</option>
            </select>
          </label>
        </div>
        <div class="row">
          <label>
            Приоритет (0–100)
            <input v-model.number="form.priority" type="number" min="0" max="100" required />
          </label>
          <label>
            Удержание, ходов
            <input v-model.number="form.sticky_turns" type="number" min="0" max="20" required />
          </label>
          <label class="check">
            <input v-model="form.enabled" type="checkbox" />
            Включён
          </label>
        </div>
        <label>
          Описание (что делает и когда включать)
          <textarea v-model="form.description" required maxlength="1024" rows="3" />
        </label>
        <label>
          Инструкции
          <textarea v-model="form.body" required maxlength="20000" rows="8" />
        </label>
        <label>
          Примеры вопросов для роутера
          <textarea v-model="form.examples" rows="5" placeholder="По одному вопросу на строку" />
        </label>
        <label>
          Подсказки продолжения (после ответа)
          <textarea v-model="form.suggestions" rows="3" placeholder="По одной подсказке на строку" />
        </label>
        <label>
          Подсказки для поиска
          <textarea v-model="form.query_hints" rows="3" placeholder="По одной фразе на строку" />
        </label>
        <label>
          Усиление поиска — имена файлов базы знаний
          <textarea v-model="form.retrieval_boost" rows="3" placeholder="Памятка….txt" />
        </label>
        <div class="actions">
          <button class="btn btn--primary" type="submit" :disabled="busy">Сохранить</button>
          <button class="btn" type="button" :disabled="busy" @click="cancelEdit">Отмена</button>
        </div>
      </form>
    </section>

    <template v-else>
      <p v-if="!items.length" class="empty">Навыков пока нет — создайте или загрузите шаблоны фонда</p>
      <table v-else>
        <thead>
          <tr>
            <th>Название</th>
            <th>Тип</th>
            <th>Приоритет</th>
            <th>Примеры</th>
            <th />
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in items" :key="item.id" :class="{ off: !item.enabled }">
            <td>
              <strong>{{ item.title }}</strong>
              <span class="sub">{{ item.name }}</span>
            </td>
            <td>{{ item.type }}</td>
            <td>{{ item.priority }}</td>
            <td>{{ item.examples.length }}</td>
            <td class="ops">
              <button class="btn btn--small" type="button" @click="startEdit(item)">Изменить</button>
              <button class="btn btn--small" type="button" @click="toggle(item)">
                {{ item.enabled ? 'Выкл.' : 'Вкл.' }}
              </button>
              <button class="btn btn--small btn--danger" type="button" @click="remove(item)">Удалить</button>
            </td>
          </tr>
        </tbody>
      </table>

      <section class="preview">
        <h3>Превью роутера</h3>
        <form class="add" @submit.prevent="preview">
          <input v-model="previewQ" required placeholder="Вопрос пользователя" />
          <button class="btn btn--primary" type="submit">Проверить</button>
        </form>
        <ul v-if="matches.length">
          <li v-for="m in matches" :key="m.name">
            {{ m.title }} <code>{{ m.name }}</code> — score {{ m.score }}
            <span v-if="m.sticky">(sticky)</span>
          </li>
        </ul>
        <p v-else class="empty">Нет совпадений выше порога</p>
      </section>
    </template>
  </div>
</template>

<style scoped>
.skills {
  display: grid;
  gap: 12px;
  max-width: 860px;
}
.hint,
.empty {
  color: var(--muted);
  margin: 0;
}
.toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.ok {
  color: var(--success);
  margin: 0;
}
.err {
  color: var(--danger);
  margin: 0;
}
table {
  width: 100%;
  border-collapse: collapse;
}
th,
td {
  text-align: left;
  padding: 8px 10px;
  border-bottom: 1px solid var(--border);
  vertical-align: top;
}
.sub {
  display: block;
  color: var(--muted);
  font-size: 0.85rem;
}
tr.off {
  opacity: 0.55;
}
.ops {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  justify-content: flex-end;
}
.preview {
  margin-top: 16px;
  padding-top: 16px;
  border-top: 1px solid var(--border);
}
.add {
  display: flex;
  gap: 8px;
  margin: 10px 0;
}
.add input {
  flex: 1;
}
.editor {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 16px;
}
.editor h3 {
  margin: 0 0 12px;
}
.fields {
  display: grid;
  gap: 12px;
}
.fields label {
  display: grid;
  gap: 6px;
  font-size: 0.9rem;
  color: var(--muted);
}
.fields input,
.fields textarea,
.fields select,
.add input {
  font: inherit;
  padding: 8px 10px;
  border: 1px solid var(--border);
  border-radius: 8px;
  color: inherit;
}
.row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 12px;
  align-items: end;
}
.check {
  display: flex !important;
  flex-direction: row;
  align-items: center;
  gap: 8px;
  padding-bottom: 10px;
}
.actions {
  display: flex;
  gap: 8px;
}
</style>

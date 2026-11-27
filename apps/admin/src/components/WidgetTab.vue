<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api, type WidgetSettings } from '../api'

const form = ref<WidgetSettings | null>(null)
const message = ref('')
const error = ref('')

onMounted(async () => {
  form.value = await api.widget()
})

async function save() {
  if (!form.value) return
  error.value = ''
  message.value = ''
  try {
    form.value = await api.saveWidget(form.value)
    message.value = 'Сохранено'
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : 'Не удалось сохранить'
  }
}

async function copySnippet() {
  if (!form.value) return
  await navigator.clipboard.writeText(form.value.snippet)
  message.value = 'Код скопирован'
}

async function copyButtonSnippet() {
  if (!form.value) return
  await navigator.clipboard.writeText(form.value.open_button_snippet)
  message.value = 'Код кнопки скопирован'
}
</script>

<template>
  <form v-if="form" class="widget" @submit.prevent="save">
    <label>Название в шапке <input v-model="form.title" required maxlength="120" /></label>
    <label>Описание в шапке, до 150 символов <input v-model="form.description" maxlength="150" /></label>
    <label class="color">Цветовая схема
      <span class="color__row">
        <input v-model="form.primary_color" type="color" required />
        <span>{{ form.primary_color }}</span>
      </span>
    </label>
    <label class="check"><input v-model="form.show_sources" type="checkbox" /> Показывать источники</label>
    <label>Положение виджета
      <select v-model="form.position">
        <option value="bottom-right">Снизу справа (по умолчанию)</option>
        <option value="bottom-left">Снизу слева</option>
        <option value="top-right">Сверху справа</option>
        <option value="top-left">Сверху слева</option>
      </select>
      <small class="hint">
        Иконка и окно виджета будут открываться в выбранном углу. Выбор применяется к css-файлу
        виджета после нажатия «Сохранить»; код на сайте клиента менять не нужно.
      </small>
    </label>
    <label>Приветствие в чате, до 500 символов
      <textarea v-model="form.greeting" maxlength="500" rows="3" />
    </label>
    <label>Юридическая информация под чертой, до 250 символов
      <textarea v-model="form.disclaimer" required maxlength="250" rows="3" />
    </label>
    <label>Подробная юридическая информация, до 3000 символов
      <textarea v-model="form.legal_notes" maxlength="3000" rows="6" />
    </label>
    <p v-if="message" class="ok">{{ message }}</p>
    <p v-if="error" class="err">{{ error }}</p>
    <div class="actions">
      <button class="btn btn--primary" type="submit">Сохранить</button>
      <button class="btn" type="button" @click="copySnippet">Скопировать код для сайта</button>
    </div>
    <pre class="snippet">{{ form.snippet }}</pre>
    <section class="button-snippet">
      <h3>Отдельная кнопка на сайте</h3>
      <p class="hint">
        Этот код размещается в любом месте страницы клиента. Кнопка открывает окно виджета —
        удобно, когда иконка виджета скрыта или нужна заметная кнопка.
      </p>
      <pre class="snippet">{{ form.open_button_snippet }}</pre>
      <button class="btn" type="button" @click="copyButtonSnippet">Скопировать код кнопки</button>
    </section>
  </form>
</template>

<style scoped>
.widget { display: grid; gap: 12px; max-width: 640px; padding-bottom: 24px; }
label { display: grid; gap: 4px; font-size: 14px; }
input, textarea, select { font: inherit; padding: 8px; }
.color__row { display: flex; align-items: center; gap: 8px; }
input[type='color'] {
  width: 48px;
  height: 32px;
  padding: 2px;
  border: 1px solid #eadfce;
  background: #fff;
  cursor: pointer;
}
.check { display: flex; gap: 8px; align-items: center; }
.hint { color: #6b6258; font-size: 12px; line-height: 1.4; }
.actions {
  display: flex;
  gap: 8px;
  position: sticky;
  bottom: 0;
  margin: 0 -8px;
  padding: 12px 8px;
  background: var(--bg);
  border-top: 1px solid var(--border);
  z-index: 1;
}
.button-snippet {
  display: grid;
  gap: 8px;
  margin-top: 12px;
  padding-top: 16px;
  border-top: 1px solid var(--border);
}
.button-snippet h3 { margin: 0; font-size: 15px; }
.snippet { white-space: pre-wrap; background: #f6f1ea; padding: 12px; margin: 0; }
.ok { color: #0a7a32; }
.err { color: #a32020; }
</style>

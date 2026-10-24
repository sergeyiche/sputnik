<script setup lang="ts">
import { ref } from 'vue'
import { platformApi, type PlatformTenant } from '../api'

const username = ref('')
const password = ref('')
const operator = ref('')
const error = ref('')
const tenants = ref<PlatformTenant[]>([])
const alerts = ref<string[]>([])
const usage = ref('')

async function login() {
  error.value = ''
  try {
    const me = await platformApi.login(username.value, password.value)
    operator.value = me.username
    await refresh()
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : 'Не удалось войти'
  }
}

async function refresh() {
  const [list, card] = await Promise.all([platformApi.tenants(), platformApi.gigachat()])
  tenants.value = list.items
  alerts.value = card.alerts
  const balance = card.balance_note || (card.balance ? 'остаток кабинета получен' : 'остаток не запрашивался')
  usage.value = `${card.model} · ${card.scope} · вопросов за 7 дней: ${card.questions_7d} · токенов: ${card.tokens_7d}. ${balance}`
}

async function approve(id: number) {
  await platformApi.approve(id)
  await refresh()
}

async function reject(id: number) {
  await platformApi.reject(id)
  await refresh()
}

async function toggle(item: PlatformTenant, enabled: boolean) {
  await platformApi.setEnabled(item.id, enabled)
  await refresh()
}

function size(bytes: number): string {
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} КБ`
  return `${(bytes / (1024 * 1024)).toFixed(1)} МБ`
}
</script>

<template>
  <div v-if="!operator" class="gate">
    <form class="card gate__card" @submit.prevent="login">
      <h1 class="gate__title">Спутник</h1>
      <p class="gate__subtitle">Консоль платформы</p>
      <label class="gate__field">
        <span>Логин</span>
        <input v-model="username" autocomplete="username" required />
      </label>
      <label class="gate__field">
        <span>Пароль</span>
        <input v-model="password" type="password" autocomplete="current-password" required />
      </label>
      <p v-if="error" class="gate__error" role="alert">{{ error }}</p>
      <button class="btn btn--primary gate__submit" type="submit">Войти</button>
    </form>
  </div>
  <div v-else class="shell">
    <header class="shell__header">
      <div class="shell__brand">Спутник <span class="shell__section">платформа</span></div>
      <div class="shell__user">{{ operator }}</div>
    </header>
    <main class="shell__main">
      <p v-if="error" class="notice notice--error">{{ error }}</p>
      <p class="notice notice--info">{{ usage }}</p>
      <p v-for="alert in alerts" :key="alert" class="notice notice--error">{{ alert }}</p>
      <div class="card table-wrap">
        <table>
          <thead>
            <tr>
              <th>Организация</th>
              <th>Статус</th>
              <th class="num">Документы</th>
              <th class="num">Диск</th>
              <th class="num">Вопросы 7д</th>
              <th class="num">Токены 7д</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in tenants" :key="item.id">
              <td>
                {{ item.name }}
                <span class="sub">{{ item.slug }} · {{ item.email }}</span>
                <span v-for="reason in item.anomalies" :key="reason" class="sub anomaly">{{ reason }}</span>
              </td>
              <td>{{ item.status }}</td>
              <td class="num">{{ item.documents }}</td>
              <td class="num">{{ size(item.disk_bytes) }}</td>
              <td class="num">{{ item.questions_7d }}</td>
              <td class="num">{{ item.tokens_7d }}</td>
              <td class="actions">
                <button v-if="item.status === 'pending'" class="btn btn--small btn--primary" type="button" @click="approve(item.id)">Включить</button>
                <button v-if="item.status === 'pending'" class="btn btn--small" type="button" @click="reject(item.id)">Отклонить</button>
                <label v-if="item.status === 'active' || item.status === 'suspended'" class="toggle">
                  <input
                    type="checkbox"
                    :checked="item.status === 'active'"
                    @change="toggle(item, ($event.target as HTMLInputElement).checked)"
                  />
                  работает
                </label>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </main>
  </div>
</template>

<style scoped>
.gate {
  min-height: calc(100vh - 52px);
  display: grid;
  place-items: center;
  padding: 16px;
}

.gate__card {
  width: 100%;
  max-width: 360px;
  padding: 28px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.gate__title {
  margin: 0;
  font-size: 1.5rem;
  color: var(--brand-dark);
}

.gate__subtitle {
  margin: -8px 0 6px;
  color: var(--muted);
}

.gate__field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 0.85rem;
  color: var(--muted);
}

.gate__field input {
  padding: 9px 12px;
  border: 1px solid var(--border);
  border-radius: 8px;
  font: inherit;
  color: var(--text);
}

.gate__field input:focus {
  outline: 2px solid var(--brand-soft);
  border-color: var(--brand);
}

.gate__error {
  margin: 0;
  color: var(--danger);
}

.gate__submit {
  justify-content: center;
  padding: 10px;
}

.shell__header {
  display: flex;
  align-items: center;
  gap: 24px;
  padding: 0 24px;
  background: #fff;
  border-bottom: 1px solid var(--border);
}

.shell__brand {
  font-weight: 700;
  color: var(--brand-dark);
  padding: 14px 0;
}

.shell__section {
  margin-left: 6px;
  font-weight: 400;
  color: var(--muted);
}

.shell__user {
  margin-left: auto;
  color: var(--muted);
}

.shell__main {
  max-width: 1200px;
  margin: 0 auto;
  padding: 24px;
}

.anomaly {
  color: var(--danger);
}

.toggle {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
</style>

<script setup lang="ts">
import { ref } from 'vue'
import { api, ApiError } from '../api'

const emit = defineEmits<{
  authenticated: [username: string]
  leaveReset: []
  passwordChanged: []
}>()

type Mode = 'login' | 'forgot' | 'sent' | 'reset'

function tokenFromHash(): string {
  const match = /^#reset=(.*)$/.exec(window.location.hash)
  if (!match?.[1]) return ''
  try {
    return decodeURIComponent(match[1])
  } catch {
    return match[1]
  }
}

const mode = ref<Mode>(tokenFromHash() ? 'reset' : 'login')
const username = ref('')
const password = ref('')
const email = ref('')
const resetToken = ref(tokenFromHash())
const passwordAgain = ref('')
const error = ref<string | null>(null)
const notice = ref<string | null>(null)
const submitting = ref(false)

function clearHash() {
  if (!window.location.hash.startsWith('#reset=')) return
  history.replaceState(null, '', window.location.pathname + window.location.search)
}

function showLogin(message: string | null = null) {
  if (mode.value === 'reset') {
    clearHash()
    resetToken.value = ''
    emit('leaveReset')
  }
  mode.value = 'login'
  error.value = null
  notice.value = message
  password.value = ''
  passwordAgain.value = ''
}

async function submitLogin() {
  if (submitting.value) return
  error.value = null
  notice.value = null
  submitting.value = true
  try {
    const me = await api.login(username.value.trim(), password.value)
    password.value = ''
    emit('authenticated', me.username)
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : 'Не удалось войти'
  } finally {
    submitting.value = false
  }
}

async function submitForgot() {
  if (submitting.value) return
  error.value = null
  notice.value = null
  submitting.value = true
  try {
    const result = await api.forgotPassword(email.value.trim())
    notice.value = result.detail
    mode.value = 'sent'
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : 'Не удалось отправить письмо'
  } finally {
    submitting.value = false
  }
}

async function submitReset() {
  if (submitting.value) return
  error.value = null
  if (password.value !== passwordAgain.value) {
    error.value = 'Пароли не совпадают'
    return
  }
  submitting.value = true
  try {
    const result = await api.resetPassword(resetToken.value, password.value)
    password.value = ''
    passwordAgain.value = ''
    clearHash()
    resetToken.value = ''
    notice.value = result.detail
    mode.value = 'login'
    emit('passwordChanged')
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : 'Не удалось сохранить пароль'
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="login">
    <form v-if="mode === 'login'" class="card login__card" @submit.prevent="submitLogin">
      <h1 class="login__title">Спутник</h1>
      <p class="login__subtitle">Управление базой знаний</p>

      <label class="login__field">
        <span>Почта</span>
        <input v-model="username" autocomplete="username" required autofocus />
      </label>
      <label class="login__field">
        <span>Пароль</span>
        <input v-model="password" type="password" autocomplete="current-password" required />
      </label>

      <p v-if="notice" class="login__notice">{{ notice }}</p>
      <p v-if="error" class="login__error" role="alert">{{ error }}</p>

      <button class="btn btn--primary login__submit" type="submit" :disabled="submitting">
        <span v-if="submitting" class="spinner" />
        Войти
      </button>
      <button class="login__link" type="button" @click="mode = 'forgot'; error = null; notice = null">
        Забыли пароль?
      </button>
    </form>

    <form v-else-if="mode === 'forgot'" class="card login__card" @submit.prevent="submitForgot">
      <h1 class="login__title">Спутник</h1>
      <p class="login__subtitle">Новый пароль придёт на почту кабинета</p>

      <label class="login__field">
        <span>Почта</span>
        <input v-model="email" type="email" autocomplete="email" required autofocus />
      </label>

      <p v-if="error" class="login__error" role="alert">{{ error }}</p>

      <button class="btn btn--primary login__submit" type="submit" :disabled="submitting">
        <span v-if="submitting" class="spinner" />
        Отправить ссылку
      </button>
      <button class="login__link" type="button" @click="showLogin()">Назад ко входу</button>
    </form>

    <div v-else-if="mode === 'sent'" class="card login__card">
      <h1 class="login__title">Спутник</h1>
      <p class="login__notice">{{ notice }}</p>
      <button class="login__link" type="button" @click="showLogin()">Назад ко входу</button>
    </div>

    <form v-else class="card login__card" @submit.prevent="submitReset">
      <h1 class="login__title">Спутник</h1>
      <p class="login__subtitle">Новый пароль кабинета</p>

      <label class="login__field">
        <span>Новый пароль</span>
        <input v-model="password" type="password" autocomplete="new-password" minlength="8" required autofocus />
      </label>
      <label class="login__field">
        <span>Повторите пароль</span>
        <input v-model="passwordAgain" type="password" autocomplete="new-password" minlength="8" required />
      </label>

      <p v-if="error" class="login__error" role="alert">{{ error }}</p>

      <button class="btn btn--primary login__submit" type="submit" :disabled="submitting">
        <span v-if="submitting" class="spinner" />
        Сохранить пароль
      </button>
      <button class="login__link" type="button" @click="showLogin()">Назад ко входу</button>
    </form>
  </div>
</template>

<style scoped>
.login {
  min-height: 100vh;
  display: grid;
  place-items: center;
  padding: 16px;
}

.login__card {
  width: 100%;
  max-width: 360px;
  padding: 28px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.login__title {
  margin: 0;
  font-size: 1.5rem;
  color: var(--brand-dark);
}

.login__subtitle {
  margin: -8px 0 6px;
  color: var(--muted);
}

.login__field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 0.85rem;
  color: var(--muted);
}

.login__field input {
  padding: 9px 12px;
  border: 1px solid var(--border);
  border-radius: 8px;
  font: inherit;
  color: var(--text);
}

.login__field input:focus {
  outline: 2px solid var(--brand-soft);
  border-color: var(--brand);
}

.login__error {
  margin: 0;
  color: var(--danger);
  font-size: 0.9rem;
}

.login__notice {
  margin: 0;
  color: var(--success);
  font-size: 0.9rem;
}

.login__submit {
  justify-content: center;
  padding: 10px;
}

.login__link {
  align-self: flex-start;
  padding: 0;
  border: 0;
  background: none;
  color: var(--brand-dark);
  font: inherit;
  cursor: pointer;
}

.login__link:hover {
  text-decoration: underline;
}
</style>

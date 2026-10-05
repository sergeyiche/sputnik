<script setup lang="ts">
import { ref } from 'vue'
import { api, ApiError } from '../api'

const emit = defineEmits<{ authenticated: [username: string] }>()

const username = ref('')
const password = ref('')
const error = ref<string | null>(null)
const submitting = ref(false)

async function submit() {
  if (submitting.value) return
  error.value = null
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
</script>

<template>
  <div class="login">
    <form class="card login__card" @submit.prevent="submit">
      <h1 class="login__title">Спутник</h1>
      <p class="login__subtitle">Управление базой знаний</p>

      <label class="login__field">
        <span>Логин</span>
        <input v-model="username" autocomplete="username" required autofocus />
      </label>
      <label class="login__field">
        <span>Пароль</span>
        <input v-model="password" type="password" autocomplete="current-password" required />
      </label>

      <p v-if="error" class="login__error" role="alert">{{ error }}</p>

      <button class="btn btn--primary login__submit" type="submit" :disabled="submitting">
        <span v-if="submitting" class="spinner" />
        Войти
      </button>
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

.login__submit {
  justify-content: center;
  padding: 10px;
}
</style>

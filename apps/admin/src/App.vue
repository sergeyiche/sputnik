<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api, setUnauthorizedHandler } from './api'
import ImportTab from './components/ImportTab.vue'
import LoginForm from './components/LoginForm.vue'
import SourcesTab from './components/SourcesTab.vue'

type Tab = 'sources' | 'import'

const TABS: { id: Tab; label: string }[] = [
  { id: 'sources', label: 'База знаний' },
  { id: 'import', label: 'Импорт' },
]

const username = ref<string | null>(null)
const checking = ref(true)
const activeTab = ref<Tab>('sources')

setUnauthorizedHandler(() => {
  username.value = null
})

async function logout() {
  try {
    await api.logout()
  } finally {
    username.value = null
  }
}

onMounted(async () => {
  try {
    username.value = (await api.me()).username
  } catch {
    username.value = null
  } finally {
    checking.value = false
  }
})
</script>

<template>
  <div v-if="checking" class="boot"><span class="spinner" /></div>
  <LoginForm v-else-if="!username" @authenticated="username = $event" />
  <div v-else class="layout">
    <header class="header">
      <div class="header__brand">
        Спутник
        <span class="header__section">администрирование</span>
      </div>
      <nav class="tabs" role="tablist">
        <button
          v-for="tab in TABS"
          :key="tab.id"
          class="tabs__item"
          :class="{ 'is-active': activeTab === tab.id }"
          type="button"
          role="tab"
          :aria-selected="activeTab === tab.id"
          @click="activeTab = tab.id"
        >
          {{ tab.label }}
        </button>
      </nav>
      <div class="header__user">
        {{ username }}
        <button class="btn btn--small" type="button" @click="logout">Выйти</button>
      </div>
    </header>

    <main class="main">
      <SourcesTab v-if="activeTab === 'sources'" />
      <ImportTab v-else />
    </main>
  </div>
</template>

<style scoped>
.boot {
  min-height: 100vh;
  display: grid;
  place-items: center;
  color: var(--brand);
}

.layout {
  min-height: 100vh;
}

.header {
  display: flex;
  align-items: center;
  gap: 24px;
  padding: 0 24px;
  background: #fff;
  border-bottom: 1px solid var(--border);
}

.header__brand {
  font-weight: 700;
  color: var(--brand-dark);
  padding: 14px 0;
}

.header__section {
  margin-left: 6px;
  font-weight: 400;
  color: var(--muted);
}

.tabs {
  display: flex;
  gap: 4px;
  flex: 1;
  align-self: stretch;
}

.tabs__item {
  padding: 0 14px;
  border: none;
  border-bottom: 3px solid transparent;
  background: none;
  color: var(--muted);
  cursor: pointer;
}

.tabs__item:hover {
  color: var(--text);
}

.tabs__item.is-active {
  color: var(--brand-dark);
  border-bottom-color: var(--brand);
  font-weight: 600;
}

.header__user {
  display: flex;
  align-items: center;
  gap: 10px;
  color: var(--muted);
  font-size: 0.9rem;
}

.main {
  max-width: 1200px;
  margin: 0 auto;
  padding: 24px;
}
</style>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api, setUnauthorizedHandler } from './api'
import ImportTab from './components/ImportTab.vue'
import LoginForm from './components/LoginForm.vue'
import PlatformConsole from './components/PlatformConsole.vue'
import PromptTab from './components/PromptTab.vue'
import SourcesTab from './components/SourcesTab.vue'
import WidgetTab from './components/WidgetTab.vue'

type Tab = 'sources' | 'import' | 'prompt' | 'widget'

const operatorConsole = window.location.pathname.startsWith('/console')

const TABS: { id: Tab; label: string }[] = [
  { id: 'sources', label: 'База знаний' },
  { id: 'import', label: 'Импорт' },
  { id: 'prompt', label: 'Промпт' },
  { id: 'widget', label: 'Виджет' },
]

const username = ref<string | null>(null)
const checking = ref(true)
const activeTab = ref<Tab>('sources')
const openingReset = ref(window.location.hash.startsWith('#reset='))

setUnauthorizedHandler(() => {
  username.value = null
})

function onAuthenticated(name: string) {
  openingReset.value = false
  username.value = name
}

function onPasswordChanged() {
  openingReset.value = false
  username.value = null
}

async function logout() {
  try {
    await api.logout()
  } finally {
    username.value = null
  }
}

onMounted(async () => {
  document.title = operatorConsole ? 'Спутник — платформа' : 'Спутник — администрирование'
  if (operatorConsole) {
    checking.value = false
    return
  }
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
  <PlatformConsole v-if="operatorConsole" />
  <div v-else-if="checking" class="boot"><span class="spinner" /></div>
  <LoginForm
    v-else-if="!username || openingReset"
    @authenticated="onAuthenticated"
    @leave-reset="openingReset = false"
    @password-changed="onPasswordChanged"
  />
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
      <ImportTab v-else-if="activeTab === 'import'" />
      <PromptTab v-else-if="activeTab === 'prompt'" />
      <WidgetTab v-else />
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

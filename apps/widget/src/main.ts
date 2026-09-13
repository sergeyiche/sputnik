import { createApp } from 'vue'
import FloatingChat from './components/FloatingChat.vue'

const root = document.getElementById('app')
if (root) {
  createApp(FloatingChat).mount(root)
}

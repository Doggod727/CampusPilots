import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import { createPinia } from 'pinia'
import { createApp } from 'vue'

import App from './App.vue'
import { configureHttpClient } from './api/client'
import { installNoPersistenceGuard } from './app/bootstrap/noPersistence'
import { mountWithReadinessGate } from './app/bootstrap/readinessGate'
import { installSessionExpiryGuard } from './app/bootstrap/sessionExpiry'
import { router } from './app/router'
import './design/index.css'

installNoPersistenceGuard()
configureHttpClient()
installSessionExpiryGuard()

// A tab left open across a deployment may still reference old hashed chunks.
// Vite emits this event when a lazy route chunk can no longer be fetched.
window.addEventListener('vite:preloadError', (event) => {
  event.preventDefault()
  window.location.reload()
})

void mountWithReadinessGate(() => {
  const app = createApp(App)
  app.use(createPinia())
  app.use(router)
  app.use(ElementPlus)
  app.mount('#app')
})

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

void mountWithReadinessGate(() => {
  const app = createApp(App)
  app.use(createPinia())
  app.use(router)
  app.use(ElementPlus)
  app.mount('#app')
})

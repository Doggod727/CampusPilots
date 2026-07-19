import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '@/api/client'
import { useAuthStore } from '@/modules/auth/stores/auth'
import LoginPage from '@/modules/auth/LoginPage.vue'

function setup() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: { template: '<p>home</p>' } },
      { path: '/login', component: LoginPage },
      { path: '/dashboard', component: { template: '<p>dashboard</p>' } },
    ],
  })
  const wrapper = mount(LoginPage, {
    global: { plugins: [router] },
  })
  return { wrapper, router }
}

describe('LoginPage', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('shows the stable error for invalid credentials and clears the password', async () => {
    setActivePinia(createPinia())
    const store = useAuthStore()
    store.login = vi.fn().mockRejectedValue(new ApiError(401, 'INVALID_CREDENTIALS', '登录状态无效，请重新登录'))
    const { wrapper } = setup()
    await wrapper.find('#login-username').setValue('student01')
    await wrapper.find('#login-password').setValue('wrong-pass')
    await wrapper.find('form').trigger('submit.prevent')
    await wrapper.vm.$nextTick()
    expect(wrapper.text()).toContain('用户名或密码不正确')
    expect((wrapper.find('#login-password').element as HTMLInputElement).value).toBe('')
  })

  it('navigates to the redirect target on success', async () => {
    setActivePinia(createPinia())
    const store = useAuthStore()
    store.login = vi.fn().mockResolvedValue(undefined)
    const { wrapper, router } = setup()
    await router.replace({ path: '/login', query: { redirect: '/dashboard' } })
    await wrapper.find('#login-username').setValue('student01')
    await wrapper.find('#login-password').setValue('secret')
    await wrapper.find('form').trigger('submit.prevent')
    await vi.waitFor(() => expect(router.currentRoute.value.path).toBe('/dashboard'))
  })
})

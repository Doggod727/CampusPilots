import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import ForbiddenView from '@/app/router/ForbiddenView.vue'

const replace = vi.fn()

vi.mock('vue-router', () => ({
  useRouter: () => ({ replace }),
}))

describe('ForbiddenView', () => {
  beforeEach(() => {
    replace.mockReset()
  })

  it('returns to the authenticated chat entry', async () => {
    const wrapper = mount(ForbiddenView)

    await wrapper.get('button').trigger('click')

    expect(replace).toHaveBeenCalledOnce()
    expect(replace).toHaveBeenCalledWith({ name: 'chat' })
  })
})

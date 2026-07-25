import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import HomeView from '@/app/router/HomeView.vue'

describe('App scaffold', () => {
  it('renders the product name', () => {
    const wrapper = mount(HomeView)
    expect(wrapper.text()).toContain('CampusPilot')
  })
})

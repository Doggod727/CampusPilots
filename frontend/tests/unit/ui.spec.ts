import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import {
  EmptyState,
  ErrorState,
  StatusBadge,
  UiButton,
  UiCard,
  UiPagination,
  UiSkeleton,
} from '@/shared/ui'

describe('UiButton', () => {
  it('renders slot and variant class', () => {
    const wrapper = mount(UiButton, { props: { variant: 'primary' }, slots: { default: '提交' } })
    expect(wrapper.text()).toContain('提交')
    expect(wrapper.classes()).toContain('ui-button--primary')
  })

  it('is disabled while loading with aria-busy', () => {
    const wrapper = mount(UiButton, { props: { loading: true } })
    expect(wrapper.attributes('disabled')).toBeDefined()
    expect(wrapper.attributes('aria-busy')).toBe('true')
  })
})

describe('UiCard', () => {
  it('renders slot content with surface style', () => {
    const wrapper = mount(UiCard, { slots: { default: '<p>内容</p>' } })
    expect(wrapper.html()).toContain('内容')
    expect(wrapper.classes()).toContain('ui-card--md')
  })
})

describe('StatusBadge', () => {
  it('maps known status to localized label and tone', () => {
    const wrapper = mount(StatusBadge, { props: { status: 'awaiting_approval' } })
    expect(wrapper.text()).toContain('待审批')
    expect(wrapper.classes()).toContain('status-badge--warning')
  })

  it('falls back to raw status for unknown values', () => {
    const wrapper = mount(StatusBadge, { props: { status: 'custom_state' } })
    expect(wrapper.text()).toContain('custom_state')
  })

  it('updates the localized label and tone when status changes', async () => {
    const wrapper = mount(StatusBadge, { props: { status: 'created' } })
    expect(wrapper.text()).toContain('已创建')
    await wrapper.setProps({ status: 'succeeded' })
    expect(wrapper.text()).toContain('成功')
    expect(wrapper.classes()).toContain('status-badge--success')
  })
})

describe('EmptyState/ErrorState', () => {
  it('renders empty description', () => {
    const wrapper = mount(EmptyState, { props: { title: '暂无数据', description: '稍后再来' } })
    expect(wrapper.text()).toContain('暂无数据')
    expect(wrapper.text()).toContain('稍后再来')
  })

  it('emits retry from error state', async () => {
    const wrapper = mount(ErrorState, { props: { title: '加载失败' } })
    await wrapper.find('button').trigger('click')
    expect(wrapper.emitted('retry')).toBeTruthy()
  })
})

describe('UiPagination', () => {
  it('emits change on page click', async () => {
    const wrapper = mount(UiPagination, { props: { page: 1, total: 50, pageSize: 10 } })
    const pages = wrapper.findAll('.ui-pagination__page')
    await pages[1].trigger('click')
    expect(wrapper.emitted('change')?.[0]).toEqual([2])
  })
})

describe('UiSkeleton', () => {
  it('renders requested line count', () => {
    const wrapper = mount(UiSkeleton, { props: { lines: 4 } })
    expect(wrapper.findAll('.ui-skeleton__line')).toHaveLength(4)
  })
})

import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import type { AgentRunEvent } from '@/api/stream/agentStream'
import AgentTimeline from '@/modules/agent-workbench/AgentTimeline.vue'

const EVENTS: AgentRunEvent[] = [
  { sequence: 1, event: 'route', data: { target_agent: 'knowledge', confidence: '0.95' } },
  { sequence: 2, event: 'agent_step', data: { agent_code: 'knowledge_agent', status: 'running' } },
  { sequence: 3, event: 'tool_call', data: { tool_name: 'knowledge.search' } },
  { sequence: 4, event: 'approval_required', data: { tool_name: 'electricity.create_topup_request' } },
  { sequence: 5, event: 'done', data: { status: 'succeeded' } },
]

describe('AgentTimeline', () => {
  it('renders each event once with stage labels and pastel tones', () => {
    const wrapper = mount(AgentTimeline, { props: { events: EVENTS, live: false } })
    const items = wrapper.findAll('.timeline__item')
    expect(items).toHaveLength(5)
    expect(wrapper.text()).toContain('路由决策')
    expect(wrapper.text()).toContain('需要审批')
    expect(wrapper.text()).toContain('完成')
    expect(items[0].classes()).toContain('timeline__item--thinking')
    expect(items[2].classes()).toContain('timeline__item--edit')
    expect(items[3].classes()).toContain('timeline__item--done')
    expect(items[4].classes()).toContain('timeline__item--done')
  })

  it('shows the live indicator only while streaming', () => {
    const wrapper = mount(AgentTimeline, { props: { events: [], live: true } })
    expect(wrapper.text()).toContain('实时更新中')
    expect(wrapper.text()).toContain('等待运行事件')
    const stopped = mount(AgentTimeline, { props: { events: [], live: false } })
    expect(stopped.text()).not.toContain('实时更新中')
  })
})

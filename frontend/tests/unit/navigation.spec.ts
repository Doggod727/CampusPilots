import { describe, expect, it } from 'vitest'

import { filteredNav, NAV_ITEMS } from '@/app/router/navigation'

const ROLE_PERMISSIONS: Record<string, string[]> = {
  student: [
    'chat:use', 'service:read', 'work_order:create', 'work_order:read',
    'electricity:read_own', 'electricity:topup_request:create',
    'community:read', 'community:write', 'knowledge:read',
    'agent:run', 'agent:run:read_own', 'agent:catalog:read', 'tool:catalog:read',
  ],
  service_staff: ['service:read', 'work_order:read', 'work_order:transition', 'community:read', 'dashboard:read'],
  knowledge_admin: ['chat:use', 'knowledge:read', 'knowledge:read_all', 'knowledge:write', 'knowledge:write_all', 'knowledge:publish', 'config:read', 'dashboard:read'],
  community_operator: ['community:read', 'community:write', 'community:moderate', 'moderation:read', 'moderation:decide', 'community:anonymous_identity:read', 'dashboard:read'],
  super_admin: NAV_ITEMS.flatMap((item) => [...item.permissions]),
}

function names(permissions: string[]): string[] {
  const set = new Set(permissions)
  return filteredNav((code) => set.has(code)).flatMap((group) => group.items.map((item) => item.name))
}

describe('permission-driven navigation', () => {
  it('gives students only self-service entries', () => {
    const visible = names(ROLE_PERMISSIONS.student)
    expect(visible).toContain('chat')
    expect(visible).toContain('services')
    expect(visible).toContain('electricity')
    expect(visible).toContain('agent-runs')
    expect(visible).not.toContain('work-orders-handle')
    expect(visible).not.toContain('admin-users')
    expect(visible).not.toContain('datasets')
  })

  it('gives service staff work-order handling without admin entries', () => {
    const visible = names(ROLE_PERMISSIONS.service_staff)
    expect(visible).toContain('work-orders-handle')
    expect(visible).not.toContain('admin-users')
    expect(visible).not.toContain('knowledge-ingestion')
  })

  it('gives knowledge admin the knowledge group', () => {
    const visible = names(ROLE_PERMISSIONS.knowledge_admin)
    expect(visible).toContain('knowledge-bases')
    expect(visible).toContain('knowledge-ingestion')
    expect(visible).toContain('admin-config')
    expect(visible).not.toContain('admin-users')
  })

  it('gives community operator moderation entries', () => {
    const visible = names(ROLE_PERMISSIONS.community_operator)
    expect(visible).toContain('moderation-cases')
    expect(visible).toContain('community-topics')
    expect(visible).not.toContain('admin-users')
  })

  it('gives super admin every declared entry', () => {
    expect(names(ROLE_PERMISSIONS.super_admin)).toHaveLength(NAV_ITEMS.length)
  })

  it('keeps groups empty-free when nothing is visible', () => {
    expect(filteredNav(() => false)).toEqual([])
  })
})

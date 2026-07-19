import { defineStore } from 'pinia'

import { callApi } from '@/api/client'
import { listAgents, listTools } from '@/api/generated'
import type { AgentCatalogItem, ToolCatalogItem } from '@/api/generated'

interface CatalogState {
  agents: AgentCatalogItem[]
  tools: ToolCatalogItem[]
  loading: boolean
  failed: boolean
  loaded: boolean
}

/** Agent/Tool 目录：能力与风险全部来自后端目录，不硬编码。 */
export const useAgentCatalogStore = defineStore('agent-catalog', {
  state: (): CatalogState => ({
    agents: [],
    tools: [],
    loading: false,
    failed: false,
    loaded: false,
  }),
  actions: {
    async load(force = false): Promise<void> {
      if (this.loading || (this.loaded && !force)) {
        return
      }
      this.loading = true
      this.failed = false
      try {
        const [agents, tools] = await Promise.all([callApi(() => listAgents({})), callApi(() => listTools({}))])
        this.agents = agents.data.items
        this.tools = tools.data.items
        this.loaded = true
      } catch {
        this.failed = true
      } finally {
        this.loading = false
      }
    },
    toolByName(name: string): ToolCatalogItem | undefined {
      return this.tools.find((tool) => tool.name === name)
    },
  },
})

<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import { ApiError, callApi } from '@/api/client'
import {
  cancelAgentRun,
  createAgentRun,
  createChatCompletion,
  createConversation,
  createMessageFeedback,
  deleteConversation,
  getAgentRun,
  getMessage,
  listAgentRuns,
  listConversationMessages,
  listConversations,
  listKnowledgeBases,
} from '@/api/generated'
import type {
  AgentCatalogItem,
  AgentRunDetailData,
  Citation,
  Conversation,
  KnowledgeBase,
  Message,
  ToolCatalogItem,
} from '@/api/generated'
import { lastSequenceOf, streamAgentRun, type AgentRunEvent } from '@/api/stream/agentStream'
import { streamChatCompletion } from '@/api/stream/chatStream'
import { filteredNav } from '@/app/router/navigation'
import AgentTimeline from '@/modules/agent-workbench/AgentTimeline.vue'
import ApprovalCards from '@/modules/agent-workbench/ApprovalCards.vue'
import { useAgentCatalogStore } from '@/modules/agent-workbench/stores/catalog'
import { useAuthStore } from '@/modules/auth/stores/auth'
import { renderMarkdown } from '@/shared/lib/markdown'
import { EmptyState, ErrorState, StatusBadge, UiButton, UiCard, UiSkeleton } from '@/shared/ui'

const CONV_PAGE_SIZE = 20
const MSG_PAGE_SIZE = 20
const MAX_KB_SELECTION = 10
const auth = useAuthStore()
const router = useRouter()
const agentCatalog = useAgentCatalogStore()
const canDebugAgents = computed(
  () => auth.hasPermission('model:read') || auth.hasPermission('tool:catalog:write'),
)
const sidebarOpen = ref(false)
const expandedModule = ref<string | null>(null)
const accountMenuOpen = ref(false)
const accountMenuNotice = ref('')
const accountMenuEl = ref<HTMLElement | null>(null)

const userInitials = computed(() =>
  (auth.user?.display_name ?? auth.user?.username ?? '用户').slice(0, 1),
)
const userName = computed(() => auth.user?.display_name ?? auth.user?.username ?? '当前用户')
const userRole = computed(() => auth.user?.roles.map((role) => role.name).join(' · ') || '已登录')
const moduleGroups = computed(() =>
  filteredNav((code) => auth.hasPermission(code))
    .map((group) => {
      if (group.title !== 'Agent') return group
      if (!canDebugAgents.value) return { ...group, items: [] }
      const canInspectAllRuns = auth.hasPermission('agent:run:read_all')
      return {
        title: '能力中心',
        items: group.items
          .filter((item) => item.name !== 'agent-runs' || canInspectAllRuns)
          .map((item) => ({
            ...item,
            title:
              item.name === 'agent-catalog'
                ? '可用 Agent'
                : item.name === 'tool-catalog'
                  ? '可用工具'
                  : item.title,
          })),
      }
    })
    .filter((group) => group.items.length > 0),
)

function toggleModule(title: string) {
  expandedModule.value = expandedModule.value === title ? null : title
}

function closeAccountMenu() {
  accountMenuOpen.value = false
  accountMenuNotice.value = ''
}

function toggleAccountMenu() {
  accountMenuOpen.value = !accountMenuOpen.value
  accountMenuNotice.value = ''
}

function handleDocumentClick(event: MouseEvent) {
  if (accountMenuEl.value && !accountMenuEl.value.contains(event.target as Node)) {
    closeAccountMenu()
  }
}

async function logout() {
  closeAccountMenu()
  await auth.logout()
  await router.replace({ name: 'login' })
}

const canUseAgent = computed(
  () => auth.hasPermission('agent:run') && auth.hasPermission('agent:run:read_own'),
)

const MESSAGE_STATUS_LABELS: Record<Message['status'], string> = {
  pending: '待处理',
  streaming: '生成中',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
  fallback: '兜底回答',
}

function nowIso(): string {
  return new Date().toISOString()
}

function formatTime(value: string): string {
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

function describeChatError(error: unknown, fallback: string): string {
  if (error instanceof ApiError) {
    if (error.status === 403) {
      return '权限不足：需要问答权限（chat:use）且知识库在可访问范围内。'
    }
    if (error.status === 404) {
      return '会话或消息不存在、或已被删除。'
    }
    if (error.status === 409) {
      return '请求冲突：相同请求已处理或会话状态已变化，请刷新后重试。'
    }
    if (error.status === 422) {
      return error.details[0]?.reason ?? '输入校验失败：问题需 1–2000 字，知识库需选择 1–10 个。'
    }
    if (error.status === 429) {
      return '请求过于频繁，请稍后再试。'
    }
    if (error.status === 502 || error.status === 504) {
      return '模型服务暂不可用或响应超时，请稍后重试。'
    }
  }
  return fallback
}

/* ---------- 会话栏 ---------- */

const conversations = ref<Conversation[]>([])
const conversationsTotal = ref(0)
const conversationsPage = ref(0)
const conversationsLoading = ref(true)
const conversationsFailed = ref(false)
const conversationError = ref('')

const hasMoreConversations = computed(() => conversations.value.length < conversationsTotal.value)

async function loadConversations() {
  conversationsLoading.value = true
  conversationsFailed.value = false
  try {
    const response = await callApi(() =>
      listConversations({ query: { page: 1, page_size: CONV_PAGE_SIZE } }),
    )
    conversations.value = response.data.items
    conversationsTotal.value = response.data.pagination.total
    conversationsPage.value = 1
  } catch {
    conversationsFailed.value = true
  } finally {
    conversationsLoading.value = false
  }
}

async function loadMoreConversations() {
  const next = conversationsPage.value + 1
  try {
    const response = await callApi(() =>
      listConversations({ query: { page: next, page_size: CONV_PAGE_SIZE } }),
    )
    conversations.value = [...conversations.value, ...response.data.items]
    conversationsTotal.value = response.data.pagination.total
    conversationsPage.value = next
  } catch (error) {
    conversationError.value = describeChatError(error, '更多会话加载失败。')
  }
}

const creatingConversation = ref(false)
/** 同一次创建会话固定幂等键：失败重试复用，成功后更换。 */
const conversationKey = ref(crypto.randomUUID())

async function createNewConversation() {
  if (creatingConversation.value) {
    return
  }
  creatingConversation.value = true
  conversationError.value = ''
  try {
    const response = await callApi(() =>
      createConversation({ body: {}, headers: { 'Idempotency-Key': conversationKey.value } }),
    )
    conversationKey.value = crypto.randomUUID()
    conversations.value = [response.data, ...conversations.value]
    conversationsTotal.value += 1
    clearAgentSelection()
    await selectConversation(response.data.id)
  } catch (error) {
    conversationError.value = describeChatError(error, '创建会话失败，请稍后重试。')
  } finally {
    creatingConversation.value = false
  }
}

async function ensureActiveConversation(): Promise<string> {
  if (activeId.value) return activeId.value
  const response = await callApi(() =>
    createConversation({ body: {}, headers: { 'Idempotency-Key': conversationKey.value } }),
  )
  conversationKey.value = crypto.randomUUID()
  conversations.value = [response.data, ...conversations.value]
  conversationsTotal.value += 1
  activeId.value = response.data.id
  messages.value = []
  return response.data.id
}

const confirmingDeleteId = ref<string | null>(null)
const deletingConversation = ref(false)

async function confirmDeleteConversation(item: Conversation) {
  if (deletingConversation.value) {
    return
  }
  deletingConversation.value = true
  conversationError.value = ''
  try {
    await callApi(() => deleteConversation({ path: { conversation_id: item.id } }))
    confirmingDeleteId.value = null
    conversations.value = conversations.value.filter((entry) => entry.id !== item.id)
    conversationsTotal.value = Math.max(0, conversationsTotal.value - 1)
    if (activeId.value === item.id) {
      activeId.value = null
      messages.value = []
      citationsFor.value = null
    }
  } catch (error) {
    conversationError.value = describeChatError(error, '删除会话失败，请稍后重试。')
  } finally {
    deletingConversation.value = false
  }
}

/* ---------- 消息区 ---------- */

const activeId = ref<string | null>(null)
const activeConversation = computed(
  () => conversations.value.find((item) => item.id === activeId.value) ?? null,
)
const messages = ref<Message[]>([])
const earliestPage = ref(1)
const messagesLoading = ref(false)
const messagesFailed = ref(false)
const loadingEarlier = ref(false)
const messageListEl = ref<HTMLElement | null>(null)

const hasEarlier = computed(() => earliestPage.value > 1)

async function scrollToBottom() {
  await nextTick()
  const el = messageListEl.value
  if (el) {
    el.scrollTop = el.scrollHeight
  }
}

async function loadMessages() {
  if (!activeId.value) {
    return
  }
  messagesLoading.value = true
  messagesFailed.value = false
  const conversationId = activeId.value
  try {
    const first = await callApi(() =>
      listConversationMessages({
        path: { conversation_id: conversationId },
        query: { page: 1, page_size: MSG_PAGE_SIZE },
      }),
    )
    const total = first.data.pagination.total
    const lastPage = Math.max(1, Math.ceil(total / MSG_PAGE_SIZE))
    if (lastPage === 1) {
      messages.value = first.data.items
      earliestPage.value = 1
    } else {
      // 消息按时间升序分页：首屏直接取最后一页（最新），向上翻页加载更早历史
      const last = await callApi(() =>
        listConversationMessages({
          path: { conversation_id: conversationId },
          query: { page: lastPage, page_size: MSG_PAGE_SIZE },
        }),
      )
      messages.value = last.data.items
      earliestPage.value = lastPage
    }
    await scrollToBottom()
  } catch {
    messagesFailed.value = true
  } finally {
    messagesLoading.value = false
  }
}

async function loadEarlier() {
  if (!activeId.value || !hasEarlier.value || loadingEarlier.value) {
    return
  }
  loadingEarlier.value = true
  try {
    const response = await callApi(() =>
      listConversationMessages({
        path: { conversation_id: activeId.value as string },
        query: { page: earliestPage.value - 1, page_size: MSG_PAGE_SIZE },
      }),
    )
    messages.value = [...response.data.items, ...messages.value]
    earliestPage.value -= 1
  } catch (error) {
    conversationError.value = describeChatError(error, '历史消息加载失败。')
  } finally {
    loadingEarlier.value = false
  }
}

async function selectConversation(id: string) {
  if (activeId.value !== id) {
    clearAgentSelection()
  }
  if (streaming.value) {
    stopStream()
  }
  stopAgentStream()
  activeAgentRunId.value = null
  agentDetails.value = []
  agentEvents.value = []
  citationsFor.value = null
  activeId.value = id
  sidebarOpen.value = false
  await Promise.all([loadMessages(), loadConversationAgentRuns(id)])
}

/* ---------- 知识库范围（后端要求 1–10 个，默认全选可访问库） ---------- */

const kbPanelOpen = ref(false)
const kbs = ref<KnowledgeBase[]>([])
const kbError = ref('')
const kbNotice = ref('')
const selectedKbIds = ref<string[]>([])

async function loadKbScope() {
  try {
    const response = await callApi(() => listKnowledgeBases({ query: { page: 1, page_size: 50 } }))
    kbs.value = response.data.items
    selectedKbIds.value = response.data.items.slice(0, MAX_KB_SELECTION).map((kb) => kb.id)
  } catch (error) {
    kbError.value =
      error instanceof ApiError && error.status === 403
        ? '无法加载知识库列表（需要 knowledge:read 权限），暂不能发起问答。'
        : '知识库列表加载失败，请稍后刷新重试。'
  }
}

function toggleKb(id: string) {
  kbNotice.value = ''
  if (selectedKbIds.value.includes(id)) {
    selectedKbIds.value = selectedKbIds.value.filter((value) => value !== id)
  } else if (selectedKbIds.value.length >= MAX_KB_SELECTION) {
    kbNotice.value = `一次问答最多选择 ${MAX_KB_SELECTION} 个知识库。`
  } else {
    selectedKbIds.value = [...selectedKbIds.value, id]
  }
}

/* ---------- Agent / Tool 显式调用（目录与执行均来自 M5 后端） ---------- */

type SelectionPanel = 'agents' | 'tools' | null
type SpecialistAgentCode =
  'knowledge_agent' | 'service_agent' | 'community_agent' | 'governance_agent' | 'modelops_agent'
const SPECIALIST_AGENT_CODES = new Set<string>([
  'knowledge_agent',
  'service_agent',
  'community_agent',
  'governance_agent',
  'modelops_agent',
])
const selectionPanel = ref<SelectionPanel>(null)
const selectedAgentCodes = ref<SpecialistAgentCode[]>([])
const selectedToolNames = ref<string[]>([])
const selectionNotice = ref('')
const activeAgentRunId = ref<string | null>(null)
const agentDetails = ref<AgentRunDetailData[]>([])
const agentEvents = ref<AgentRunEvent[]>([])
const agentSubmissionBlocked = computed(() => {
  const active = agentDetails.value.find((detail) => detail.run.id === activeAgentRunId.value)
  return !!active && ['created', 'routing', 'running', 'awaiting_approval'].includes(active.run.status)
})
const agentStreaming = ref(false)
const agentError = ref('')
let agentController: AbortController | null = null

const pendingApprovalCount = computed(() =>
  agentDetails.value.reduce(
    (total, detail) =>
      total + detail.approvals.filter((approval) => approval.status === 'pending').length,
    0,
  ),
)

async function focusPendingApproval() {
  accountMenuOpen.value = true
  if (pendingApprovalCount.value === 0) {
    accountMenuNotice.value = activeId.value
      ? '当前对话没有待审批事项。'
      : '请先打开一段对话查看审批事项。'
    return
  }
  closeAccountMenu()
  sidebarOpen.value = false
  await nextTick()
  messageListEl.value
    ?.querySelector<HTMLElement>('[data-pending-approval="true"]')
    ?.scrollIntoView({ behavior: 'smooth', block: 'center' })
}

function agentAnswer(detail: AgentRunDetailData): string | null {
  if (!detail) return null
  if (detail.run.final_answer) return detail.run.final_answer
  const completed = [...detail.steps]
    .reverse()
    .find((step) => step.status === 'succeeded' || step.status === 'partial')
  if (!completed?.output_summary || Object.keys(completed.output_summary).length === 0) return null
  const direct = completed.output_summary.answer ?? completed.output_summary.final_answer
  if (typeof direct === 'string' && direct.trim()) return direct
  const missingSlots = completed.output_summary.missing_slots
  if (Array.isArray(missingSlots) && missingSlots.length > 0) {
    const labels: Record<string, string> = {
      item_type: '发布类型（失物或拾物）',
      title: '物品名称',
      category: '物品分类',
      location: '地点',
      occurred_at: '发生时间',
      description: '详细描述',
      room_id: '房间号',
    }
    return `请继续补充必要信息：${missingSlots.map((slot) => labels[String(slot)] ?? String(slot)).join('、')}`
  }
  return null
}

type AgentConversationTurn = {
  key: string
  user: string | null
  answer: string | null
  isLast: boolean
}

function agentConversationTurns(detail: AgentRunDetailData): AgentConversationTurn[] {
  const relevantSteps = detail.steps.filter((step) => {
    const value = step.output_summary?.answer ?? step.output_summary?.final_answer
    return (
      (typeof value === 'string' && value.trim().length > 0) ||
      typeof step.input_summary?.continuation_input === 'string'
    )
  })
  const hasContinuation = relevantSteps.some(
    (step) => typeof step.input_summary?.continuation_input === 'string',
  )
  if (!hasContinuation) {
    return [{
      key: `${detail.run.id}:aggregate`,
      user: detail.run.input_summary,
      answer: agentAnswer(detail),
      isLast: true,
    }]
  }
  return relevantSteps.map((step, index) => {
    const output = step.output_summary ?? {}
    const value = output.answer ?? output.final_answer
    const continuation = step.input_summary?.continuation_input
    return {
      key: `${detail.run.id}:${step.id}`,
      user: index === 0
        ? detail.run.input_summary
        : typeof continuation === 'string' && continuation.trim()
          ? continuation
          : null,
      answer: typeof value === 'string' ? value : null,
      isLast: index === relevantSteps.length - 1,
    }
  })
}

type ConversationTimelineEntry =
  | { kind: 'message'; key: string; at: string; message: Message }
  | { kind: 'agent'; key: string; at: string; detail: AgentRunDetailData }

const conversationTimeline = computed<ConversationTimelineEntry[]>(() =>
  [
    ...messages.value.map((message) => ({
      kind: 'message' as const,
      key: `message:${message.id}`,
      at: message.created_at,
      message,
    })),
    ...agentDetails.value.map((detail) => ({
      kind: 'agent' as const,
      key: `agent:${detail.run.id}`,
      at: detail.run.created_at,
      detail,
    })),
  ].sort((left, right) => left.at.localeCompare(right.at) || left.key.localeCompare(right.key)),
)

type SpecialistCatalogItem = AgentCatalogItem & { code: SpecialistAgentCode }
const enabledAgents = computed<SpecialistCatalogItem[]>(() =>
  agentCatalog.agents.filter(
    (item): item is SpecialistCatalogItem => item.enabled && SPECIALIST_AGENT_CODES.has(item.code),
  ),
)
const enabledTools = computed(() => agentCatalog.tools.filter((item) => item.enabled))
const agentSelectionActive = computed(
  () => selectedAgentCodes.value.length > 0 || selectedToolNames.value.length > 0,
)
const selectedAgents = computed(() =>
  selectedAgentCodes.value
    .map((code) => enabledAgents.value.find((item) => item.code === code))
    .filter((item): item is SpecialistCatalogItem => item !== undefined),
)
const visibleTools = computed(() => {
  if (selectedAgentCodes.value.length === 0) {
    return enabledTools.value
  }
  const allowed = new Set(selectedAgents.value.flatMap((agent) => agent.tool_allowlist))
  return enabledTools.value.filter((tool) => allowed.has(tool.name))
})

function openSelectionPanel(panel: Exclude<SelectionPanel, null>) {
  selectionNotice.value = ''
  selectionPanel.value = panel
  if (!agentCatalog.loaded && !agentCatalog.loading) {
    void agentCatalog.load()
  }
}

function toggleAgent(code: string) {
  selectionNotice.value = ''
  if (!SPECIALIST_AGENT_CODES.has(code)) {
    return
  }
  const specialistCode = code as SpecialistAgentCode
  if (selectedAgentCodes.value.includes(specialistCode)) {
    const remaining = selectedAgentCodes.value.filter((value) => value !== specialistCode)
    selectedAgentCodes.value = remaining
    const allowed = new Set(
      enabledAgents.value
        .filter((agent) => remaining.includes(agent.code))
        .flatMap((agent) => agent.tool_allowlist),
    )
    selectedToolNames.value = selectedToolNames.value.filter((name) => allowed.has(name))
    return
  }
  if (selectedAgentCodes.value.length >= 3) {
    selectionNotice.value = '一次运行最多显式选择 3 个 Agent。'
    return
  }
  selectedAgentCodes.value = [...selectedAgentCodes.value, specialistCode]
}

function toggleTool(tool: ToolCatalogItem) {
  selectionNotice.value = ''
  if (selectedToolNames.value.includes(tool.name)) {
    selectedToolNames.value = selectedToolNames.value.filter((name) => name !== tool.name)
    return
  }
  const owner = enabledAgents.value.find((agent) => agent.tool_allowlist.includes(tool.name))
  if (!owner) {
    selectionNotice.value = '当前没有可用 Agent 能调用该 Tool。'
    return
  }
  if (!selectedAgentCodes.value.includes(owner.code)) {
    if (selectedAgentCodes.value.length >= 3) {
      selectionNotice.value = '该 Tool 需要额外 Agent，但已达 3 个 Agent 上限。'
      return
    }
    selectedAgentCodes.value = [...selectedAgentCodes.value, owner.code as SpecialistAgentCode]
  }
  selectedToolNames.value = [...selectedToolNames.value, tool.name]
}

function clearAgentSelection() {
  selectedAgentCodes.value = []
  selectedToolNames.value = []
  selectionPanel.value = null
  selectionNotice.value = ''
}

async function loadConversationAgentRuns(conversationId: string) {
  if (!canUseAgent.value) {
    agentDetails.value = []
    return
  }
  try {
    const response = await callApi(() =>
      listAgentRuns({ query: { page: 1, page_size: 100, conversation_id: conversationId } }),
    )
    agentDetails.value = await Promise.all(
      response.data.items.map(async (run) => {
        const detail = await callApi(() => getAgentRun({ path: { run_id: run.id } }))
        return detail.data
      }),
    )
    const running = agentDetails.value.find((detail) =>
      ['created', 'routing', 'running', 'awaiting_approval'].includes(detail.run.status),
    )
    if (running && !activeAgentRunId.value) {
      activeAgentRunId.value = running.run.id
      agentEvents.value = []
      void followAgentRun(running.run.id)
    }
  } catch {
    agentDetails.value = []
  }
}

function stopAgentStream() {
  agentController?.abort()
  agentController = null
  agentStreaming.value = false
}

async function loadAgentDetail(runId: string): Promise<AgentRunDetailData> {
  const response = await callApi(() => getAgentRun({ path: { run_id: runId } }))
  const index = agentDetails.value.findIndex((item) => item.run.id === runId)
  agentDetails.value =
    index < 0
      ? [...agentDetails.value, response.data]
      : agentDetails.value.map((item, itemIndex) => (itemIndex === index ? response.data : item))
  return response.data
}

function pushAgentEvent(runId: string, event: AgentRunEvent) {
  if (activeAgentRunId.value === runId && !agentEvents.value.some((item) => item.sequence === event.sequence)) {
    agentEvents.value = [...agentEvents.value, event].sort((a, b) => a.sequence - b.sequence)
  }
  if (event.event !== 'approval_required') {
    return
  }
  void loadAgentDetail(runId)
}

async function cancelActiveAgentRun() {
  const runId = activeAgentRunId.value
  if (!runId) return
  try {
    await callApi(() =>
      cancelAgentRun({
        path: { run_id: runId },
        headers: { 'Idempotency-Key': crypto.randomUUID() },
      }),
    )
    stopAgentStream()
    await loadAgentDetail(runId)
    if (activeId.value) await loadConversationAgentRuns(activeId.value)
  } catch (error) {
    agentError.value = describeAgentError(error)
  }
}

async function followAgentRun(runId: string, lastEventId?: number) {
  stopAgentStream()
  const streamController = new AbortController()
  agentController = streamController
  agentStreaming.value = true
  let streamInterrupted = false
  try {
    await streamAgentRun(
      runId,
      {
        onEvent: (event) => pushAgentEvent(runId, event),
        onDone: (event) => pushAgentEvent(runId, event),
        onError: (event) => pushAgentEvent(runId, event),
      },
      { signal: streamController.signal, lastEventId },
    )
  } catch {
    streamInterrupted = !streamController.signal.aborted
  } finally {
    let recovered: AgentRunDetailData | null = null
    if (activeAgentRunId.value === runId) {
      try {
        recovered = await loadAgentDetail(runId)
      } catch {
        if (agentController === streamController) {
          agentError.value = 'Agent 运行详情加载失败。'
        }
      }
    }
    // A newer stream has replaced this one.  The old stream must not clear or
    // report errors for the new stream.
    if (agentController !== streamController) return
    agentController = null
    agentStreaming.value = false
    if (activeId.value) await loadConversationAgentRuns(activeId.value)
    await scrollToBottom()
    if (
      streamInterrupted &&
      recovered &&
      ['created', 'routing', 'running'].includes(recovered.run.status)
    ) {
      void pollAgentRun(runId)
    }
  }
}

async function handleAgentApprovalDecided(runId: string) {
  activeAgentRunId.value = runId
  await loadAgentDetail(runId)
  void followAgentRun(runId)
}

function describeAgentError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 403) return '当前账号没有 Agent 运行权限。'
    if (error.status === 404) return 'Agent 运行不存在或无权访问。'
    if (error.status === 409) return 'Agent 或 Tool 当前不可用，或请求已处理。'
    if (error.status === 422) return error.details[0]?.reason ?? 'Agent 运行参数无效。'
    if (error.status === 429) return '已达 Agent 运行频率上限，请稍后再试。'
  }
  return 'Agent 服务暂不可用，请稍后重试。'
}

/* ---------- 发送（同步 / 流式） ---------- */

const draft = ref('')
type ChatMode = 'stream' | 'sync'
type AnswerMode = 'auto' | 'library' | 'learn'
const mode = ref<ChatMode>('stream')
const answerMode = ref<AnswerMode>('auto')
const composerInputEl = ref<HTMLTextAreaElement | null>(null)
const commandIndex = ref(0)
const sending = ref(false)
const streaming = ref(false)
const sendError = ref('')
const streamNotice = ref('')
/** 同步模式：同一条草稿固定幂等键，失败重试复用，成功后更换。 */
const draftKey = ref(crypto.randomUUID())
let controller: AbortController | null = null

type ChatCommand = {
  command: '/stream' | '/sync' | '/auto' | '/lib' | '/learn' | '/agent' | '/tool'
  label: string
  description: string
  mode?: ChatMode
}

const COMMAND_OPTIONS: ReadonlyArray<ChatCommand> = [
  { command: '/stream', mode: 'stream', label: '流式回答', description: '边生成边显示（默认）' },
  { command: '/sync', mode: 'sync', label: '完整回答', description: '生成完成后一次显示' },
  { command: '/auto', label: '智能助手', description: '由 Supervisor 自动路由并执行任务' },
  { command: '/lib', label: '选择知识库', description: '设置本次问答检索范围' },
  { command: '/learn', label: '学习辅导', description: '可选课程资料；无资料时使用通用模型' },
  { command: '/agent', label: '调试 Agent', description: '高级账号显式选择最多 3 个 Agent 执行任务' },
  { command: '/tool', label: '调试 Tool', description: '高级账号限定本次允许调用的 Tool' },
]

const availableCommands = computed(() =>
  canDebugAgents.value
    ? COMMAND_OPTIONS
    : COMMAND_OPTIONS.filter((option) => !['/agent', '/tool'].includes(option.command)),
)

const slashMenuOpen = computed(() => /^\/[a-z]*$/i.test(draft.value.trim()))
const filteredCommands = computed(() => {
  const query = draft.value.trim().toLowerCase()
  return availableCommands.value.filter((option) => option.command.startsWith(query))
})
const exactCommand = computed(() =>
  availableCommands.value.find((option) => option.command === draft.value.trim().toLowerCase()),
)
const canSend = computed(
  () =>
    draft.value.trim().length > 0 &&
    !slashMenuOpen.value &&
    (answerMode.value !== 'library' || selectedKbIds.value.length > 0) &&
    !sending.value &&
    !streaming.value &&
    !agentStreaming.value &&
    !agentSubmissionBlocked.value,
)

watch(draft, () => {
  commandIndex.value = 0
})

async function applyCommand(option: ChatCommand) {
  if (option.mode) {
    mode.value = option.mode
  } else if (option.command === '/auto') {
    answerMode.value = 'auto'
    clearAgentSelection()
    kbPanelOpen.value = false
  } else if (option.command === '/lib') {
    answerMode.value = 'library'
    kbPanelOpen.value = true
    selectionPanel.value = null
  } else if (option.command === '/learn') {
    answerMode.value = 'learn'
    kbPanelOpen.value = true
    selectionPanel.value = null
  } else if (option.command === '/agent') {
    if (!canDebugAgents.value) return
    kbPanelOpen.value = false
    openSelectionPanel('agents')
  } else if (option.command === '/tool') {
    if (!canDebugAgents.value) return
    kbPanelOpen.value = false
    openSelectionPanel('tools')
  }
  draft.value = ''
  commandIndex.value = 0
  await nextTick()
  composerInputEl.value?.focus()
}

async function handleComposerKeydown(event: KeyboardEvent) {
  if (event.isComposing) {
    return
  }
  if (slashMenuOpen.value) {
    const options = filteredCommands.value
    if (event.key === 'ArrowDown' && options.length > 0) {
      event.preventDefault()
      commandIndex.value = (commandIndex.value + 1) % options.length
      return
    }
    if (event.key === 'ArrowUp' && options.length > 0) {
      event.preventDefault()
      commandIndex.value = (commandIndex.value - 1 + options.length) % options.length
      return
    }
    if (event.key === 'Escape') {
      event.preventDefault()
      draft.value = ''
      return
    }
    if (event.key === 'Enter') {
      event.preventDefault()
      const option = exactCommand.value ?? options[commandIndex.value]
      if (option) {
        await applyCommand(option)
      }
      return
    }
  }
  if (
    event.key === 'Enter' &&
    !event.shiftKey &&
    !event.ctrlKey &&
    !event.altKey &&
    !event.metaKey
  ) {
    event.preventDefault()
    await send()
    return
  }
}

function makeLocalUserMessage(question: string): Message {
  return {
    id: `local-${crypto.randomUUID()}`,
    conversation_id: activeId.value ?? '',
    sequence_no: 0,
    role: 'user',
    status: 'completed',
    content: question,
    request_id: '',
    citations: [],
    created_at: nowIso(),
    updated_at: nowIso(),
  }
}

async function send() {
  const question = draft.value.trim()
  if (!canSend.value) {
    return
  }
  sendError.value = ''
  streamNotice.value = ''
  if (answerMode.value === 'auto' || agentSelectionActive.value) {
    await sendAgent(question)
  } else if (mode.value === 'stream') {
    await sendStream(question)
  } else {
    await sendSync(question)
  }
}

const AGENT_MODE_BY_CODE: Readonly<
  Record<string, 'knowledge' | 'service' | 'community' | 'governance' | 'modelops'>
> = {
  knowledge_agent: 'knowledge',
  service_agent: 'service',
  community_agent: 'community',
  governance_agent: 'governance',
  modelops_agent: 'modelops',
}

async function sendAgent(question: string) {
  sending.value = true
  agentError.value = ''
  try {
    const conversationId = await ensureActiveConversation()
    const continuationRunId = agentDetails.value.find(
      (detail) => detail.run.id === activeAgentRunId.value && String(detail.run.status) === 'awaiting_input',
    )?.run.id
    const continuationCursor = continuationRunId ? lastSequenceOf(agentEvents.value) : undefined
    const firstAgent = selectedAgentCodes.value[0]
    const response = await callApi(() =>
      createAgentRun({
        body: {
          input: question,
          conversation_id: conversationId,
          mode: canDebugAgents.value && firstAgent ? AGENT_MODE_BY_CODE[firstAgent] : 'auto',
          context: canDebugAgents.value
            ? {
                requested_agent_codes: selectedAgentCodes.value,
                requested_tool_names: selectedToolNames.value,
              }
            : {},
        },
        headers: { 'Idempotency-Key': draftKey.value },
      }),
    )
    draft.value = ''
    draftKey.value = crypto.randomUUID()
    activeAgentRunId.value = response.data.id
    const isContinuation = continuationRunId === response.data.id
    if (isContinuation) {
      agentDetails.value = agentDetails.value.map((detail) =>
        detail.run.id === response.data.id ? { ...detail, run: response.data } : detail,
      )
    } else {
      agentDetails.value = [
        ...agentDetails.value,
        {
          run: response.data,
          steps: [],
          tool_calls: [],
          approvals: [],
        },
      ]
      agentEvents.value = []
    }
    selectionPanel.value = null
    await loadConversations()
    if (mode.value === 'stream') {
      void followAgentRun(response.data.id, isContinuation ? continuationCursor : undefined)
    } else {
      void pollAgentRun(response.data.id)
    }
  } catch (error) {
    agentError.value = describeAgentError(error)
  } finally {
    sending.value = false
  }
}

async function pollAgentRun(runId: string) {
  agentStreaming.value = true
  try {
    for (let attempt = 0; attempt < 160; attempt += 1) {
      const detail = await loadAgentDetail(runId)
      if (['succeeded', 'partial', 'failed', 'cancelled'].includes(detail.run.status)) return
      await new Promise((resolve) => window.setTimeout(resolve, 750))
    }
    agentError.value = 'Agent 运行仍在处理中，可稍后从当前会话继续查看。'
  } catch (error) {
    agentError.value = describeAgentError(error)
  } finally {
    agentStreaming.value = false
    activeAgentRunId.value = null
    if (activeId.value) await loadConversationAgentRuns(activeId.value)
    await scrollToBottom()
  }
}

async function sendSync(question: string) {
  sending.value = true
  try {
    const response = await callApi(() =>
      createChatCompletion({
        body: {
          conversation_id: activeId.value ?? null,
          question,
          knowledge_base_ids: selectedKbIds.value,
          mode: answerMode.value === 'learn' ? 'learn' : 'rag',
        },
        headers: { 'Idempotency-Key': draftKey.value },
      }),
    )
    draft.value = ''
    draftKey.value = crypto.randomUUID()
    if (!activeId.value) {
      activeId.value = response.data.conversation.id
    }
    messages.value = [
      ...messages.value,
      response.data.user_message,
      response.data.assistant_message,
    ]
    await scrollToBottom()
    await loadConversations()
  } catch (error) {
    sendError.value = describeChatError(error, '发送失败，请稍后重试。')
  } finally {
    sending.value = false
  }
}

async function fetchFinalMessage(messageId: string): Promise<Message | null> {
  try {
    const response = await callApi(() => getMessage({ path: { message_id: messageId } }))
    return response.data
  } catch {
    return null
  }
}

/** 流式发送：meta → delta* → sources → done/error；终止后统一经 getMessage 恢复权威消息，缺失时整体重拉。 */
async function sendStream(question: string) {
  messages.value = [...messages.value, makeLocalUserMessage(question)]
  const placeholder = reactive<Message>({
    id: `local-stream-${crypto.randomUUID()}`,
    conversation_id: activeId.value ?? '',
    sequence_no: 0,
    role: 'assistant',
    status: 'streaming',
    content: '',
    request_id: '',
    citations: [],
    created_at: nowIso(),
    updated_at: nowIso(),
  })
  messages.value = [...messages.value, placeholder]
  draft.value = ''
  await scrollToBottom()
  streaming.value = true
  controller = new AbortController()
  let messageId: string | null = null
  let streamFailed: { code: string; message: string } | null = null
  let disconnected = false
  try {
    await streamChatCompletion(
      {
        question,
        knowledge_base_ids: selectedKbIds.value,
        mode: answerMode.value === 'learn' ? 'learn' : 'rag',
        conversation_id: activeId.value ?? null,
      },
      {
        onMeta: (payload) => {
          messageId = payload.message_id
          if (!activeId.value) {
            activeId.value = payload.conversation_id
          }
        },
        onDelta: (payload) => {
          placeholder.content += payload.content
          placeholder.updated_at = nowIso()
        },
        onSources: (payload) => {
          placeholder.citations = payload.citations.map((citation) => ({
            chunk_id: '',
            preview_url: '',
            ...citation,
          }))
        },
        onError: (payload) => {
          streamFailed = { code: payload.code, message: payload.message }
          messageId = messageId ?? payload.message_id
        },
      },
      controller.signal,
    )
  } catch {
    disconnected = true
  }
  const aborted = controller.signal.aborted
  controller = null
  // 恢复：优先 getMessage 拿最终内容/引用/状态；拿不到则重拉整个消息列表
  const recovered = messageId ? await fetchFinalMessage(messageId) : null
  if (recovered) {
    const index = messages.value.findIndex((item) => item.id === placeholder.id)
    if (index >= 0) {
      messages.value = [
        ...messages.value.slice(0, index),
        recovered,
        ...messages.value.slice(index + 1),
      ]
    }
  } else if (activeId.value) {
    await loadMessages()
  }
  if (streamFailed !== null) {
    const failure = streamFailed as { code: string; message: string }
    streamNotice.value = `回答生成中断（${failure.code}）：${failure.message}。已显示后端保存的最终状态。`
  } else if (aborted) {
    streamNotice.value = '已停止生成，已显示后端保存的消息状态。'
  } else if (disconnected) {
    streamNotice.value = '连接中断，已从后端恢复该消息的最终状态。'
  }
  streaming.value = false
  draftKey.value = crypto.randomUUID()
  await loadConversations()
  await scrollToBottom()
}

function stopStream() {
  controller?.abort()
  controller = null
  streaming.value = false
}

/* ---------- 引用侧栏（fallback 回答不展示伪引用） ---------- */

const citationsFor = ref<{ messageId: string; citations: Citation[] } | null>(null)

function showCitations(message: Message): boolean {
  return (
    message.role === 'assistant' && message.status !== 'fallback' && message.citations.length > 0
  )
}

function openCitations(message: Message) {
  if (!showCitations(message)) {
    return
  }
  citationsFor.value = { messageId: message.id, citations: message.citations }
}

/* ---------- 反馈（幂等：同一消息同一评价复用幂等键） ---------- */

const feedbackGiven = ref<Record<string, 1 | -1>>({})
const feedbackKeys = new Map<string, string>()
const feedbackError = ref('')
const feedbackBusyId = ref('')

function canFeedback(message: Message): boolean {
  return (
    message.role === 'assistant' &&
    (message.status === 'completed' || message.status === 'fallback') &&
    !message.id.startsWith('local-')
  )
}

async function sendFeedback(message: Message, rating: 1 | -1) {
  if (feedbackGiven.value[message.id] || feedbackBusyId.value) {
    return
  }
  feedbackBusyId.value = message.id
  feedbackError.value = ''
  const mapKey = `${message.id}:${rating}`
  let key = feedbackKeys.get(mapKey)
  if (!key) {
    key = crypto.randomUUID()
    feedbackKeys.set(mapKey, key)
  }
  try {
    await callApi(() =>
      createMessageFeedback({
        path: { message_id: message.id },
        body: { rating },
        headers: { 'Idempotency-Key': key },
      }),
    )
    feedbackGiven.value = { ...feedbackGiven.value, [message.id]: rating }
  } catch (error) {
    if (error instanceof ApiError && error.status === 409) {
      feedbackError.value = '该消息当前状态不允许反馈。'
    } else if (error instanceof ApiError && error.status === 404) {
      feedbackError.value = '消息不存在或已被删除。'
    } else {
      feedbackError.value = '反馈提交失败，请稍后重试。'
    }
  } finally {
    feedbackBusyId.value = ''
  }
}

onMounted(() => {
  document.addEventListener('click', handleDocumentClick)
  void loadConversations()
  void loadKbScope()
  if (canDebugAgents.value) {
    void agentCatalog.load()
  }
})
onUnmounted(() => {
  document.removeEventListener('click', handleDocumentClick)
  stopStream()
  stopAgentStream()
})
</script>

<template>
  <div class="chat">
    <button
      v-if="sidebarOpen"
      type="button"
      class="chat__scrim"
      aria-label="关闭会话栏"
      @click="sidebarOpen = false"
    />

    <aside class="chat__sidebar" :class="{ 'chat__sidebar--open': sidebarOpen }">
      <div class="chat__brand">
        <span class="chat__brand-mark" aria-hidden="true">CP</span>
        <span class="chat__brand-copy">
          <strong>CampusPilot</strong>
          <small>智能助手</small>
        </span>
        <button
          type="button"
          class="chat__sidebar-close"
          aria-label="关闭会话栏"
          @click="sidebarOpen = false"
        >
          ×
        </button>
      </div>

      <UiButton
        variant="primary"
        class="chat__new"
        :loading="creatingConversation"
        @click="createNewConversation"
      >
        <span aria-hidden="true">＋</span> 新对话
      </UiButton>

      <nav v-if="moduleGroups.length" class="chat__modules" aria-label="CampusPilot 功能模块">
        <section v-for="group in moduleGroups" :key="group.title" class="chat__module">
          <button
            type="button"
            class="chat__module-trigger"
            :aria-expanded="expandedModule === group.title"
            @click="toggleModule(group.title)"
          >
            <span>{{ group.title }}</span>
            <span class="chat__module-chevron" aria-hidden="true">
              {{ expandedModule === group.title ? '−' : '＋' }}
            </span>
          </button>
          <div v-if="expandedModule === group.title" class="chat__module-links">
            <RouterLink
              v-for="item in group.items"
              :key="item.name"
              :to="{ name: item.name }"
              class="chat__module-link"
              :class="{ 'chat__module-link--active': item.name === 'chat' }"
              @click="sidebarOpen = false"
            >
              {{ item.title }}
            </RouterLink>
          </div>
        </section>
      </nav>

      <p class="chat__history-label">最近对话</p>
      <p v-if="conversationError" class="chat__error" role="alert">{{ conversationError }}</p>
      <div class="chat__history">
        <UiSkeleton v-if="conversationsLoading" :lines="4" />
        <ErrorState
          v-else-if="conversationsFailed"
          title="会话列表加载失败"
          @retry="loadConversations"
        />
        <EmptyState
          v-else-if="conversations.length === 0"
          title="暂无会话"
          description="新建会话开始知识问答"
        />
        <template v-else>
          <ul class="chat__conversations">
            <li v-for="item in conversations" :key="item.id">
              <div
                class="chat__conversation"
                :class="{ 'chat__conversation--active': item.id === activeId }"
                role="button"
                tabindex="0"
                @click="selectConversation(item.id)"
                @keydown.enter="selectConversation(item.id)"
              >
                <div class="chat__conversation-copy">
                  <p class="chat__conversation-title">{{ item.title || '未命名会话' }}</p>
                  <p class="chat__conversation-meta">
                    {{ item.message_count }} 条消息<template v-if="item.last_message_at">
                      · {{ formatTime(item.last_message_at) }}
                    </template>
                  </p>
                </div>
                <div class="chat__conversation-actions" @click.stop>
                  <template v-if="confirmingDeleteId === item.id">
                    <UiButton
                      size="sm"
                      variant="danger"
                      :loading="deletingConversation"
                      @click="confirmDeleteConversation(item)"
                    >
                      确认
                    </UiButton>
                    <UiButton size="sm" variant="text" @click="confirmingDeleteId = null">
                      取消
                    </UiButton>
                  </template>
                  <UiButton
                    v-else
                    size="sm"
                    variant="text"
                    aria-label="删除会话"
                    @click="confirmingDeleteId = item.id"
                  >
                    删除
                  </UiButton>
                </div>
              </div>
            </li>
          </ul>
          <UiButton
            v-if="hasMoreConversations"
            size="sm"
            variant="text"
            class="chat__more"
            @click="loadMoreConversations"
          >
            加载更多
          </UiButton>
        </template>
      </div>

      <div ref="accountMenuEl" class="chat__account">
        <div v-if="accountMenuOpen" class="chat__account-menu" role="menu" aria-label="账户菜单">
          <p class="chat__account-heading">我的事项</p>
          <RouterLink
            v-if="auth.hasPermission('work_order:read')"
            :to="{ name: 'work-orders-mine' }"
            class="chat__account-item"
            role="menuitem"
            @click="closeAccountMenu"
          >
            我的工单
          </RouterLink>
          <RouterLink
            v-if="auth.hasPermission('community:read')"
            :to="{ name: 'community-events' }"
            class="chat__account-item"
            role="menuitem"
            @click="closeAccountMenu"
          >
            我的活动
          </RouterLink>
          <RouterLink
            v-if="auth.hasPermission('community:read')"
            :to="{ name: 'lost-found-claims' }"
            class="chat__account-item"
            role="menuitem"
            @click="closeAccountMenu"
          >
            我的认领
          </RouterLink>
          <button
            v-if="canUseAgent"
            type="button"
            class="chat__account-item"
            role="menuitem"
            @click="focusPendingApproval"
          >
            <span>待办审批</span>
            <span v-if="pendingApprovalCount" class="chat__account-count">
              {{ pendingApprovalCount }}
            </span>
          </button>
          <p v-if="accountMenuNotice" class="chat__account-notice" role="status">
            {{ accountMenuNotice }}
          </p>
          <button
            type="button"
            class="chat__account-item chat__account-item--logout"
            role="menuitem"
            @click="logout"
          >
            退出登录
          </button>
        </div>
        <button
          type="button"
          class="chat__user"
          aria-haspopup="menu"
          :aria-expanded="accountMenuOpen"
          @click.stop="toggleAccountMenu"
          @keydown.escape="closeAccountMenu"
        >
          <span class="chat__user-avatar" aria-hidden="true">{{ userInitials }}</span>
          <span class="chat__user-copy">
            <strong>{{ userName }}</strong>
            <small>{{ userRole }}</small>
          </span>
          <span class="chat__user-chevron" aria-hidden="true">{{
            accountMenuOpen ? '⌄' : '›'
          }}</span>
        </button>
      </div>
    </aside>

    <section class="chat__main">
      <header class="chat__header">
        <button
          type="button"
          class="chat__sidebar-toggle"
          aria-label="打开会话栏"
          @click="sidebarOpen = true"
        >
          ☰
        </button>
        <div class="chat__header-text">
          <h1 class="chat__title">
            {{ activeConversation?.title || 'CampusPilot' }}
          </h1>
        </div>
      </header>

      <div ref="messageListEl" class="chat__messages" aria-live="polite">
        <div v-if="!activeId" class="chat__welcome">
          <span class="chat__welcome-mark" aria-hidden="true">CP</span>
          <h2>今天想了解什么？</h2>
          <p>新建对话，或从左侧打开一段历史记录。</p>
        </div>
        <UiSkeleton v-else-if="messagesLoading" :lines="6" />
        <ErrorState
          v-else-if="messagesFailed && conversationTimeline.length === 0"
          title="消息加载失败"
          @retry="loadMessages"
        />
        <template v-else>
          <p v-if="messagesFailed" class="chat__notice" role="status">
            部分知识问答消息加载失败，Agent 任务记录已从数据库恢复。
          </p>
          <div v-if="hasEarlier" class="chat__earlier">
            <UiButton size="sm" :loading="loadingEarlier" @click="loadEarlier">
              加载更早消息
            </UiButton>
          </div>
          <EmptyState
            v-if="conversationTimeline.length === 0"
            title="暂无消息"
            description="在下方输入问题开始问答"
          />
          <template v-for="entry in conversationTimeline" :key="entry.key">
            <template v-if="entry.kind === 'agent'">
              <template v-for="turn in agentConversationTurns(entry.detail)" :key="turn.key">
                <article v-if="turn.user" class="chat__message chat__message--user">
                  <div class="chat__bubble">
                    <p class="chat__bubble-text">{{ turn.user }}</p>
                  </div>
                  <span class="chat__message-avatar chat__message-avatar--user" aria-hidden="true">
                    {{ userInitials }}
                  </span>
                </article>
                <article class="chat__message chat__message--assistant">
                  <span
                    class="chat__message-avatar chat__message-avatar--assistant"
                    aria-hidden="true"
                  >CP</span>
                  <div class="chat__bubble chat__bubble--agent">
                    <div
                      v-if="turn.answer"
                      class="chat__bubble-text"
                      v-html="renderMarkdown(turn.answer)"
                    ></div>
                    <p
                      v-else-if="entry.detail.run.id === activeAgentRunId && agentStreaming"
                      class="chat__bubble-text chat__bubble-text--pending"
                    >
                      Agent 正在执行任务…
                    </p>
                    <p
                      v-else-if="String(entry.detail.run.status) === 'awaiting_approval'"
                      class="chat__bubble-text chat__bubble-text--pending"
                    >
                      等待您确认后继续执行。
                    </p>
                    <p v-else class="chat__bubble-text chat__bubble-text--failed">
                      {{ entry.detail.run.error_code || '该运行暂无最终回答。' }}
                    </p>
                    <details v-if="turn.isLast" class="chat__agent-details">
                      <summary>执行详情 · {{ entry.detail.run.status }}</summary>
                      <AgentTimeline
                        v-if="entry.detail.run.id === activeAgentRunId"
                        :events="agentEvents"
                        :live="agentStreaming"
                      />
                      <ol v-else class="chat__agent-steps">
                        <li v-for="step in entry.detail.steps" :key="step.id">
                          {{ step.agent_code }} · {{ step.status }}
                        </li>
                      </ol>
                    </details>
                  </div>
                </article>
              </template>
              <ApprovalCards
                v-if="entry.detail.approvals.length"
                :data-pending-approval="
                  entry.detail.approvals.some((approval) => approval.status === 'pending')
                "
                :run-id="entry.detail.run.id"
                :approvals="entry.detail.approvals"
                @decided="handleAgentApprovalDecided(entry.detail.run.id)"
              />
            </template>
            <article
              v-else
              class="chat__message"
              :class="
                entry.message.role === 'user' ? 'chat__message--user' : 'chat__message--assistant'
              "
            >
              <span
                v-if="entry.message.role === 'assistant'"
                class="chat__message-avatar chat__message-avatar--assistant"
                aria-hidden="true"
              >CP</span>
              <div class="chat__bubble">
                <div
                  v-if="
                    entry.message.role === 'assistant' &&
                      entry.message.status !== 'completed' &&
                      entry.message.status !== 'fallback'
                  "
                  class="chat__bubble-status"
                >
                  <StatusBadge
                    :status="entry.message.status"
                    :label="MESSAGE_STATUS_LABELS[entry.message.status]"
                  />
                </div>
                <div
                  v-if="entry.message.content"
                  class="chat__bubble-text"
                  v-html="
                    entry.message.role === 'assistant'
                      ? renderMarkdown(entry.message.content)
                      : entry.message.content
                  "
                ></div>
                <p
                  v-else-if="entry.message.status === 'streaming'"
                  class="chat__bubble-text chat__bubble-text--pending"
                >
                  正在生成…
                </p>
                <p
                  v-else-if="entry.message.status === 'failed'"
                  class="chat__bubble-text chat__bubble-text--failed"
                >
                  回答生成失败{{
                    entry.message.error_code ? `（${entry.message.error_code}）` : ''
                  }}
                </p>
                <div v-if="entry.message.role === 'assistant'" class="chat__bubble-actions">
                  <UiButton
                    v-if="showCitations(entry.message)"
                    size="sm"
                    variant="text"
                    @click="openCitations(entry.message)"
                  >
                    引用 {{ entry.message.citations.length }}
                  </UiButton>
                  <template v-if="canFeedback(entry.message)">
                    <button
                      type="button"
                      class="chat__feedback"
                      :class="{ 'chat__feedback--active': feedbackGiven[entry.message.id] === 1 }"
                      :disabled="
                        !!feedbackGiven[entry.message.id] || feedbackBusyId === entry.message.id
                      "
                      :aria-label="`对回答 ${entry.message.sequence_no} 点赞`"
                      @click="sendFeedback(entry.message, 1)"
                    >
                      👍
                    </button>
                    <button
                      type="button"
                      class="chat__feedback"
                      :class="{ 'chat__feedback--active': feedbackGiven[entry.message.id] === -1 }"
                      :disabled="
                        !!feedbackGiven[entry.message.id] || feedbackBusyId === entry.message.id
                      "
                      :aria-label="`对回答 ${entry.message.sequence_no} 点踩`"
                      @click="sendFeedback(entry.message, -1)"
                    >
                      👎
                    </button>
                  </template>
                </div>
              </div>
              <span
                v-if="entry.message.role === 'user'"
                class="chat__message-avatar chat__message-avatar--user"
                aria-hidden="true"
              >{{ userInitials }}</span>
            </article>
          </template>
        </template>
      </div>

      <footer class="chat__composer-wrap">
        <div class="chat__composer">
          <p v-if="feedbackError" class="chat__error" role="alert">{{ feedbackError }}</p>
          <p v-if="agentError" class="chat__error" role="alert">{{ agentError }}</p>
          <p v-if="streamNotice" class="chat__notice" role="status">{{ streamNotice }}</p>
          <p v-if="sendError" class="chat__error" role="alert">{{ sendError }}</p>
          <p
            v-else-if="answerMode === 'library' && selectedKbIds.length === 0 && !kbError"
            class="chat__hint"
          >
            请输入 /lib 选择至少一个知识库。
          </p>
          <UiCard v-if="kbPanelOpen" class="chat__kb-panel" padding="sm">
            <div class="chat__kb-head">
              <strong>选择知识库</strong>
              <button type="button" aria-label="关闭知识库选择" @click="kbPanelOpen = false">
                ×
              </button>
            </div>
            <p v-if="kbError" class="chat__error" role="alert">{{ kbError }}</p>
            <template v-else>
              <p class="chat__kb-hint">
                {{ answerMode === 'learn' ? '可选课程资料（无资料时使用通用模型）：' : '选择本次问答检索范围（1–10 个）：' }}
              </p>
              <div class="chat__kb-list">
                <label v-for="kb in kbs" :key="kb.id" class="chat__kb-item">
                  <input
                    type="checkbox"
                    :checked="selectedKbIds.includes(kb.id)"
                    @change="toggleKb(kb.id)"
                  />
                  <span>{{ kb.name }}</span>
                </label>
                <p v-if="kbs.length === 0" class="chat__kb-hint">暂无可访问知识库。</p>
              </div>
              <p v-if="kbNotice" class="chat__error" role="alert">{{ kbNotice }}</p>
            </template>
          </UiCard>
          <UiCard v-if="selectionPanel && canDebugAgents" class="chat__selection-panel" padding="sm">
            <div class="chat__kb-head">
              <strong>{{ selectionPanel === 'agents' ? '选择 Agent' : '选择 Tool' }}</strong>
              <button
                type="button"
                aria-label="关闭 Agent 与 Tool 选择"
                @click="selectionPanel = null"
              >
                ×
              </button>
            </div>
            <UiSkeleton v-if="agentCatalog.loading" :lines="3" />
            <ErrorState
              v-else-if="agentCatalog.failed"
              title="Agent 目录加载失败"
              @retry="agentCatalog.load(true)"
            />
            <div v-else-if="selectionPanel === 'agents'" class="chat__selection-list">
              <label v-for="agent in enabledAgents" :key="agent.code" class="chat__selection-item">
                <input
                  type="checkbox"
                  :checked="selectedAgentCodes.includes(agent.code)"
                  @change="toggleAgent(agent.code)"
                />
                <span><strong>{{ agent.name }}</strong><small>{{ agent.description }}</small></span>
              </label>
            </div>
            <div v-else class="chat__selection-list chat__selection-list--tools">
              <label v-for="tool in visibleTools" :key="tool.name" class="chat__selection-item">
                <input
                  type="checkbox"
                  :checked="selectedToolNames.includes(tool.name)"
                  @change="toggleTool(tool)"
                />
                <span><strong><code>{{ tool.name }}</code></strong><small>{{ tool.description }} · {{ tool.risk_level.toUpperCase() }}</small></span>
              </label>
            </div>
            <p v-if="selectionNotice" class="chat__error" role="alert">{{ selectionNotice }}</p>
            <div v-if="agentSelectionActive" class="chat__selection-actions">
              <button type="button" @click="clearAgentSelection">清除 Agent 模式</button>
              <span>已选 {{ selectedAgentCodes.length }} Agent ·
                {{ selectedToolNames.length }} Tool</span>
            </div>
          </UiCard>
          <div v-if="slashMenuOpen" class="chat__command-menu" role="listbox" aria-label="输入命令">
            <p v-if="filteredCommands.length === 0" class="chat__command-empty">暂无匹配命令</p>
            <button
              v-for="(option, index) in filteredCommands"
              :key="option.command"
              type="button"
              class="chat__command-option"
              :class="{
                'chat__command-option--active': option.mode === mode,
                'chat__command-option--selected': index === commandIndex,
              }"
              :aria-selected="index === commandIndex"
              @mouseenter="commandIndex = index"
              @mousedown.prevent="applyCommand(option)"
            >
              <code>{{ option.command }}</code>
              <span>
                <strong>{{ option.label }}</strong>
                <small>{{ option.description }}</small>
              </span>
            </button>
          </div>
          <div class="chat__composer-row">
            <div class="chat__selection-chips" aria-label="当前回答选项">
              <span class="chat__selection-chip">{{ mode === 'stream' ? '流式' : '完整' }}</span>
              <span class="chat__selection-chip">
                {{ answerMode === 'auto' ? '智能路由' : answerMode === 'learn' ? '学习辅导' : '知识库问答' }}
              </span>
              <span
                v-if="answerMode !== 'auto'"
                class="chat__selection-chip chat__selection-chip--muted"
              >知识库 {{ selectedKbIds.length }}</span>
              <span v-if="agentSelectionActive" class="chat__selection-chip">Agent {{ selectedAgentCodes.length }}</span>
              <span
                v-if="selectedToolNames.length"
                class="chat__selection-chip chat__selection-chip--muted"
              >
                Tool {{ selectedToolNames.length }}
              </span>
            </div>
            <textarea
              ref="composerInputEl"
              v-model="draft"
              class="chat__input"
              rows="1"
              :maxlength="answerMode === 'auto' || agentSelectionActive ? 4000 : 2000"
              placeholder="输入问题，或输入 / 查看命令"
              :disabled="sending || streaming || agentStreaming || agentSubmissionBlocked"
              aria-label="问题输入"
              @keydown="handleComposerKeydown"
            />
            <UiButton
              v-if="streaming || agentStreaming"
              variant="danger"
              class="chat__send"
              aria-label="停止生成"
              @click="activeAgentRunId ? cancelActiveAgentRun() : stopStream()"
            >
              ■
            </UiButton>
            <UiButton
              v-else
              variant="primary"
              class="chat__send"
              :loading="sending"
              :disabled="!canSend"
              aria-label="发送"
              @click="send"
            >
              <span aria-hidden="true">↑</span><span class="chat__sr-only">发送</span>
            </UiButton>
          </div>
          <p class="chat__disclaimer">CampusPilot 可能会出错，请通过引用来源核对重要信息。</p>
        </div>
      </footer>
    </section>

    <aside v-if="citationsFor" class="chat__citations" aria-label="引用来源">
      <div class="chat__citations-head">
        <h2 class="chat__citations-title">引用来源（{{ citationsFor.citations.length }}）</h2>
        <UiButton size="sm" @click="citationsFor = null">关闭</UiButton>
      </div>
      <ol class="chat__citation-list">
        <li
          v-for="citation in citationsFor.citations"
          :key="citation.citation_no"
          class="chat__citation"
        >
          <p class="chat__citation-doc">
            [{{ citation.citation_no }}] {{ citation.document_title }}
          </p>
          <p class="chat__citation-loc">
            {{ citation.source_location
            }}<template v-if="citation.page_number != null">
              · 第 {{ citation.page_number }} 页
            </template>
            · 相关度 {{ citation.relevance_score.toFixed(2) }}
          </p>
          <blockquote class="chat__citation-quote">{{ citation.quote_excerpt }}</blockquote>
        </li>
      </ol>
    </aside>
  </div>
</template>

<style scoped>
.chat {
  display: grid;
  grid-template-columns: 272px minmax(0, 1fr);
  height: 100vh;
  min-height: 0;
  background: var(--cp-surface-card);
  color: var(--cp-ink);
}

.chat__sidebar {
  display: flex;
  flex-direction: column;
  min-height: 0;
  padding: var(--cp-space-3);
  border-right: 1px solid var(--cp-hairline);
  background: var(--cp-canvas);
  z-index: 20;
}

.chat__brand {
  min-height: 48px;
  display: flex;
  align-items: center;
  gap: var(--cp-space-2);
  padding: 0 var(--cp-space-1);
}

.chat__brand-mark,
.chat__welcome-mark {
  display: grid;
  place-items: center;
  flex: 0 0 auto;
  background: var(--cp-primary);
  color: var(--cp-on-primary);
  font-size: 12px;
  font-weight: 700;
}

.chat__brand-mark {
  width: 30px;
  height: 30px;
  border-radius: var(--cp-radius-button);
}

.chat__brand-copy,
.chat__user-copy {
  min-width: 0;
  display: flex;
  flex-direction: column;
  line-height: 1.3;
}

.chat__brand-copy strong,
.chat__user-copy strong {
  overflow: hidden;
  color: var(--cp-ink);
  font-size: 12px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.chat__brand-copy small,
.chat__user-copy small {
  overflow: hidden;
  color: var(--cp-muted);
  font-size: 10px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.chat__sidebar-close,
.chat__sidebar-toggle {
  display: none;
  width: 40px;
  height: 40px;
  border: 1px solid var(--cp-hairline-strong);
  border-radius: var(--cp-radius-button);
  background: var(--cp-surface-card);
  color: var(--cp-ink);
  cursor: pointer;
  font-size: 18px;
}

.chat__new {
  width: 100%;
  min-height: 44px;
  margin-top: var(--cp-space-3);
  justify-content: flex-start;
  font-size: 13px;
}

.chat__modules {
  max-height: 34vh;
  margin-top: var(--cp-space-2);
  padding-bottom: var(--cp-space-2);
  border-bottom: 1px solid var(--cp-hairline);
  overflow-y: auto;
}

.chat__module + .chat__module {
  margin-top: 2px;
}

.chat__module-trigger {
  width: 100%;
  min-height: 34px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 var(--cp-space-2);
  border: 1px solid transparent;
  border-radius: var(--cp-radius-button);
  background: transparent;
  color: var(--cp-body);
  cursor: pointer;
  font-family: inherit;
  font-size: 12px;
  font-weight: 500;
  text-align: left;
}

.chat__module-trigger:hover,
.chat__module-trigger:focus-visible,
.chat__module-trigger[aria-expanded='true'] {
  background: var(--cp-surface-card);
  color: var(--cp-ink);
}

.chat__module-chevron {
  color: var(--cp-muted-soft);
  font-size: 12px;
}

.chat__module-links {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 2px 0 var(--cp-space-1) var(--cp-space-3);
}

.chat__module-link {
  min-height: 30px;
  display: flex;
  align-items: center;
  padding: 0 var(--cp-space-2);
  border-radius: 6px;
  color: var(--cp-muted);
  font-size: 11px;
  text-decoration: none;
}

.chat__module-link:hover,
.chat__module-link:focus-visible {
  background: var(--cp-canvas-soft);
  color: var(--cp-ink);
}

.chat__module-link--active {
  color: var(--cp-primary);
  font-weight: 600;
}

.chat__history-label {
  margin: var(--cp-space-3) var(--cp-space-2) var(--cp-space-1);
  color: var(--cp-muted-soft);
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.chat__history {
  min-height: 0;
  flex: 1;
  overflow-y: auto;
}

.chat__conversations {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-1);
}

.chat__conversation {
  position: relative;
  display: flex;
  align-items: center;
  min-height: 46px;
  padding: 6px var(--cp-space-2);
  border: 1px solid transparent;
  border-radius: var(--cp-radius-button);
  background: transparent;
  cursor: pointer;
}

.chat__conversation:hover,
.chat__conversation:focus-visible {
  background: var(--cp-canvas-soft);
}

.chat__conversation--active {
  border-color: var(--cp-hairline);
  background: var(--cp-surface-card);
}

.chat__conversation-copy {
  min-width: 0;
  flex: 1;
}

.chat__conversation-title {
  margin: 0;
  font-size: 12px;
  color: var(--cp-ink);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.chat__conversation-meta {
  margin: 2px 0 0;
  font-size: 10.5px;
  color: var(--cp-muted-soft);
}

.chat__conversation-actions {
  display: none;
  align-items: center;
  gap: var(--cp-space-1);
  margin-left: var(--cp-space-1);
}

.chat__conversation:hover .chat__conversation-actions,
.chat__conversation:focus-within .chat__conversation-actions {
  display: flex;
}

.chat__more {
  width: 100%;
  margin-top: var(--cp-space-2);
}

.chat__account {
  position: relative;
  margin-top: var(--cp-space-3);
  border-top: 1px solid var(--cp-hairline);
}

.chat__account-menu {
  position: absolute;
  right: 0;
  bottom: calc(100% + var(--cp-space-2));
  left: 0;
  z-index: 30;
  padding: var(--cp-space-2);
  border: 1px solid var(--cp-hairline-strong);
  border-radius: var(--cp-radius-card);
  background: var(--cp-surface-card);
}

.chat__account-heading {
  margin: 2px var(--cp-space-2) var(--cp-space-1);
  color: var(--cp-muted-soft);
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.08em;
}

.chat__account-item {
  width: 100%;
  min-height: 40px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 var(--cp-space-2);
  border: 0;
  border-radius: var(--cp-radius-button);
  background: transparent;
  color: var(--cp-body);
  cursor: pointer;
  font-family: inherit;
  font-size: 12px;
  text-align: left;
  text-decoration: none;
}

.chat__account-item:hover,
.chat__account-item:focus-visible {
  background: var(--cp-canvas-soft);
  color: var(--cp-ink);
}

.chat__account-item--logout {
  margin-top: var(--cp-space-1);
  border-top: 1px solid var(--cp-hairline);
  border-radius: 0 0 var(--cp-radius-button) var(--cp-radius-button);
  color: var(--cp-primary-active);
}

.chat__account-count {
  min-width: 20px;
  min-height: 20px;
  display: grid;
  place-items: center;
  border-radius: var(--cp-radius-pill);
  background: var(--cp-primary-soft);
  color: var(--cp-primary-active);
  font-size: 10px;
  font-weight: 700;
}

.chat__account-notice {
  margin: var(--cp-space-1) var(--cp-space-2);
  color: var(--cp-muted);
  font-size: 10px;
  line-height: 1.5;
}

.chat__user {
  width: 100%;
  display: flex;
  align-items: center;
  gap: var(--cp-space-2);
  min-height: 56px;
  padding: var(--cp-space-2);
  border: 0;
  background: transparent;
  cursor: pointer;
  font-family: inherit;
  text-align: left;
}

.chat__user:hover,
.chat__user:focus-visible,
.chat__user[aria-expanded='true'] {
  background: var(--cp-canvas-soft);
}

.chat__user-avatar,
.chat__message-avatar {
  display: grid;
  place-items: center;
  flex: 0 0 auto;
  border-radius: 50%;
  font-size: 12px;
  font-weight: 600;
}

.chat__user-avatar {
  width: 34px;
  height: 34px;
  background: var(--cp-ink);
  color: var(--cp-canvas);
}

.chat__user-copy {
  flex: 1;
}

.chat__user-chevron {
  color: var(--cp-muted-soft);
  font-size: 16px;
}

.chat__main {
  position: relative;
  display: flex;
  flex-direction: column;
  min-height: 0;
  background: var(--cp-surface-card);
}

.chat__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--cp-space-3);
  min-height: 64px;
  padding: 0 var(--cp-space-5);
  border-bottom: 1px solid var(--cp-hairline-soft);
  background: color-mix(in srgb, var(--cp-surface-card) 94%, transparent);
}

.chat__title {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: var(--cp-ink);
}

.chat__kb-panel,
.chat__selection-panel {
  position: absolute;
  right: auto;
  bottom: calc(100% + var(--cp-space-2));
  left: 0;
  z-index: 13;
  width: min(440px, calc(100vw - 32px));
  border-color: var(--cp-hairline-strong);
}

.chat__selection-panel {
  max-height: min(440px, 60vh);
  overflow-y: auto;
}

.chat__selection-list {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--cp-space-2);
}

.chat__selection-list--tools {
  grid-template-columns: minmax(0, 1fr);
}

.chat__selection-item {
  display: flex;
  align-items: flex-start;
  gap: var(--cp-space-2);
  padding: var(--cp-space-2);
  border: 1px solid var(--cp-hairline);
  border-radius: var(--cp-radius-button);
  cursor: pointer;
}

.chat__selection-item span {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.chat__selection-item strong {
  color: var(--cp-ink);
  font-size: 12px;
}

.chat__selection-item small {
  color: var(--cp-muted);
  font-size: 10px;
}

.chat__selection-item code {
  color: var(--cp-primary);
  font-family: var(--cp-font-mono);
  font-size: 10.5px;
}

.chat__selection-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--cp-space-2);
  margin-top: var(--cp-space-2);
  color: var(--cp-muted);
  font-size: 10px;
}

.chat__selection-actions button {
  min-height: 32px;
  padding: 0 var(--cp-space-2);
  border: 0;
  border-radius: var(--cp-radius-button);
  background: var(--cp-canvas);
  color: var(--cp-primary);
  cursor: pointer;
  font-size: 11px;
}

.chat__kb-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--cp-space-2);
  margin-bottom: var(--cp-space-2);
  color: var(--cp-ink);
  font-size: 12px;
}

.chat__kb-head button {
  width: 32px;
  height: 32px;
  border: 0;
  border-radius: var(--cp-radius-button);
  background: transparent;
  color: var(--cp-muted);
  cursor: pointer;
  font-size: 18px;
}

.chat__kb-head button:hover,
.chat__kb-head button:focus-visible {
  background: var(--cp-canvas);
  color: var(--cp-ink);
}

.chat__kb-hint {
  margin: 0 0 var(--cp-space-2);
  font-size: 13px;
  color: var(--cp-muted);
}

.chat__kb-list {
  display: flex;
  flex-wrap: wrap;
  gap: var(--cp-space-2) var(--cp-space-4);
}

.chat__kb-item {
  display: inline-flex;
  align-items: center;
  gap: var(--cp-space-1);
  font-size: 13px;
  color: var(--cp-body);
  cursor: pointer;
}

.chat__messages {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-5);
  padding: var(--cp-space-6) max(var(--cp-space-5), calc((100% - 780px) / 2));
  background: var(--cp-surface-card);
}

.chat__welcome {
  flex: 1;
  display: grid;
  align-content: center;
  justify-items: center;
  padding-bottom: 15vh;
  text-align: center;
}

.chat__welcome-mark {
  width: 42px;
  height: 42px;
  margin-bottom: var(--cp-space-4);
  border-radius: var(--cp-radius-card);
  font-size: 14px;
}

.chat__welcome h2 {
  margin: 0;
  color: var(--cp-ink);
  font-size: 28px;
  font-weight: 400;
  letter-spacing: -0.02em;
}

.chat__welcome p {
  margin: var(--cp-space-2) 0 0;
  color: var(--cp-muted);
  font-size: 14px;
}

.chat__earlier {
  display: flex;
  justify-content: center;
}

.chat__message {
  display: flex;
  align-items: flex-start;
  gap: var(--cp-space-3);
  width: 100%;
  max-width: 780px;
  margin: 0 auto;
}

.chat__message--user {
  justify-content: flex-end;
}

.chat__message--assistant {
  justify-content: flex-start;
}

.chat__bubble {
  max-width: calc(100% - 48px);
  padding: var(--cp-space-2) 0;
  background: transparent;
}

.chat__bubble--agent {
  width: min(760px, calc(100% - 48px));
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-3);
}

.chat__agent-details {
  border-top: 1px solid var(--cp-hairline);
  padding-top: var(--cp-space-2);
  color: var(--cp-muted);
  font-size: 12px;
}

.chat__agent-details summary {
  min-height: 32px;
  cursor: pointer;
  font-weight: 600;
}

.chat__agent-steps {
  margin: var(--cp-space-2) 0 0;
  padding-left: var(--cp-space-5);
}

.chat__message--user .chat__bubble {
  max-width: 78%;
  padding: var(--cp-space-3) var(--cp-space-4);
  border: 1px solid var(--cp-hairline);
  border-radius: 18px;
  background: var(--cp-canvas);
}

.chat__message-avatar {
  width: 30px;
  height: 30px;
  margin-top: var(--cp-space-1);
}

.chat__message-avatar--assistant {
  border-radius: var(--cp-radius-button);
  background: var(--cp-primary);
  color: var(--cp-on-primary);
  font-size: 10px;
}

.chat__message-avatar--user {
  background: var(--cp-ink);
  color: var(--cp-canvas);
}

.chat__bubble-status {
  margin-bottom: var(--cp-space-1);
}

.chat__bubble-text {
  margin: 0;
  font-size: 15px;
  line-height: 1.7;
  color: var(--cp-ink);
  white-space: pre-wrap;
  word-break: break-word;
}

.chat__bubble-text--pending {
  color: var(--cp-muted);
}

.chat__bubble-text--failed {
  color: var(--cp-error);
}

/* Markdown rendered content */
.chat__bubble-text :deep(h1),
.chat__bubble-text :deep(h2),
.chat__bubble-text :deep(h3),
.chat__bubble-text :deep(h4),
.chat__bubble-text :deep(h5),
.chat__bubble-text :deep(h6) {
  margin: var(--cp-space-2) 0 var(--cp-space-1);
  font-weight: 600;
  line-height: 1.35;
}

.chat__bubble-text :deep(h1) { font-size: 1.25em; }
.chat__bubble-text :deep(h2) { font-size: 1.15em; }
.chat__bubble-text :deep(h3) { font-size: 1.05em; }

.chat__bubble-text :deep(p) {
  margin: 0 0 var(--cp-space-1);
}

.chat__bubble-text :deep(p:last-child) {
  margin-bottom: 0;
}

.chat__bubble-text :deep(ul),
.chat__bubble-text :deep(ol) {
  margin: 0 0 var(--cp-space-1);
  padding-left: 1.5em;
}

.chat__bubble-text :deep(li) {
  margin-bottom: 2px;
}

.chat__bubble-text :deep(blockquote) {
  margin: var(--cp-space-1) 0;
  padding: var(--cp-space-1) var(--cp-space-2);
  border-left: 3px solid var(--cp-primary);
  background: var(--cp-canvas-soft);
  border-radius: 0 var(--cp-radius-button) var(--cp-radius-button) 0;
  color: var(--cp-muted);
}

.chat__bubble-text :deep(code) {
  padding: 1px 5px;
  border-radius: 4px;
  background: var(--cp-canvas-soft);
  font-family: 'Cascadia Code', 'Fira Code', 'Consolas', monospace;
  font-size: 0.9em;
}

.chat__bubble-text :deep(pre) {
  margin: var(--cp-space-1) 0;
  padding: var(--cp-space-2);
  border-radius: var(--cp-radius-button);
  background: var(--cp-canvas);
  overflow-x: auto;
}

.chat__bubble-text :deep(pre code) {
  padding: 0;
  background: none;
}

.chat__bubble-text :deep(a) {
  color: var(--cp-primary);
  text-decoration: underline;
}

.chat__bubble-text :deep(hr) {
  margin: var(--cp-space-2) 0;
  border: none;
  border-top: 1px solid var(--cp-hairline);
}

.chat__bubble-text :deep(table) {
  width: 100%;
  border-collapse: collapse;
  margin: var(--cp-space-1) 0;
}

.chat__bubble-text :deep(th),
.chat__bubble-text :deep(td) {
  padding: 6px var(--cp-space-2);
  border: 1px solid var(--cp-hairline);
  text-align: left;
}

.chat__bubble-text :deep(th) {
  background: var(--cp-canvas-soft);
  font-weight: 600;
}

.chat__bubble-actions {
  display: flex;
  align-items: center;
  gap: var(--cp-space-2);
  margin-top: var(--cp-space-2);
}

.chat__feedback {
  min-width: var(--cp-control-sm);
  min-height: var(--cp-control-sm);
  border: 1px solid var(--cp-hairline-strong);
  border-radius: var(--cp-radius-button);
  background: var(--cp-surface-card);
  cursor: pointer;
  font-size: 14px;
}

.chat__feedback--active {
  border-color: var(--cp-primary);
  background: color-mix(in srgb, var(--cp-primary) 8%, white);
}

.chat__feedback:disabled {
  cursor: default;
  opacity: 0.7;
}

.chat__composer-wrap {
  flex: 0 0 auto;
  padding: var(--cp-space-2) var(--cp-space-5) var(--cp-space-3);
  background: var(--cp-surface-card);
}

.chat__composer {
  position: relative;
  width: 100%;
  max-width: 780px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-2);
}

.chat__command-menu {
  position: absolute;
  right: auto;
  bottom: calc(100% + var(--cp-space-2));
  left: 0;
  z-index: 12;
  width: min(330px, calc(100vw - 32px));
  padding: var(--cp-space-1);
  border: 1px solid var(--cp-hairline-strong);
  border-radius: var(--cp-radius-card);
  background: var(--cp-surface-card);
}

.chat__command-empty {
  margin: 0;
  padding: var(--cp-space-3);
  color: var(--cp-muted);
  font-size: 11px;
  text-align: center;
}

.chat__command-option {
  width: 100%;
  min-height: 52px;
  display: grid;
  grid-template-columns: 66px minmax(0, 1fr);
  align-items: center;
  gap: var(--cp-space-2);
  padding: var(--cp-space-2);
  border: 0;
  border-radius: var(--cp-radius-button);
  background: transparent;
  color: var(--cp-body);
  cursor: pointer;
  font-family: inherit;
  text-align: left;
}

.chat__command-option:hover,
.chat__command-option:focus-visible,
.chat__command-option--active {
  background: var(--cp-canvas);
}

.chat__command-option--selected {
  background: color-mix(in srgb, var(--cp-primary) 8%, var(--cp-surface-card));
  outline: 1px solid color-mix(in srgb, var(--cp-primary) 45%, var(--cp-hairline));
}

.chat__command-option code {
  color: var(--cp-primary);
  font-family: var(--cp-font-mono);
  font-size: 11px;
}

.chat__command-option span {
  display: flex;
  flex-direction: column;
}

.chat__command-option strong {
  color: var(--cp-ink);
  font-size: 12px;
  font-weight: 600;
}

.chat__command-option small {
  margin-top: 2px;
  color: var(--cp-muted);
  font-size: 10.5px;
}

.chat__composer-row {
  display: flex;
  gap: var(--cp-space-2);
  align-items: center;
  min-height: 58px;
  padding: var(--cp-space-2);
  border: 1px solid var(--cp-hairline-strong);
  border-radius: 18px;
  background: var(--cp-surface-card);
}

.chat__selection-chips {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  gap: var(--cp-space-1);
}

.chat__selection-chip {
  display: inline-flex;
  align-items: center;
  min-height: 28px;
  padding: 0 var(--cp-space-2);
  border-radius: var(--cp-radius-pill);
  background: color-mix(in srgb, var(--cp-primary) 9%, var(--cp-surface-card));
  color: var(--cp-primary);
  font-size: 10px;
  font-weight: 600;
  white-space: nowrap;
}

.chat__selection-chip--muted {
  background: var(--cp-canvas);
  color: var(--cp-muted);
  font-weight: 500;
}

.chat__input {
  flex: 1;
  min-height: 40px;
  max-height: 160px;
  padding: 9px var(--cp-space-2);
  border: 0;
  outline: 0;
  background: transparent;
  color: var(--cp-ink);
  font-family: var(--cp-font-sans);
  font-size: 14px;
  resize: none;
}

.chat__send {
  width: 40px;
  min-width: 40px;
  height: 40px;
  min-height: 40px;
  padding: 0;
  border-radius: 50%;
  flex-shrink: 0;
  font-size: 18px;
}

.chat__disclaimer {
  margin: 0;
  color: var(--cp-muted-soft);
  font-size: 11px;
  text-align: center;
}

.chat__sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

.chat__error {
  margin: 0;
  padding: var(--cp-space-2) var(--cp-space-3);
  border: 1px solid color-mix(in srgb, var(--cp-error) 35%, transparent);
  border-radius: var(--cp-radius-button);
  background: color-mix(in srgb, var(--cp-error) 6%, white);
  color: var(--cp-error);
  font-size: 13px;
}

.chat__notice {
  margin: 0;
  padding: var(--cp-space-2) var(--cp-space-3);
  border: 1px solid color-mix(in srgb, var(--cp-warning) 35%, transparent);
  border-radius: var(--cp-radius-button);
  background: color-mix(in srgb, var(--cp-warning) 8%, white);
  color: var(--cp-warning);
  font-size: 13px;
}

.chat__hint {
  margin: 0;
  font-size: 13px;
  color: var(--cp-muted);
}

.chat__citations {
  position: fixed;
  top: var(--cp-space-4);
  right: var(--cp-space-4);
  bottom: var(--cp-space-4);
  z-index: 40;
  width: min(380px, calc(100vw - 32px));
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-3);
  padding: var(--cp-space-3);
  border: 1px solid var(--cp-hairline);
  border-radius: var(--cp-radius-card);
  background: var(--cp-surface-card);
  overflow-y: auto;
}

.chat__citations-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--cp-space-2);
}

.chat__citations-title {
  margin: 0;
  font-size: 15px;
  color: var(--cp-ink);
}

.chat__citation-list {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-3);
}

.chat__citation {
  padding: var(--cp-space-3);
  border: 1px solid var(--cp-hairline);
  border-radius: var(--cp-radius-button);
  background: var(--cp-canvas-soft);
}

.chat__citation-doc {
  margin: 0;
  font-size: 13px;
  font-weight: 600;
  color: var(--cp-ink);
}

.chat__citation-loc {
  margin: var(--cp-space-1) 0 0;
  font-size: 12px;
  color: var(--cp-muted);
}

.chat__citation-quote {
  margin: var(--cp-space-2) 0 0;
  padding-left: var(--cp-space-3);
  border-left: 2px solid var(--cp-hairline-strong);
  font-size: 13px;
  color: var(--cp-body);
  white-space: pre-wrap;
  word-break: break-word;
}

@media (max-width: 760px) {
  .chat {
    grid-template-columns: minmax(0, 1fr);
  }

  .chat__sidebar {
    position: fixed;
    inset: 0 auto 0 0;
    width: min(86vw, 300px);
    transform: translateX(-100%);
    transition: transform 0.2s ease;
  }

  .chat__sidebar--open {
    transform: translateX(0);
  }

  .chat__scrim {
    position: fixed;
    inset: 0;
    z-index: 15;
    display: block;
    border: 0;
    background: rgba(38, 37, 30, 0.28);
  }

  .chat__sidebar-close {
    display: grid;
    place-items: center;
    margin-left: auto;
  }

  .chat__sidebar-toggle {
    display: grid;
    place-items: center;
  }

  .chat__header {
    min-height: 60px;
    padding: 0 var(--cp-space-3);
  }

  .chat__messages {
    padding: var(--cp-space-5) var(--cp-space-3);
  }

  .chat__composer-wrap {
    padding: var(--cp-space-2) var(--cp-space-3);
  }

  .chat__message--user .chat__bubble {
    max-width: 86%;
  }

  .chat__kb-panel,
  .chat__selection-panel {
    width: calc(100vw - 24px);
  }

  .chat__selection-list {
    grid-template-columns: minmax(0, 1fr);
  }

  .chat__citations {
    top: var(--cp-space-2);
    right: var(--cp-space-2);
    bottom: var(--cp-space-2);
    width: calc(100vw - 16px);
  }
}
</style>

<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, reactive, ref } from 'vue'

import { ApiError, callApi } from '@/api/client'
import {
  createChatCompletion,
  createConversation,
  createMessageFeedback,
  deleteConversation,
  getMessage,
  listConversationMessages,
  listConversations,
  listKnowledgeBases,
} from '@/api/generated'
import type { Citation, Conversation, KnowledgeBase, Message } from '@/api/generated'
import { streamChatCompletion } from '@/api/stream/chatStream'
import { EmptyState, ErrorState, StatusBadge, UiButton, UiCard, UiSkeleton } from '@/shared/ui'

const CONV_PAGE_SIZE = 20
const MSG_PAGE_SIZE = 20
const MAX_KB_SELECTION = 10

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
    const response = await callApi(() => listConversations({ query: { page: 1, page_size: CONV_PAGE_SIZE } }))
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
    const response = await callApi(() => listConversations({ query: { page: next, page_size: CONV_PAGE_SIZE } }))
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
    await selectConversation(response.data.id)
  } catch (error) {
    conversationError.value = describeChatError(error, '创建会话失败，请稍后重试。')
  } finally {
    creatingConversation.value = false
  }
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
const activeConversation = computed(() => conversations.value.find((item) => item.id === activeId.value) ?? null)
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
      listConversationMessages({ path: { conversation_id: conversationId }, query: { page: 1, page_size: MSG_PAGE_SIZE } }),
    )
    const total = first.data.pagination.total
    const lastPage = Math.max(1, Math.ceil(total / MSG_PAGE_SIZE))
    if (lastPage === 1) {
      messages.value = first.data.items
      earliestPage.value = 1
    } else {
      // 消息按时间升序分页：首屏直接取最后一页（最新），向上翻页加载更早历史
      const last = await callApi(() =>
        listConversationMessages({ path: { conversation_id: conversationId }, query: { page: lastPage, page_size: MSG_PAGE_SIZE } }),
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
  if (streaming.value) {
    stopStream()
  }
  citationsFor.value = null
  activeId.value = id
  await loadMessages()
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

/* ---------- 发送（同步 / 流式） ---------- */

const draft = ref('')
const mode = ref<'stream' | 'sync'>('stream')
const sending = ref(false)
const streaming = ref(false)
const sendError = ref('')
const streamNotice = ref('')
/** 同步模式：同一条草稿固定幂等键，失败重试复用，成功后更换。 */
const draftKey = ref(crypto.randomUUID())
let controller: AbortController | null = null

const canSend = computed(
  () => draft.value.trim().length > 0 && selectedKbIds.value.length > 0 && !sending.value && !streaming.value,
)

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
  if (mode.value === 'stream') {
    await sendStream(question)
  } else {
    await sendSync(question)
  }
}

async function sendSync(question: string) {
  sending.value = true
  try {
    const response = await callApi(() =>
      createChatCompletion({
        body: { conversation_id: activeId.value ?? null, question, knowledge_base_ids: selectedKbIds.value },
        headers: { 'Idempotency-Key': draftKey.value },
      }),
    )
    draft.value = ''
    draftKey.value = crypto.randomUUID()
    if (!activeId.value) {
      activeId.value = response.data.conversation.id
    }
    messages.value = [...messages.value, response.data.user_message, response.data.assistant_message]
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
      { question, knowledge_base_ids: selectedKbIds.value, conversation_id: activeId.value ?? null },
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
      messages.value = [...messages.value.slice(0, index), recovered, ...messages.value.slice(index + 1)]
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
  return message.role === 'assistant' && message.status !== 'fallback' && message.citations.length > 0
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
      createMessageFeedback({ path: { message_id: message.id }, body: { rating }, headers: { 'Idempotency-Key': key } }),
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
  void loadConversations()
  void loadKbScope()
})
onUnmounted(stopStream)
</script>

<template>
  <div class="chat">
    <aside class="chat__sidebar">
      <UiButton variant="primary" class="chat__new" :loading="creatingConversation" @click="createNewConversation">新建会话</UiButton>
      <p v-if="conversationError" class="chat__error" role="alert">{{ conversationError }}</p>
      <UiSkeleton v-if="conversationsLoading" :lines="4" />
      <ErrorState v-else-if="conversationsFailed" title="会话列表加载失败" @retry="loadConversations" />
      <EmptyState v-else-if="conversations.length === 0" title="暂无会话" description="新建会话开始知识问答" />
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
              <p class="chat__conversation-title">{{ item.title || '未命名会话' }}</p>
              <p class="chat__conversation-meta">
                {{ item.message_count }} 条消息<template v-if="item.last_message_at"> · {{ formatTime(item.last_message_at) }}</template>
              </p>
              <div class="chat__conversation-actions" @click.stop>
                <template v-if="confirmingDeleteId === item.id">
                  <UiButton size="sm" variant="danger" :loading="deletingConversation" @click="confirmDeleteConversation(item)">确认删除</UiButton>
                  <UiButton size="sm" @click="confirmingDeleteId = null">取消</UiButton>
                </template>
                <UiButton v-else size="sm" variant="text" @click="confirmingDeleteId = item.id">删除</UiButton>
              </div>
            </div>
          </li>
        </ul>
        <UiButton v-if="hasMoreConversations" size="sm" class="chat__more" @click="loadMoreConversations">加载更多会话</UiButton>
      </template>
    </aside>

    <section class="chat__main">
      <header class="chat__header">
        <div class="chat__header-text">
          <h1 class="chat__title">{{ activeConversation?.title || '知识问答' }}</h1>
          <p class="chat__subtitle">基于已发布知识库文档回答，引用可溯源</p>
        </div>
        <div class="chat__header-actions">
          <div class="chat__mode" role="radiogroup" aria-label="回答模式">
            <button
              type="button"
              class="chat__mode-btn"
              :class="{ 'chat__mode-btn--active': mode === 'stream' }"
              :disabled="streaming"
              @click="mode = 'stream'"
            >
              流式
            </button>
            <button
              type="button"
              class="chat__mode-btn"
              :class="{ 'chat__mode-btn--active': mode === 'sync' }"
              :disabled="streaming"
              @click="mode = 'sync'"
            >
              同步
            </button>
          </div>
          <UiButton size="sm" @click="kbPanelOpen = !kbPanelOpen">知识库（已选 {{ selectedKbIds.length }}）</UiButton>
        </div>
      </header>

      <UiCard v-if="kbPanelOpen" class="chat__kb-panel" padding="sm">
        <p v-if="kbError" class="chat__error" role="alert">{{ kbError }}</p>
        <template v-else>
          <p class="chat__kb-hint">选择本次问答检索的知识库（1–10 个）：</p>
          <div class="chat__kb-list">
            <label v-for="kb in kbs" :key="kb.id" class="chat__kb-item">
              <input type="checkbox" :checked="selectedKbIds.includes(kb.id)" @change="toggleKb(kb.id)" />
              <span>{{ kb.name }}</span>
            </label>
            <p v-if="kbs.length === 0" class="chat__kb-hint">暂无可访问知识库。</p>
          </div>
          <p v-if="kbNotice" class="chat__error" role="alert">{{ kbNotice }}</p>
        </template>
      </UiCard>

      <div ref="messageListEl" class="chat__messages" aria-live="polite">
        <EmptyState v-if="!activeId" title="选择或新建会话" description="从左侧选择历史会话，或点击“新建会话”开始提问" />
        <UiSkeleton v-else-if="messagesLoading" :lines="6" />
        <ErrorState v-else-if="messagesFailed" title="消息加载失败" @retry="loadMessages" />
        <template v-else>
          <div v-if="hasEarlier" class="chat__earlier">
            <UiButton size="sm" :loading="loadingEarlier" @click="loadEarlier">加载更早消息</UiButton>
          </div>
          <EmptyState v-if="messages.length === 0" title="暂无消息" description="在下方输入问题开始问答" />
          <article
            v-for="message in messages"
            :key="message.id"
            class="chat__message"
            :class="message.role === 'user' ? 'chat__message--user' : 'chat__message--assistant'"
          >
            <div class="chat__bubble">
              <div v-if="message.role === 'assistant' && message.status !== 'completed'" class="chat__bubble-status">
                <StatusBadge :status="message.status" :label="MESSAGE_STATUS_LABELS[message.status]" />
              </div>
              <p v-if="message.content" class="chat__bubble-text">{{ message.content }}</p>
              <p v-else-if="message.status === 'streaming'" class="chat__bubble-text chat__bubble-text--pending">正在生成…</p>
              <p v-else-if="message.status === 'failed'" class="chat__bubble-text chat__bubble-text--failed">
                回答生成失败{{ message.error_code ? `（${message.error_code}）` : '' }}
              </p>
              <div v-if="message.role === 'assistant'" class="chat__bubble-actions">
                <UiButton v-if="showCitations(message)" size="sm" variant="text" @click="openCitations(message)">
                  引用 {{ message.citations.length }}
                </UiButton>
                <template v-if="canFeedback(message)">
                  <button
                    type="button"
                    class="chat__feedback"
                    :class="{ 'chat__feedback--active': feedbackGiven[message.id] === 1 }"
                    :disabled="!!feedbackGiven[message.id] || feedbackBusyId === message.id"
                    :aria-label="`对回答 ${message.sequence_no} 点赞`"
                    @click="sendFeedback(message, 1)"
                  >
                    👍
                  </button>
                  <button
                    type="button"
                    class="chat__feedback"
                    :class="{ 'chat__feedback--active': feedbackGiven[message.id] === -1 }"
                    :disabled="!!feedbackGiven[message.id] || feedbackBusyId === message.id"
                    :aria-label="`对回答 ${message.sequence_no} 点踩`"
                    @click="sendFeedback(message, -1)"
                  >
                    👎
                  </button>
                </template>
              </div>
            </div>
          </article>
        </template>
      </div>

      <p v-if="feedbackError" class="chat__error" role="alert">{{ feedbackError }}</p>
      <p v-if="streamNotice" class="chat__notice" role="status">{{ streamNotice }}</p>

      <footer class="chat__composer">
        <p v-if="sendError" class="chat__error" role="alert">{{ sendError }}</p>
        <p v-else-if="selectedKbIds.length === 0 && !kbError" class="chat__hint">请先在右上角“知识库”中选择至少一个知识库。</p>
        <div class="chat__composer-row">
          <textarea
            v-model="draft"
            class="chat__input"
            rows="3"
            maxlength="2000"
            placeholder="输入问题，Enter 发送，Shift+Enter 换行"
            :disabled="streaming"
            aria-label="问题输入"
            @keydown.enter.exact.prevent="send"
          />
          <UiButton v-if="streaming" variant="danger" class="chat__send" @click="stopStream">停止</UiButton>
          <UiButton v-else variant="primary" class="chat__send" :loading="sending" :disabled="!canSend" @click="send">发送</UiButton>
        </div>
      </footer>
    </section>

    <aside v-if="citationsFor" class="chat__citations" aria-label="引用来源">
      <div class="chat__citations-head">
        <h2 class="chat__citations-title">引用来源（{{ citationsFor.citations.length }}）</h2>
        <UiButton size="sm" @click="citationsFor = null">关闭</UiButton>
      </div>
      <ol class="chat__citation-list">
        <li v-for="citation in citationsFor.citations" :key="citation.citation_no" class="chat__citation">
          <p class="chat__citation-doc">[{{ citation.citation_no }}] {{ citation.document_title }}</p>
          <p class="chat__citation-loc">
            {{ citation.source_location }}<template v-if="citation.page_number != null"> · 第 {{ citation.page_number }} 页</template>
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
  grid-template-columns: 280px minmax(0, 1fr);
  gap: var(--cp-space-4);
  height: 100%;
  min-height: 0;
}

.chat__sidebar {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-3);
  min-height: 0;
}

.chat__new {
  width: 100%;
}

.chat__conversations {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-2);
  overflow-y: auto;
}

.chat__conversation {
  padding: var(--cp-space-3);
  border: 1px solid var(--cp-hairline);
  border-radius: var(--cp-radius-card);
  background: var(--cp-surface-card);
  cursor: pointer;
}

.chat__conversation--active {
  border-color: var(--cp-primary);
}

.chat__conversation-title {
  margin: 0;
  font-size: 14px;
  color: var(--cp-ink);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.chat__conversation-meta {
  margin: var(--cp-space-1) 0 0;
  font-size: 12px;
  color: var(--cp-muted-soft);
}

.chat__conversation-actions {
  display: flex;
  gap: var(--cp-space-1);
  margin-top: var(--cp-space-1);
  justify-content: flex-end;
}

.chat__more {
  align-self: center;
}

.chat__main {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-3);
  min-height: 0;
}

.chat__header {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: var(--cp-space-3);
  flex-wrap: wrap;
}

.chat__title {
  margin: 0;
  font-size: 20px;
  color: var(--cp-ink);
}

.chat__subtitle {
  margin: var(--cp-space-1) 0 0;
  font-size: 13px;
  color: var(--cp-muted);
}

.chat__header-actions {
  display: flex;
  align-items: center;
  gap: var(--cp-space-2);
}

.chat__mode {
  display: flex;
  border: 1px solid var(--cp-hairline-strong);
  border-radius: var(--cp-radius-pill);
  overflow: hidden;
}

.chat__mode-btn {
  min-height: 32px;
  padding: 0 var(--cp-space-3);
  border: none;
  background: var(--cp-surface-card);
  color: var(--cp-body);
  font-size: 13px;
  cursor: pointer;
}

.chat__mode-btn--active {
  background: var(--cp-ink);
  color: var(--cp-canvas);
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
  min-height: 320px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-3);
  padding: var(--cp-space-3);
  border: 1px solid var(--cp-hairline);
  border-radius: var(--cp-radius-card);
  background: var(--cp-canvas-soft);
}

.chat__earlier {
  display: flex;
  justify-content: center;
}

.chat__message {
  display: flex;
}

.chat__message--user {
  justify-content: flex-end;
}

.chat__message--assistant {
  justify-content: flex-start;
}

.chat__bubble {
  max-width: 78%;
  padding: var(--cp-space-3);
  border: 1px solid var(--cp-hairline);
  border-radius: var(--cp-radius-card);
  background: var(--cp-surface-card);
}

.chat__message--user .chat__bubble {
  background: color-mix(in srgb, var(--cp-primary) 8%, white);
  border-color: color-mix(in srgb, var(--cp-primary) 30%, transparent);
}

.chat__bubble-status {
  margin-bottom: var(--cp-space-1);
}

.chat__bubble-text {
  margin: 0;
  font-size: 14px;
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

.chat__composer {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-2);
}

.chat__composer-row {
  display: flex;
  gap: var(--cp-space-2);
  align-items: flex-end;
}

.chat__input {
  flex: 1;
  padding: var(--cp-space-3);
  border: 1px solid var(--cp-hairline-strong);
  border-radius: var(--cp-radius-button);
  background: var(--cp-surface-card);
  color: var(--cp-ink);
  font-family: var(--cp-font-sans);
  font-size: 14px;
  resize: vertical;
}

.chat__send {
  flex-shrink: 0;
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
  grid-column: 3;
  width: 320px;
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

@media (max-width: 1024px) {
  .chat {
    grid-template-columns: minmax(0, 1fr);
  }

  .chat__citations {
    grid-column: auto;
    width: auto;
  }
}
</style>

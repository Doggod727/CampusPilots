<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { ApiError, callApi } from '@/api/client'
import { createPost, deletePost, listPosts, listTopics, updatePost } from '@/api/generated'
import type { CommunityContentStatus, Post, Topic } from '@/api/generated'
import { useAuthStore } from '@/modules/auth/stores/auth'
import { EmptyState, ErrorState, PageHeader, StatusBadge, UiButton, UiCard, UiField, UiPagination, UiSkeleton } from '@/shared/ui'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const items = ref<Post[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = 10
const loading = ref(true)
const failed = ref(false)

const topicId = computed(() => (typeof route.query.topicId === 'string' ? route.query.topicId : ''))
const searchInput = ref('')
const appliedQuery = ref('')
const mineOnly = ref(false)
const sort = ref<'-published_at' | 'published_at'>('-published_at')

const canWrite = computed(() => auth.hasPermission('community:write'))

/** 作者本人或运营员可编辑/删除；匿名帖不回传作者 ID，前端不做越权展示。 */
function canManage(post: Post): boolean {
  if (!canWrite.value) {
    return false
  }
  if (auth.hasPermission('community:moderate')) {
    return true
  }
  return post.author.user_id != null && post.author.user_id === auth.user?.id
}

async function load() {
  loading.value = true
  failed.value = false
  try {
    const response = await callApi(() =>
      listPosts({
        query: {
          page: page.value,
          page_size: pageSize,
          ...(topicId.value ? { topic_id: topicId.value } : {}),
          ...(appliedQuery.value ? { q: appliedQuery.value } : {}),
          ...(mineOnly.value ? { mine: true } : {}),
          sort: sort.value,
        },
      }),
    )
    items.value = response.data.items
    total.value = response.data.pagination.total
  } catch {
    failed.value = true
  } finally {
    loading.value = false
  }
}

async function changePage(next: number) {
  page.value = next
  await load()
}

async function applySearch() {
  appliedQuery.value = searchInput.value.trim()
  page.value = 1
  await load()
}

async function changeSort(next: '-published_at' | 'published_at') {
  sort.value = next
  page.value = 1
  await load()
}

async function toggleMine(value: boolean) {
  mineOnly.value = value
  page.value = 1
  await load()
}

function clearTopicFilter() {
  void router.push({ name: 'community-posts' })
}

function openDetail(post: Post) {
  void router.push({ name: 'community-post-detail', params: { postId: post.id } })
}

watch(topicId, () => {
  page.value = 1
  void load()
})

/* ---------- 发帖 ---------- */

const createOpen = ref(false)
const createSubmitting = ref(false)
const createFailure = ref('')
/** 对话框打开时生成一次并复用：重试不会产生重复帖子。 */
const createKey = ref('')
const topics = ref<Topic[]>([])
const topicsLoaded = ref(false)
const createForm = reactive({ topic_id: '', title: '', content_markdown: '', is_anonymous: false })

const selectedTopic = computed(() => topics.value.find((topic) => topic.id === createForm.topic_id) ?? null)
const canSubmitCreate = computed(
  () =>
    createForm.topic_id.length > 0 &&
    createForm.title.trim().length > 0 &&
    createForm.content_markdown.trim().length > 0 &&
    !createSubmitting.value,
)

async function openCreate() {
  createForm.topic_id = topicId.value || ''
  createForm.title = ''
  createForm.content_markdown = ''
  createForm.is_anonymous = false
  createFailure.value = ''
  createKey.value = crypto.randomUUID()
  createOpen.value = true
  if (!topicsLoaded.value) {
    try {
      const response = await callApi(() => listTopics({ query: { page: 1, page_size: 50, status: 'active' } }))
      topics.value = response.data.items
      topicsLoaded.value = true
    } catch {
      topics.value = []
      createFailure.value = '话题列表加载失败，请关闭后重试'
    }
  }
}

watch(selectedTopic, (topic) => {
  if (!topic?.allow_anonymous) {
    createForm.is_anonymous = false
  }
})

async function submitCreate() {
  if (!canSubmitCreate.value) {
    return
  }
  createSubmitting.value = true
  createFailure.value = ''
  try {
    await callApi(() =>
      createPost({
        body: {
          topic_id: createForm.topic_id,
          title: createForm.title.trim(),
          content_markdown: createForm.content_markdown.trim(),
          is_anonymous: createForm.is_anonymous,
        },
        headers: { 'Idempotency-Key': createKey.value },
      }),
    )
    createOpen.value = false
    await load()
  } catch (error) {
    createFailure.value = describeError(error, 'create')
  } finally {
    createSubmitting.value = false
  }
}

/* ---------- 编辑 / 删除本人帖子 ---------- */

const editOpen = ref(false)
const editSubmitting = ref(false)
const editFailure = ref('')
const editingPost = ref<Post | null>(null)
const editForm = reactive({ title: '', content_markdown: '' })

const canSubmitEdit = computed(
  () => editForm.title.trim().length > 0 && editForm.content_markdown.trim().length > 0 && !editSubmitting.value,
)

function openEdit(post: Post) {
  editingPost.value = post
  editForm.title = post.title
  editForm.content_markdown = post.content_markdown
  editFailure.value = ''
  editOpen.value = true
}

async function submitEdit() {
  const post = editingPost.value
  if (!post || !canSubmitEdit.value) {
    return
  }
  editSubmitting.value = true
  editFailure.value = ''
  try {
    await callApi(() =>
      updatePost({
        path: { post_id: post.id },
        body: {
          title: editForm.title.trim(),
          content_markdown: editForm.content_markdown.trim(),
          version: post.version,
        },
      }),
    )
    editOpen.value = false
    await load()
  } catch (error) {
    editFailure.value = describeError(error, 'update')
  } finally {
    editSubmitting.value = false
  }
}

const deleteFailure = ref('')
const deletingId = ref<string | null>(null)

async function removePost(post: Post) {
  if (deletingId.value !== null) {
    return
  }
  deletingId.value = post.id
  deleteFailure.value = ''
  try {
    await callApi(() => deletePost({ path: { post_id: post.id } }))
    await load()
  } catch (error) {
    deleteFailure.value = describeError(error, 'delete')
  } finally {
    deletingId.value = null
  }
}

function describeError(error: unknown, operation: 'create' | 'update' | 'delete'): string {
  if (error instanceof ApiError) {
    if (error.status === 403) {
      return '当前账号没有发帖或管理权限'
    }
    if (error.status === 404) {
      return '帖子不存在或已被删除，请刷新列表'
    }
    if (error.status === 409) {
      if (operation === 'update') {
        return '版本冲突：帖子刚被修改，请刷新后重试'
      }
      return '操作冲突，请刷新后重试'
    }
    if (error.status === 422) {
      return error.details[0]?.reason ?? '输入内容不符合要求'
    }
    if (error.status === 429) {
      return '操作过于频繁，请稍后再试'
    }
  }
  return '服务暂不可用，请稍后重试'
}

const STATUS_LABELS: Record<CommunityContentStatus, string> = {
  published: '已发布',
  pending_review: '审核中',
  rejected: '未通过',
  hidden: '已隐藏',
  deleted: '已删除',
}

function formatTime(value?: string | null): string {
  if (!value) {
    return ''
  }
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

onMounted(load)
</script>

<template>
  <div class="posts">
    <PageHeader title="社区帖子" subtitle="浏览、搜索并发帖；内容的可见性以后端返回为准">
      <UiButton v-if="canWrite" variant="primary" @click="openCreate">发帖</UiButton>
    </PageHeader>

    <div class="posts__toolbar">
      <form class="posts__search" @submit.prevent="applySearch">
        <input v-model="searchInput" class="posts__search-input" type="search" placeholder="搜索帖子标题或内容" aria-label="搜索帖子" />
        <UiButton type="submit">搜索</UiButton>
      </form>
      <div class="posts__sort" role="tablist">
        <button
          type="button"
          class="posts__sort-btn"
          :class="{ 'posts__sort-btn--active': sort === '-published_at' }"
          @click="changeSort('-published_at')"
        >
          最新
        </button>
        <button
          type="button"
          class="posts__sort-btn"
          :class="{ 'posts__sort-btn--active': sort === 'published_at' }"
          @click="changeSort('published_at')"
        >
          最早
        </button>
      </div>
      <label class="posts__mine">
        <input :checked="mineOnly" type="checkbox" @change="toggleMine(($event.target as HTMLInputElement).checked)" />
        只看我的
      </label>
    </div>

    <div v-if="topicId" class="posts__topic-filter">
      正在按话题过滤
      <UiButton size="sm" variant="text" @click="clearTopicFilter">清除过滤</UiButton>
    </div>

    <p v-if="deleteFailure" class="posts__alert" role="alert">{{ deleteFailure }}</p>

    <UiSkeleton v-if="loading" :lines="5" />
    <ErrorState v-else-if="failed" title="帖子列表加载失败" @retry="load" />
    <EmptyState v-else-if="items.length === 0" title="暂无帖子" description="换个筛选条件，或发布第一篇帖子" />
    <template v-else>
      <div class="posts__list">
        <UiCard v-for="post in items" :key="post.id" class="posts__item" padding="md" @click="openDetail(post)">
          <div class="posts__item-head">
            <strong class="posts__title">{{ post.title }}</strong>
            <StatusBadge v-if="post.status !== 'published'" :status="post.status" :label="STATUS_LABELS[post.status]" />
            <span v-if="post.is_anonymous" class="posts__tag">匿名</span>
            <time class="posts__time">{{ formatTime(post.published_at ?? post.created_at) }}</time>
          </div>
          <p class="posts__preview">{{ post.content_markdown.slice(0, 120) }}</p>
          <div class="posts__meta">
            <span class="posts__topic">{{ post.topic.name }}</span>
            <span class="posts__author">{{ post.author.display_name }}</span>
            <span class="posts__counts">赞 {{ post.like_count }} · 收藏 {{ post.favorite_count }} · 评论 {{ post.comment_count }}</span>
            <span v-if="canManage(post)" class="posts__actions" @click.stop>
              <UiButton size="sm" @click="openEdit(post)">编辑</UiButton>
              <el-popconfirm
                title="确认删除该帖子？删除后不可恢复"
                confirm-button-text="删除"
                cancel-button-text="取消"
                width="220"
                @confirm="removePost(post)"
              >
                <template #reference>
                  <UiButton size="sm" variant="danger" :loading="deletingId === post.id">删除</UiButton>
                </template>
              </el-popconfirm>
            </span>
          </div>
        </UiCard>
      </div>
      <div class="posts__pagination">
        <UiPagination :page="page" :total="total" :page-size="pageSize" @change="changePage" />
      </div>
    </template>

    <el-dialog v-model="createOpen" title="发帖" width="560px">
      <form class="posts__form" @submit.prevent="submitCreate">
        <UiField label="所属话题" input-id="post-topic" required>
          <select id="post-topic" v-model="createForm.topic_id" class="posts__input" :disabled="createSubmitting">
            <option value="" disabled>请选择话题</option>
            <option v-for="topic in topics" :key="topic.id" :value="topic.id">{{ topic.name }}</option>
          </select>
        </UiField>
        <UiField label="标题" input-id="post-title" required>
          <input id="post-title" v-model="createForm.title" class="posts__input" maxlength="100" :disabled="createSubmitting" />
        </UiField>
        <UiField label="内容" input-id="post-content" required>
          <textarea id="post-content" v-model="createForm.content_markdown" class="posts__input" rows="6" maxlength="20000" :disabled="createSubmitting" />
        </UiField>
        <label v-if="selectedTopic?.allow_anonymous" class="posts__check">
          <input v-model="createForm.is_anonymous" type="checkbox" :disabled="createSubmitting" />
          匿名发布（其他用户无法看到你的身份）
        </label>
        <p v-if="createFailure" class="posts__form-error" role="alert">{{ createFailure }}</p>
        <div class="posts__form-actions">
          <UiButton @click="createOpen = false">取消</UiButton>
          <UiButton variant="primary" type="submit" :loading="createSubmitting" :disabled="!canSubmitCreate">发布</UiButton>
        </div>
      </form>
    </el-dialog>

    <el-dialog v-model="editOpen" title="编辑帖子" width="560px">
      <form class="posts__form" @submit.prevent="submitEdit">
        <UiField label="标题" input-id="post-edit-title" required>
          <input id="post-edit-title" v-model="editForm.title" class="posts__input" maxlength="100" :disabled="editSubmitting" />
        </UiField>
        <UiField label="内容" input-id="post-edit-content" required>
          <textarea id="post-edit-content" v-model="editForm.content_markdown" class="posts__input" rows="6" maxlength="20000" :disabled="editSubmitting" />
        </UiField>
        <p v-if="editFailure" class="posts__form-error" role="alert">{{ editFailure }}</p>
        <div class="posts__form-actions">
          <UiButton @click="editOpen = false">取消</UiButton>
          <UiButton variant="primary" type="submit" :loading="editSubmitting" :disabled="!canSubmitEdit">保存</UiButton>
        </div>
      </form>
    </el-dialog>
  </div>
</template>

<style scoped>
.posts {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-4);
}

.posts__toolbar {
  display: flex;
  align-items: center;
  gap: var(--cp-space-3);
  flex-wrap: wrap;
}

.posts__search {
  display: flex;
  gap: var(--cp-space-2);
  align-items: center;
}

.posts__search-input {
  min-height: var(--cp-control-md);
  width: 260px;
  padding: 0 var(--cp-space-3);
  border: 1px solid var(--cp-hairline-strong);
  border-radius: var(--cp-radius-button);
  font-family: var(--cp-font-sans);
  font-size: 14px;
  color: var(--cp-ink);
  background: var(--cp-surface-card);
}

.posts__sort {
  display: flex;
  gap: var(--cp-space-1);
}

.posts__sort-btn {
  min-height: 32px;
  padding: 0 var(--cp-space-3);
  border: 1px solid var(--cp-hairline-strong);
  border-radius: var(--cp-radius-pill);
  background: var(--cp-surface-card);
  color: var(--cp-body);
  font-size: 13px;
  cursor: pointer;
}

.posts__sort-btn--active {
  background: var(--cp-ink);
  border-color: var(--cp-ink);
  color: var(--cp-canvas);
}

.posts__mine {
  display: flex;
  align-items: center;
  gap: var(--cp-space-2);
  font-size: 13px;
  color: var(--cp-body);
  cursor: pointer;
}

.posts__topic-filter {
  display: flex;
  align-items: center;
  gap: var(--cp-space-2);
  font-size: 13px;
  color: var(--cp-muted);
}

.posts__alert {
  margin: 0;
  padding: var(--cp-space-3);
  border: 1px solid color-mix(in srgb, var(--cp-error) 35%, transparent);
  border-radius: var(--cp-radius-button);
  background: color-mix(in srgb, var(--cp-error) 6%, white);
  color: var(--cp-error);
  font-size: 13px;
}

.posts__list {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-3);
}

.posts__item {
  cursor: pointer;
  transition: border-color 0.15s ease;
}

.posts__item:hover {
  border-color: var(--cp-muted);
}

.posts__item-head {
  display: flex;
  align-items: center;
  gap: var(--cp-space-2);
  flex-wrap: wrap;
}

.posts__title {
  font-size: 15px;
  color: var(--cp-ink);
}

.posts__tag {
  font-size: 12px;
  color: var(--cp-info);
  border: 1px solid color-mix(in srgb, var(--cp-info) 35%, transparent);
  border-radius: var(--cp-radius-pill);
  padding: 2px 10px;
}

.posts__time {
  margin-left: auto;
  font-size: 12px;
  color: var(--cp-muted-soft);
}

.posts__preview {
  margin: var(--cp-space-2) 0 0;
  color: var(--cp-muted);
  font-size: 13px;
}

.posts__meta {
  margin-top: var(--cp-space-3);
  display: flex;
  align-items: center;
  gap: var(--cp-space-3);
  flex-wrap: wrap;
  font-size: 12px;
  color: var(--cp-muted);
}

.posts__topic {
  color: var(--cp-primary);
}

.posts__actions {
  margin-left: auto;
  display: flex;
  gap: var(--cp-space-2);
  align-items: center;
}

.posts__pagination {
  display: flex;
  justify-content: center;
}

.posts__form {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-4);
}

.posts__input {
  width: 100%;
  min-height: var(--cp-control-md);
  padding: var(--cp-space-2) var(--cp-space-3);
  border: 1px solid var(--cp-hairline-strong);
  border-radius: var(--cp-radius-button);
  font-family: var(--cp-font-sans);
  font-size: 14px;
  color: var(--cp-ink);
  background: var(--cp-surface-card);
  box-sizing: border-box;
}

textarea.posts__input {
  resize: vertical;
}

.posts__check {
  display: flex;
  align-items: center;
  gap: var(--cp-space-2);
  font-size: 13px;
  color: var(--cp-body);
  cursor: pointer;
}

.posts__form-error {
  margin: 0;
  padding: var(--cp-space-3);
  border: 1px solid color-mix(in srgb, var(--cp-error) 35%, transparent);
  border-radius: var(--cp-radius-button);
  background: color-mix(in srgb, var(--cp-error) 6%, white);
  color: var(--cp-error);
  font-size: 13px;
}

.posts__form-actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--cp-space-2);
}
</style>

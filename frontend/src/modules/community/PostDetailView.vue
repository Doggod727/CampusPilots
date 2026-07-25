<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { ApiError, callApi } from '@/api/client'
import {
  createComment,
  createContentReport,
  deleteComment,
  deletePostReaction,
  getPost,
  listPostComments,
  putPostReaction,
} from '@/api/generated'
import type { Comment, ContentReportReason, Post, ReactionType } from '@/api/generated'
import { useAuthStore } from '@/modules/auth/stores/auth'
import { EmptyState, ErrorState, PageHeader, UiButton, UiCard, UiField, UiPagination, UiSkeleton } from '@/shared/ui'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const postId = computed(() => String(route.params.postId ?? ''))
const canWrite = computed(() => auth.hasPermission('community:write'))

/* ---------- 帖子详情 ---------- */

const post = ref<Post | null>(null)
const loading = ref(true)
const failed = ref(false)
/** 404 与无权感知统一安全空态，不区分原因。 */
const notFound = ref(false)

async function loadPost() {
  loading.value = true
  failed.value = false
  notFound.value = false
  try {
    const response = await callApi(() => getPost({ path: { post_id: postId.value } }))
    post.value = response.data
    syncReactions(response.data)
  } catch (error) {
    post.value = null
    if (error instanceof ApiError && error.status === 404) {
      notFound.value = true
    } else {
      failed.value = true
    }
  } finally {
    loading.value = false
  }
}

/* ---------- 点赞 / 收藏（幂等；计数以后端响应为准） ---------- */

const liked = ref(false)
const favorited = ref(false)
const likeCount = ref(0)
const favoriteCount = ref(0)
const reactionPending = ref<ReactionType | null>(null)
const reactionFailure = ref('')

function syncReactions(value: Post) {
  liked.value = value.interaction.liked
  favorited.value = value.interaction.favorited
  likeCount.value = value.like_count
  favoriteCount.value = value.favorite_count
}

async function toggleReaction(type: ReactionType) {
  if (!post.value || reactionPending.value !== null) {
    return
  }
  reactionPending.value = type
  reactionFailure.value = ''
  try {
    const active = type === 'like' ? liked.value : favorited.value
    const response = await callApi(() =>
      active
        ? deletePostReaction({ path: { post_id: postId.value, reaction_type: type } })
        : putPostReaction({ path: { post_id: postId.value, reaction_type: type } }),
    )
    if (type === 'like') {
      liked.value = response.data.active
    } else {
      favorited.value = response.data.active
    }
    likeCount.value = response.data.like_count
    favoriteCount.value = response.data.favorite_count
  } catch {
    reactionFailure.value = '操作失败，请稍后重试'
  } finally {
    reactionPending.value = null
  }
}

/* ---------- 评论区 ---------- */

const comments = ref<Comment[]>([])
const commentsTotal = ref(0)
const commentsPage = ref(1)
const commentsPageSize = 10
const commentsLoading = ref(true)
const commentsFailed = ref(false)

async function loadComments() {
  commentsLoading.value = true
  commentsFailed.value = false
  try {
    const response = await callApi(() =>
      listPostComments({ path: { post_id: postId.value }, query: { page: commentsPage.value, page_size: commentsPageSize } }),
    )
    comments.value = response.data.items
    commentsTotal.value = response.data.pagination.total
  } catch {
    commentsFailed.value = true
  } finally {
    commentsLoading.value = false
  }
}

async function changeCommentsPage(next: number) {
  commentsPage.value = next
  await loadComments()
}

const commentForm = reactive({ content: '', is_anonymous: false })
const commentSubmitting = ref(false)
const commentFailure = ref('')
const replyTo = ref<Comment | null>(null)
/** 同一编辑会话固定幂等键：提交失败重试复用，成功后更换。 */
const commentKey = ref(crypto.randomUUID())

const allowAnonymousComment = computed(() => post.value?.topic.allow_anonymous ?? false)
const canSubmitComment = computed(() => commentForm.content.trim().length > 0 && !commentSubmitting.value)

function canManageComment(comment: Comment): boolean {
  if (!canWrite.value) {
    return false
  }
  if (auth.hasPermission('community:moderate')) {
    return true
  }
  return comment.author.user_id != null && comment.author.user_id === auth.user?.id
}

function startReply(comment: Comment) {
  replyTo.value = comment
  commentFailure.value = ''
}

function cancelReply() {
  replyTo.value = null
}

async function submitComment() {
  if (!canSubmitComment.value) {
    return
  }
  commentSubmitting.value = true
  commentFailure.value = ''
  try {
    await callApi(() =>
      createComment({
        path: { post_id: postId.value },
        body: {
          content_markdown: commentForm.content.trim(),
          is_anonymous: commentForm.is_anonymous,
          ...(replyTo.value ? { parent_comment_id: replyTo.value.id } : {}),
        },
        headers: { 'Idempotency-Key': commentKey.value },
      }),
    )
    commentKey.value = crypto.randomUUID()
    commentForm.content = ''
    commentForm.is_anonymous = false
    replyTo.value = null
    await loadComments()
    if (post.value) {
      post.value = { ...post.value, comment_count: post.value.comment_count + 1 }
    }
  } catch (error) {
    commentFailure.value = describeError(error)
  } finally {
    commentSubmitting.value = false
  }
}

const commentDeleteFailure = ref('')
const deletingCommentId = ref<string | null>(null)

async function removeComment(comment: Comment) {
  if (deletingCommentId.value !== null) {
    return
  }
  deletingCommentId.value = comment.id
  commentDeleteFailure.value = ''
  try {
    await callApi(() => deleteComment({ path: { comment_id: comment.id } }))
    await loadComments()
    if (post.value && post.value.comment_count > 0) {
      post.value = { ...post.value, comment_count: post.value.comment_count - 1 }
    }
  } catch (error) {
    commentDeleteFailure.value = describeError(error)
  } finally {
    deletingCommentId.value = null
  }
}

/* ---------- 举报 ---------- */

const REPORT_REASONS: Array<{ value: ContentReportReason; label: string }> = [
  { value: 'spam', label: '垃圾信息' },
  { value: 'abuse', label: '辱骂攻击' },
  { value: 'privacy', label: '泄露隐私' },
  { value: 'fraud', label: '诈骗' },
  { value: 'unsafe', label: '不安全内容' },
  { value: 'other', label: '其他' },
]

const reportOpen = ref(false)
const reportSubmitting = ref(false)
const reportFailure = ref('')
const reportDone = ref(false)
/** 对话框一次会话固定幂等键：重复提交由后端幂等合并。 */
const reportKey = ref('')
const reportForm = reactive({ reason_code: 'spam' as ContentReportReason, details: '' })

const canSubmitReport = computed(() => reportForm.details.trim().length > 0 && !reportSubmitting.value)

function openReport() {
  reportForm.reason_code = 'spam'
  reportForm.details = ''
  reportFailure.value = ''
  reportKey.value = crypto.randomUUID()
  reportOpen.value = true
}

async function submitReport() {
  if (!post.value || !canSubmitReport.value) {
    return
  }
  reportSubmitting.value = true
  reportFailure.value = ''
  try {
    await callApi(() =>
      createContentReport({
        body: {
          target_type: 'post',
          target_id: post.value!.id,
          reason_code: reportForm.reason_code,
          details: reportForm.details.trim(),
        },
        headers: { 'Idempotency-Key': reportKey.value },
      }),
    )
    reportOpen.value = false
    reportDone.value = true
  } catch (error) {
    reportFailure.value = describeError(error)
  } finally {
    reportSubmitting.value = false
  }
}

function describeError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 403) {
      return '当前账号没有执行该操作的权限'
    }
    if (error.status === 404) {
      return '目标内容不存在或已不可见'
    }
    if (error.status === 409) {
      return '相同请求已处理过，请勿重复操作'
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

function formatTime(value?: string | null): string {
  if (!value) {
    return ''
  }
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

onMounted(async () => {
  await loadPost()
  if (post.value) {
    await loadComments()
  } else {
    commentsLoading.value = false
  }
})
</script>

<template>
  <div class="detail">
    <PageHeader title="帖子详情">
      <UiButton @click="router.push({ name: 'community-posts' })">返回列表</UiButton>
    </PageHeader>

    <UiSkeleton v-if="loading" :lines="6" />
    <EmptyState
      v-else-if="notFound"
      title="内容不存在或不可见"
      description="该帖子可能已删除、未通过审核，或你无权查看"
    />
    <ErrorState v-else-if="failed" title="帖子加载失败" @retry="loadPost" />

    <template v-else-if="post">
      <UiCard class="detail__post" padding="lg">
        <div class="detail__head">
          <span class="detail__topic">{{ post.topic.name }}</span>
          <span v-if="post.is_anonymous" class="detail__tag">匿名</span>
          <time class="detail__time">{{ formatTime(post.published_at ?? post.created_at) }}</time>
        </div>
        <h2 class="detail__title">{{ post.title }}</h2>
        <p class="detail__author">作者：{{ post.author.display_name }}</p>
        <p class="detail__content">{{ post.content_markdown }}</p>

        <div class="detail__reactions">
          <template v-if="canWrite">
            <button
              type="button"
              class="detail__reaction"
              :class="{ 'detail__reaction--active': liked }"
              :disabled="reactionPending !== null"
              @click="toggleReaction('like')"
            >
              {{ liked ? '已点赞' : '点赞' }} {{ likeCount }}
            </button>
            <button
              type="button"
              class="detail__reaction"
              :class="{ 'detail__reaction--active': favorited }"
              :disabled="reactionPending !== null"
              @click="toggleReaction('favorite')"
            >
              {{ favorited ? '已收藏' : '收藏' }} {{ favoriteCount }}
            </button>
          </template>
          <template v-else>
            <span class="detail__reaction-static">点赞 {{ likeCount }}</span>
            <span class="detail__reaction-static">收藏 {{ favoriteCount }}</span>
          </template>
          <UiButton v-if="canWrite" size="sm" variant="text" @click="openReport">举报</UiButton>
        </div>
        <p v-if="reactionFailure" class="detail__inline-error" role="alert">{{ reactionFailure }}</p>
        <p v-if="reportDone" class="detail__report-done" role="status">举报已提交，感谢你的反馈</p>
      </UiCard>

      <section class="detail__comments">
        <h3 class="detail__comments-title">评论（{{ post.comment_count }}）</h3>

        <UiCard v-if="canWrite" class="detail__composer" padding="md">
          <form class="detail__composer-form" @submit.prevent="submitComment">
            <p v-if="replyTo" class="detail__replying">
              正在回复 {{ replyTo.author.display_name }}
              <UiButton size="sm" variant="text" @click="cancelReply">取消回复</UiButton>
            </p>
            <UiField label="发表评论" input-id="comment-content" required>
              <textarea
                id="comment-content"
                v-model="commentForm.content"
                class="detail__input"
                rows="3"
                maxlength="5000"
                placeholder="友善交流，理性讨论"
                :disabled="commentSubmitting"
              />
            </UiField>
            <label v-if="allowAnonymousComment" class="detail__check">
              <input v-model="commentForm.is_anonymous" type="checkbox" :disabled="commentSubmitting" />
              匿名评论
            </label>
            <p v-if="commentFailure" class="detail__inline-error" role="alert">{{ commentFailure }}</p>
            <div class="detail__composer-actions">
              <UiButton variant="primary" type="submit" :loading="commentSubmitting" :disabled="!canSubmitComment">
                发表
              </UiButton>
            </div>
          </form>
        </UiCard>

        <p v-if="commentDeleteFailure" class="detail__inline-error" role="alert">{{ commentDeleteFailure }}</p>

        <UiSkeleton v-if="commentsLoading" :lines="3" />
        <ErrorState v-else-if="commentsFailed" title="评论加载失败" @retry="loadComments" />
        <EmptyState v-else-if="comments.length === 0" title="暂无评论" description="来发表第一条评论吧" />
        <template v-else>
          <UiCard v-for="comment in comments" :key="comment.id" class="detail__comment" padding="md">
            <div class="detail__comment-head">
              <strong class="detail__comment-author">{{ comment.author.display_name }}</strong>
              <span v-if="comment.is_anonymous" class="detail__tag">匿名</span>
              <time class="detail__comment-time">{{ formatTime(comment.published_at ?? comment.created_at) }}</time>
              <span v-if="canWrite" class="detail__comment-actions">
                <UiButton size="sm" variant="text" @click="startReply(comment)">回复</UiButton>
                <el-popconfirm
                  v-if="canManageComment(comment)"
                  title="确认删除该评论？"
                  confirm-button-text="删除"
                  cancel-button-text="取消"
                  width="200"
                  @confirm="removeComment(comment)"
                >
                  <template #reference>
                    <UiButton size="sm" variant="text" :loading="deletingCommentId === comment.id">删除</UiButton>
                  </template>
                </el-popconfirm>
              </span>
            </div>
            <p class="detail__comment-content">{{ comment.content_markdown }}</p>
          </UiCard>
          <div class="detail__pagination">
            <UiPagination :page="commentsPage" :total="commentsTotal" :page-size="commentsPageSize" @change="changeCommentsPage" />
          </div>
        </template>
      </section>
    </template>

    <el-dialog v-model="reportOpen" title="举报帖子" width="480px">
      <form class="detail__form" @submit.prevent="submitReport">
        <UiField label="举报类型" input-id="report-reason" required>
          <select id="report-reason" v-model="reportForm.reason_code" class="detail__input" :disabled="reportSubmitting">
            <option v-for="reason in REPORT_REASONS" :key="reason.value" :value="reason.value">{{ reason.label }}</option>
          </select>
        </UiField>
        <UiField label="理由说明" input-id="report-details" required hint="请描述举报原因，提交后进入平台审核">
          <textarea
            id="report-details"
            v-model="reportForm.details"
            class="detail__input"
            rows="4"
            maxlength="1000"
            :disabled="reportSubmitting"
          />
        </UiField>
        <p v-if="reportFailure" class="detail__inline-error" role="alert">{{ reportFailure }}</p>
        <div class="detail__form-actions">
          <UiButton @click="reportOpen = false">取消</UiButton>
          <UiButton variant="primary" type="submit" :loading="reportSubmitting" :disabled="!canSubmitReport">提交举报</UiButton>
        </div>
      </form>
    </el-dialog>
  </div>
</template>

<style scoped>
.detail {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-4);
  max-width: 860px;
}

.detail__head {
  display: flex;
  align-items: center;
  gap: var(--cp-space-2);
  flex-wrap: wrap;
}

.detail__topic {
  font-size: 13px;
  color: var(--cp-primary);
}

.detail__tag {
  font-size: 12px;
  color: var(--cp-info);
  border: 1px solid color-mix(in srgb, var(--cp-info) 35%, transparent);
  border-radius: var(--cp-radius-pill);
  padding: 2px 10px;
}

.detail__time {
  margin-left: auto;
  font-size: 12px;
  color: var(--cp-muted-soft);
}

.detail__title {
  margin: var(--cp-space-3) 0 0;
  font-size: 22px;
  color: var(--cp-ink);
}

.detail__author {
  margin: var(--cp-space-1) 0 0;
  font-size: 13px;
  color: var(--cp-muted);
}

.detail__content {
  margin: var(--cp-space-4) 0 0;
  font-size: 14px;
  color: var(--cp-ink);
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
}

.detail__reactions {
  margin-top: var(--cp-space-4);
  display: flex;
  align-items: center;
  gap: var(--cp-space-2);
}

.detail__reaction {
  min-height: var(--cp-control-sm);
  padding: 0 var(--cp-space-3);
  border: 1px solid var(--cp-hairline-strong);
  border-radius: var(--cp-radius-pill);
  background: var(--cp-surface-card);
  color: var(--cp-body);
  font-size: 13px;
  cursor: pointer;
}

.detail__reaction--active {
  border-color: var(--cp-primary);
  color: var(--cp-primary);
  background: color-mix(in srgb, var(--cp-primary) 6%, white);
}

.detail__reaction:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.detail__reaction-static {
  font-size: 13px;
  color: var(--cp-muted);
}

.detail__inline-error {
  margin: var(--cp-space-2) 0 0;
  font-size: 13px;
  color: var(--cp-error);
}

.detail__report-done {
  margin: var(--cp-space-2) 0 0;
  font-size: 13px;
  color: var(--cp-success);
}

.detail__comments {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-3);
}

.detail__comments-title {
  margin: 0;
  font-size: 16px;
  color: var(--cp-ink);
}

.detail__composer-form {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-3);
}

.detail__replying {
  margin: 0;
  display: flex;
  align-items: center;
  gap: var(--cp-space-2);
  font-size: 13px;
  color: var(--cp-muted);
}

.detail__input {
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

textarea.detail__input {
  resize: vertical;
}

.detail__check {
  display: flex;
  align-items: center;
  gap: var(--cp-space-2);
  font-size: 13px;
  color: var(--cp-body);
  cursor: pointer;
}

.detail__composer-actions {
  display: flex;
  justify-content: flex-end;
}

.detail__comment-head {
  display: flex;
  align-items: center;
  gap: var(--cp-space-2);
  flex-wrap: wrap;
}

.detail__comment-author {
  font-size: 13px;
  color: var(--cp-ink);
}

.detail__comment-time {
  font-size: 12px;
  color: var(--cp-muted-soft);
}

.detail__comment-actions {
  margin-left: auto;
  display: flex;
  gap: var(--cp-space-1);
  align-items: center;
}

.detail__comment-content {
  margin: var(--cp-space-2) 0 0;
  font-size: 14px;
  color: var(--cp-body);
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}

.detail__pagination {
  display: flex;
  justify-content: center;
}

.detail__form {
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-4);
}

.detail__form-actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--cp-space-2);
}
</style>

from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from app.modules.agent_platform.domain.contracts import ToolInvocationContext, UserContext
from app.modules.agent_platform.tool_gateway.catalog import (
    CommunityPostPublishInput, CommunityPostPublishOutput,
    CommunityTopicSummaryInput, CommunityTopicSummaryItem, CommunityTopicSummaryOutput,
    EventCreateInput, EventCreateOutput, EventItem, EventRegisterInput,
    EventRegisterOutput, EventSearchInput, EventSearchOutput, LostFoundMatch, LostFoundMatchesInput,
    LostFoundMatchesOutput, LostFoundPublishInput, LostFoundPublishOutput, ToolModel,
)
from app.modules.agent_platform.tool_gateway.errors import (
    ToolApprovalInvalid, ToolArgumentInvalid, ToolDependencyUnavailable, ToolForbidden,
)
from app.modules.community.encryption import (
    CommunityEncryptedDataInvalid, CommunityEncryptionUnavailable,
)
from app.modules.community.errors import LostFoundItemNotFound
from app.modules.community.events import EventQueryService, EventService
from app.modules.community.lost_found import LostFoundService
from app.modules.community.matcher import LostFoundMatcherService
from app.modules.community.posts import PostQueryService, PostService
from app.modules.community.registrations import EventRegistrationService
from app.modules.community.topics import TopicService


class EventSearchToolHandler:
    def __init__(self, service: EventQueryService) -> None:
        self._service = service

    async def __call__(self, invocation: ToolInvocationContext, payload: ToolModel) -> EventSearchOutput:
        try:
            data = EventSearchInput.model_validate(payload)
        except ValidationError as exc:
            raise ToolArgumentInvalid() from exc
        if data.campus_id is not None and data.campus_id.strip():
            raise ToolArgumentInvalid()
        page = await self._service.list(actor=invocation.user, page=data.page,
            page_size=data.page_size, starts_from=data.starts_after,
            available_only=True, q=data.query)
        return EventSearchOutput(items=tuple(EventItem(event_id=item.id, title=item.title,
            starts_at=item.starts_at, remaining_capacity=max(item.capacity - item.registered_count, 0))
            for item in page.items), page=page.page, page_size=page.page_size, total=page.total)


class EventRegisterToolHandler:
    def __init__(self, service: EventRegistrationService) -> None:
        self._service = service

    async def __call__(self, invocation: ToolInvocationContext, payload: ToolModel) -> EventRegisterOutput:
        if invocation.idempotency_key is None:
            raise ToolArgumentInvalid()
        if invocation.approval_id is None or not invocation.approval_verified:
            raise ToolApprovalInvalid()
        data = EventRegisterInput.model_validate(payload)
        await self._service.register(actor=invocation.user, event_id=data.event_id,
            idempotency_key=invocation.idempotency_key, request_id=invocation.user.request_id,
            manage_transaction=False)
        registration_id = uuid5(NAMESPACE_URL,
            f"campuspilot:event-registration:{data.event_id}:{invocation.user.user_id}")
        return EventRegisterOutput(registration_id=registration_id, status="registered")


def _campus_time(value):
    return value if value.tzinfo is not None else value.replace(tzinfo=ZoneInfo("Asia/Shanghai"))


class EventCreateToolHandler:
    def __init__(self, service: EventService) -> None:
        self._service = service

    async def __call__(self, invocation: ToolInvocationContext, payload: ToolModel) -> EventCreateOutput:
        if invocation.idempotency_key is None:
            raise ToolArgumentInvalid()
        if invocation.approval_id is None or not invocation.approval_verified:
            raise ToolApprovalInvalid()
        data = EventCreateInput.model_validate(payload)
        data = data.model_copy(update={
            "starts_at": _campus_time(data.starts_at),
            "ends_at": _campus_time(data.ends_at),
            "registration_deadline": _campus_time(data.registration_deadline),
        })
        result = await self._service.create(
            actor=invocation.user,
            idempotency_key=invocation.idempotency_key,
            request_id=invocation.user.request_id,
            request_body=data.model_dump(mode="json"),
            title=data.title,
            description_markdown=data.description,
            category=data.category,
            location=data.location,
            starts_at=data.starts_at,
            ends_at=data.ends_at,
            registration_deadline=data.registration_deadline,
            capacity=data.capacity,
            manage_transaction=False,
        )
        body = result.body["data"]
        return EventCreateOutput(event_id=body["id"], status=body["status"])


class CommunityPostPublishToolHandler:
    def __init__(self, posts: PostService, topics: TopicService) -> None:
        self._posts = posts
        self._topics = topics

    async def preflight(self, context: UserContext, payload: ToolModel) -> None:
        data = CommunityPostPublishInput.model_validate(payload)
        topics = await self._topics.list(page=1, page_size=100)
        if not any(item.code == data.topic for item in topics.items):
            raise ToolArgumentInvalid(
                "社区话题不存在",
                field="topic",
                reason="社区话题请选择 campus-life、mutual-help 或 tree-hole",
            )

    async def __call__(self, invocation: ToolInvocationContext, payload: ToolModel) -> CommunityPostPublishOutput:
        if invocation.idempotency_key is None:
            raise ToolArgumentInvalid("缺少幂等键", reason="系统未生成幂等键，请重新提交")
        if invocation.approval_id is None or not invocation.approval_verified:
            raise ToolApprovalInvalid()
        data = CommunityPostPublishInput.model_validate(payload)
        topics = await self._topics.list(page=1, page_size=100)
        topic = next((item for item in topics.items if item.code == data.topic), None)
        if topic is None:
            raise ToolArgumentInvalid("社区话题不存在", field="topic", reason="所选社区话题不存在或已停用")
        result = await self._posts.create(
            actor=invocation.user,
            topic_id=topic.id,
            title=data.title,
            content_markdown=data.content,
            is_anonymous=data.is_anonymous,
            idempotency_key=invocation.idempotency_key,
            request_id=invocation.user.request_id,
            request_body=data.model_dump(mode="json"),
            manage_transaction=False,
        )
        body = result.body["data"]
        return CommunityPostPublishOutput(post_id=body["id"], status=body["status"])


class CommunityTopicSummaryToolHandler:
    def __init__(self, posts: PostQueryService) -> None:
        self._posts = posts

    async def __call__(self, invocation: ToolInvocationContext, payload: ToolModel) -> CommunityTopicSummaryOutput:
        data = CommunityTopicSummaryInput.model_validate(payload)
        page = await self._posts.list(
            actor=invocation.user,
            page=1,
            page_size=data.limit,
            q=data.query,
            sort="-published_at",
        )
        items = tuple(
            CommunityTopicSummaryItem(
                post_id=item.id,
                topic=item.topic.name,
                title=item.title,
                excerpt=" ".join(item.content_markdown.split())[:300],
                like_count=item.like_count,
                comment_count=item.comment_count,
            )
            for item in page.items
        )
        if not items:
            summary = "当前社区没有找到匹配的已发布话题。"
        else:
            topic_counts: dict[str, int] = {}
            for item in items:
                topic_counts[item.topic] = topic_counts.get(item.topic, 0) + 1
            distribution = "、".join(f"{name}{count}条" for name, count in topic_counts.items())
            highlights = "、".join(item.title for item in items[:5])
            summary = f"当前共找到 {page.total} 条已发布社区内容；本次查看的内容分布为{distribution}。近期话题包括：{highlights}。"
        return CommunityTopicSummaryOutput(summary=summary, items=items, total=page.total)


class LostFoundPublishToolHandler:
    def __init__(self, service: LostFoundService) -> None:
        self._service = service

    async def __call__(self, invocation: ToolInvocationContext, payload: ToolModel) -> LostFoundPublishOutput:
        if invocation.idempotency_key is None:
            raise ToolArgumentInvalid()
        if invocation.approval_id is None or not invocation.approval_verified:
            raise ToolApprovalInvalid()
        data = LostFoundPublishInput.model_validate(payload)
        if len(data.description) < 5:
            raise ToolArgumentInvalid()
        occurred_at = data.occurred_at
        if occurred_at.tzinfo is None:
            # CampusPilot currently serves a single campus timezone. Natural
            # Chinese time such as “下午三点” is local time, not an invalid slot.
            occurred_at = occurred_at.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
            data = data.model_copy(update={"occurred_at": occurred_at})
        contact = f"站内联系：{invocation.user.username}"
        try:
            result = await self._service.create(actor=invocation.user,
                item_type=data.item_type, title=data.title, category=data.category,
                description=data.description, occurred_at=occurred_at,
                location=data.location, contact_type="other", contact_value=contact,
                idempotency_key=invocation.idempotency_key,
                request_id=invocation.user.request_id,
                request_body=data.model_dump(mode="json"), manage_transaction=False)
        except (CommunityEncryptionUnavailable, CommunityEncryptedDataInvalid) as exc:
            raise ToolDependencyUnavailable() from exc
        body = result.body["data"]
        return LostFoundPublishOutput(item_id=body["id"], status=body["status"])


class LostFoundMatchesToolHandler:
    def __init__(self, service: LostFoundMatcherService) -> None:
        self._service = service

    async def __call__(self, invocation: ToolInvocationContext, payload: ToolModel) -> LostFoundMatchesOutput:
        data = LostFoundMatchesInput.model_validate(payload)
        try:
            page = await self._service.list(actor=invocation.user, item_id=data.item_id,
                                            page=1, page_size=data.limit,
                                            manage_transaction=False)
        except LostFoundItemNotFound as exc:
            raise ToolForbidden() from exc
        except (CommunityEncryptionUnavailable, CommunityEncryptedDataInvalid) as exc:
            raise ToolDependencyUnavailable() from exc
        return LostFoundMatchesOutput(matches=tuple(LostFoundMatch(
            matched_item_id=item.candidate.id, score=float(item.score),
            reasons=tuple(str(reason["explanation"]) for reason in item.reasons),
            status=item.candidate.status) for item in page.items[:data.limit]))

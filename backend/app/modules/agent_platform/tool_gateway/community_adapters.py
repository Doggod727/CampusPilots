from uuid import NAMESPACE_URL, uuid5

from pydantic import ValidationError

from app.modules.agent_platform.domain.contracts import ToolInvocationContext
from app.modules.agent_platform.tool_gateway.catalog import (
    EventItem, EventRegisterInput, EventRegisterOutput, EventSearchInput,
    EventSearchOutput, LostFoundMatch, LostFoundMatchesInput,
    LostFoundMatchesOutput, LostFoundPublishInput, LostFoundPublishOutput, ToolModel,
)
from app.modules.agent_platform.tool_gateway.errors import (
    ToolApprovalInvalid, ToolArgumentInvalid, ToolDependencyUnavailable, ToolForbidden,
)
from app.modules.community.encryption import (
    CommunityEncryptedDataInvalid, CommunityEncryptionUnavailable,
)
from app.modules.community.errors import LostFoundItemNotFound
from app.modules.community.events import EventQueryService
from app.modules.community.lost_found import LostFoundService
from app.modules.community.matcher import LostFoundMatcherService
from app.modules.community.registrations import EventRegistrationService


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


class LostFoundPublishToolHandler:
    def __init__(self, service: LostFoundService) -> None:
        self._service = service

    async def __call__(self, invocation: ToolInvocationContext, payload: ToolModel) -> LostFoundPublishOutput:
        if invocation.idempotency_key is None:
            raise ToolArgumentInvalid()
        if invocation.approval_id is None or not invocation.approval_verified:
            raise ToolApprovalInvalid()
        data = LostFoundPublishInput.model_validate(payload)
        if len(data.description) < 5 or data.occurred_at.tzinfo is None:
            raise ToolArgumentInvalid()
        contact = f"站内联系：{invocation.user.username}"
        try:
            result = await self._service.create(actor=invocation.user,
                item_type=data.item_type, title=data.title, category=data.category,
                description=data.description, occurred_at=data.occurred_at,
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

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.modules.agent_platform.domain.contracts import ToolDefinition


class ToolModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class KnowledgeSearchInput(ToolModel):
    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=20)
    knowledge_base_ids: tuple[UUID, ...] = ()
    filters: dict[str, Any] = Field(default_factory=dict)


class KnowledgeSearchItem(ToolModel):
    chunk_id: UUID
    document_id: UUID
    title: str
    snippet: str = Field(max_length=1000)
    score: float = Field(ge=0, le=1)
    source_location: str | None = None
    page_number: int | None = Field(default=None, ge=1)


class KnowledgeSearchOutput(ToolModel):
    items: tuple[KnowledgeSearchItem, ...]
    retrieval_version: str
    fallback_reason: str | None = None


class KnowledgeAnswerInput(ToolModel):
    question: str = Field(min_length=1, max_length=2000)
    conversation_id: UUID | None = None
    knowledge_base_ids: tuple[UUID, ...] = ()


class Citation(ToolModel):
    chunk_id: UUID
    document_id: UUID
    title: str
    quote: str = Field(max_length=1000)


class KnowledgeAnswerOutput(ToolModel):
    answer: str
    citations: tuple[Citation, ...]
    message_id: UUID
    usage: dict[str, int]
    finish_reason: str


class ServiceGuideInput(ToolModel):
    query: str = Field(min_length=1, max_length=200)
    campus_id: str | None = Field(default=None, max_length=30)
    student_type: str | None = Field(default=None, max_length=50)


class GuideItem(ToolModel):
    guide_id: UUID
    title: str
    summary: str
    location: str | None = None
    updated_at: datetime
    steps: tuple[str, ...] = ()


class ServiceGuideOutput(ToolModel):
    items: tuple[GuideItem, ...] = Field(max_length=10)


class WorkOrderCreateInput(ToolModel):
    room_id: UUID
    fault_type: Literal["electric", "plumbing", "network", "furniture", "door_window", "other"]
    description: str = Field(min_length=10, max_length=1000)
    available_time: str | None = Field(default=None, max_length=200)
    attachments: tuple[str, ...] = Field(default=(), max_length=0)

    @field_validator("fault_type", mode="before")
    @classmethod
    def normalize_fault_type(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        normalized = value.strip().lower()
        aliases = {
            "electricity": "electric", "power": "electric", "电路": "electric", "电气": "electric",
            "water": "plumbing", "水暖": "plumbing", "水管": "plumbing", "漏水": "plumbing",
            "door": "door_window", "window": "door_window", "门": "door_window", "窗": "door_window",
            "网络": "network", "家具": "furniture", "其他": "other",
        }
        if normalized in aliases:
            return aliases[normalized]
        if any(word in normalized for word in ("水龙头", "漏水", "水管")):
            return "plumbing"
        return normalized

    @model_validator(mode="after")
    def validate_available_time(self) -> "WorkOrderCreateInput":
        if self.available_time is None:
            return self
        parts = self.available_time.split("/")
        if len(parts) != 2:
            raise ValueError("可上门时间须为‘开始时间/结束时间’，例如 2026-07-23T14:00:00+08:00/2026-07-23T16:00:00+08:00")
        try:
            start = datetime.fromisoformat(parts[0].replace("Z", "+00:00"))
            end = datetime.fromisoformat(parts[1].replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("可上门时间包含无效日期") from None
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("可上门时间必须包含时区")
        if end <= start:
            raise ValueError("可上门结束时间必须晚于开始时间")
        return self


class WorkOrderCreateOutput(ToolModel):
    work_order_id: UUID
    status: Literal["submitted"]
    created_at: datetime


class WorkOrderGetInput(ToolModel):
    work_order_id: UUID


class WorkOrderEvent(ToolModel):
    status: str
    occurred_at: datetime
    summary: str | None = None


class WorkOrderGetOutput(ToolModel):
    work_order_id: UUID
    status: str
    room_id: UUID
    fault_type: str
    description: str
    created_at: datetime
    events: tuple[WorkOrderEvent, ...] = ()


class ElectricityBalanceInput(ToolModel):
    room_id: UUID


class ElectricityBalanceOutput(ToolModel):
    room_id: UUID
    balance: Decimal = Field(decimal_places=2)
    currency: Literal["CNY"] = "CNY"
    updated_at: datetime
    source: Literal["mock"] = "mock"
    is_simulated: Literal[True] = True


class ElectricityTopupInput(ToolModel):
    room_id: UUID
    amount_cny: Decimal = Field(ge=Decimal("1.00"), le=Decimal("500.00"), decimal_places=2)


class ElectricityTopupOutput(ToolModel):
    topup_request_id: UUID
    status: Literal["simulated"] = "simulated"
    amount: Decimal = Field(decimal_places=2)
    notice: Literal["模拟申请，不产生真实扣款或到账"] = "模拟申请，不产生真实扣款或到账"


class EventSearchInput(ToolModel):
    query: str | None = Field(default=None, max_length=200)
    campus_id: str | None = Field(default=None, max_length=30)
    starts_after: datetime | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


class EventItem(ToolModel):
    event_id: UUID
    title: str
    starts_at: datetime
    remaining_capacity: int = Field(ge=0)


class EventSearchOutput(ToolModel):
    items: tuple[EventItem, ...]
    page: int = Field(ge=1)
    page_size: int = Field(ge=1)
    total: int = Field(ge=0)


class EventRegisterInput(ToolModel):
    event_id: UUID


class EventRegisterOutput(ToolModel):
    registration_id: UUID
    status: Literal["registered"] = "registered"


class EventCreateInput(ToolModel):
    title: str = Field(min_length=2, max_length=120)
    description: str = Field(min_length=1, max_length=5000)
    category: Literal["lecture", "club", "sports", "arts", "volunteer", "competition", "career", "other"]
    location: str = Field(min_length=2, max_length=200)
    starts_at: datetime
    ends_at: datetime
    registration_deadline: datetime
    capacity: int = Field(ge=1, le=10000)

    @model_validator(mode="after")
    def validate_schedule(self) -> "EventCreateInput":
        if self.ends_at <= self.starts_at:
            raise ValueError("结束时间必须晚于开始时间")
        if self.registration_deadline > self.starts_at:
            raise ValueError("报名截止时间不能晚于开始时间")
        return self


class EventCreateOutput(ToolModel):
    event_id: UUID
    status: str


class CommunityPostPublishInput(ToolModel):
    topic: Literal["campus-life", "mutual-help", "tree-hole"]
    title: str = Field(min_length=2, max_length=120)
    content: str = Field(min_length=1, max_length=5000)
    is_anonymous: bool = False


class CommunityPostPublishOutput(ToolModel):
    post_id: UUID
    status: str


class CommunityTopicSummaryInput(ToolModel):
    query: str | None = Field(default=None, max_length=200)
    limit: int = Field(default=10, ge=1, le=20)


class CommunityTopicSummaryItem(ToolModel):
    post_id: UUID
    topic: str
    title: str
    excerpt: str = Field(max_length=300)
    like_count: int = Field(ge=0)
    comment_count: int = Field(ge=0)


class CommunityTopicSummaryOutput(ToolModel):
    summary: str = Field(max_length=2000)
    items: tuple[CommunityTopicSummaryItem, ...]
    total: int = Field(ge=0)


class LostFoundPublishInput(ToolModel):
    item_type: Literal["lost", "found"]
    title: str = Field(min_length=1, max_length=100)
    category: str = Field(min_length=1, max_length=50)
    location: str = Field(min_length=1, max_length=200)
    occurred_at: datetime
    description: str = Field(min_length=5, max_length=2000)
    contact_preference: Literal["in_app"] = "in_app"


class LostFoundPublishOutput(ToolModel):
    item_id: UUID
    status: str


class LostFoundMatchesInput(ToolModel):
    item_id: UUID
    limit: int = Field(default=5, ge=1, le=20)


class LostFoundMatch(ToolModel):
    matched_item_id: UUID
    score: float = Field(ge=0, le=1)
    reasons: tuple[str, ...]
    status: str


class LostFoundMatchesOutput(ToolModel):
    matches: tuple[LostFoundMatch, ...]


class GovernanceCheckInput(ToolModel):
    text: str = Field(min_length=1, max_length=10000, repr=False)
    scope: Literal["tool_input", "tool_output", "agent_context"]


class GovernanceHit(ToolModel):
    rule: str
    action: str


class GovernanceCheckOutput(ToolModel):
    risk_level: Literal["low", "medium", "high", "critical"]
    action: Literal["allow", "mask", "review", "block"]
    hits: tuple[GovernanceHit, ...]
    sanitized_text: str = Field(repr=False)
    policy_version: str


class GovernanceAuthorizeInput(ToolModel):
    user_id: UUID
    agent_code: str
    tool_name: str
    resource: dict[str, Any] = Field(default_factory=dict)


class GovernanceAuthorizeOutput(ToolModel):
    allowed: bool
    reason_code: str | None = None


class GovernanceAuditInput(ToolModel):
    action: str
    request_id: str = Field(min_length=8, max_length=64)
    result: Literal["success", "failure", "denied"]
    metadata: dict[str, Any] = Field(default_factory=dict, repr=False)


class GovernanceAuditOutput(ToolModel):
    audit_id: UUID


@dataclass(frozen=True)
class ToolContract:
    definition: ToolDefinition
    input_model: type[ToolModel]
    output_model: type[ToolModel]


def _contract(
    *, name: str, module: str, description: str,
    input_model: type[ToolModel], output_model: type[ToolModel],
    permissions: tuple[str, ...], risk: str, timeout_ms: int,
    approval: bool = False, visibility: str = "agent",
) -> ToolContract:
    return ToolContract(
        definition=ToolDefinition.model_validate({
            "name": name,
            "version": "1.0.0",
            "module": module,
            "description": description,
            "input_schema": input_model.model_json_schema(),
            "output_schema": output_model.model_json_schema(),
            "required_permissions": permissions,
            "risk_level": risk,
            "timeout_ms": timeout_ms,
            "idempotent": True,
            "requires_approval": approval,
            "visibility": visibility,
            "enabled": True,
        }),
        input_model=input_model,
        output_model=output_model,
    )


TOOL_CONTRACTS: dict[str, ToolContract] = {
    contract.definition.name: contract
    for contract in (
        _contract(name="knowledge.search", module="m1", description="检索知识片段", input_model=KnowledgeSearchInput, output_model=KnowledgeSearchOutput, permissions=("knowledge:read",), risk="r0", timeout_ms=5000),
        _contract(name="knowledge.answer", module="m1", description="生成带引用回答", input_model=KnowledgeAnswerInput, output_model=KnowledgeAnswerOutput, permissions=("knowledge:read",), risk="r1", timeout_ms=60000),
        _contract(name="service.get_guide", module="m2", description="查询校园办事指南", input_model=ServiceGuideInput, output_model=ServiceGuideOutput, permissions=("service:read",), risk="r0", timeout_ms=3000),
        _contract(name="work_order.create", module="m2", description="创建宿舍报修工单", input_model=WorkOrderCreateInput, output_model=WorkOrderCreateOutput, permissions=("work_order:create",), risk="r2", timeout_ms=10000, approval=True),
        _contract(name="work_order.get", module="m2", description="查询本人可见工单", input_model=WorkOrderGetInput, output_model=WorkOrderGetOutput, permissions=("work_order:read",), risk="r1", timeout_ms=3000),
        _contract(name="electricity.get_balance", module="m2", description="查询本人房间模拟电费", input_model=ElectricityBalanceInput, output_model=ElectricityBalanceOutput, permissions=("electricity:read_own",), risk="r1", timeout_ms=5000),
        _contract(name="electricity.create_topup_request", module="m2", description="创建模拟电费充值申请", input_model=ElectricityTopupInput, output_model=ElectricityTopupOutput, permissions=("electricity:topup_request:create",), risk="r2", timeout_ms=10000, approval=True),
        _contract(name="event.search", module="m3", description="搜索可报名校园活动", input_model=EventSearchInput, output_model=EventSearchOutput, permissions=("community:read",), risk="r0", timeout_ms=3000),
        _contract(name="event.register", module="m3", description="报名校园活动", input_model=EventRegisterInput, output_model=EventRegisterOutput, permissions=("community:write",), risk="r2", timeout_ms=10000, approval=True),
        _contract(name="event.create", module="m3", description="创建校园活动并进入审核", input_model=EventCreateInput, output_model=EventCreateOutput, permissions=("community:write",), risk="r2", timeout_ms=10000, approval=True),
        _contract(name="community.post.publish", module="m3", description="在现有社区话题下发布帖子", input_model=CommunityPostPublishInput, output_model=CommunityPostPublishOutput, permissions=("community:write",), risk="r2", timeout_ms=10000, approval=True),
        _contract(name="community.topic.summarize", module="m3", description="查询并总结当前社区帖子", input_model=CommunityTopicSummaryInput, output_model=CommunityTopicSummaryOutput, permissions=("community:read",), risk="r0", timeout_ms=5000),
        _contract(name="lost_found.publish", module="m3", description="发布失物或拾物信息", input_model=LostFoundPublishInput, output_model=LostFoundPublishOutput, permissions=("community:write",), risk="r2", timeout_ms=10000, approval=True),
        _contract(name="lost_found.search_matches", module="m3", description="检索失物招领候选", input_model=LostFoundMatchesInput, output_model=LostFoundMatchesOutput, permissions=("community:read",), risk="r1", timeout_ms=5000),
        _contract(name="governance.check_content", module="m4", description="执行输入输出内容治理", input_model=GovernanceCheckInput, output_model=GovernanceCheckOutput, permissions=("moderation:execute",), risk="r1", timeout_ms=2000, visibility="runtime_internal"),
        _contract(name="governance.authorize_tool", module="m4", description="执行 Tool 授权判定", input_model=GovernanceAuthorizeInput, output_model=GovernanceAuthorizeOutput, permissions=("agent:run",), risk="r1", timeout_ms=1000, visibility="runtime_internal"),
        _contract(name="governance.write_audit", module="m4", description="写入结构化审计事件", input_model=GovernanceAuditInput, output_model=GovernanceAuditOutput, permissions=("audit:write",), risk="r2", timeout_ms=2000, visibility="runtime_internal"),
    )
}

assert len(TOOL_CONTRACTS) == 17

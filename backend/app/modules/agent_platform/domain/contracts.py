from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.shared.responses import ErrorDetail

AgentCode = Literal[
    "supervisor",
    "knowledge_agent",
    "service_agent",
    "community_agent",
    "governance_agent",
    "modelops_agent",
]
RouteTarget = Literal[
    "knowledge", "service", "community", "governance", "modelops", "clarify"
]
RiskLevel = Literal["r0", "r1", "r2", "r3"]
ToolVisibility = Literal["agent", "runtime_internal", "mcp"]


class FrozenContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _sorted_unique(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(sorted(set(values)))


class UserContext(FrozenContract):
    user_id: UUID
    username: str = Field(min_length=1, max_length=50)
    roles: tuple[str, ...] = ()
    permissions: tuple[str, ...] = ()
    request_id: str = Field(min_length=8, max_length=64)
    campus_id: str | None = Field(default=None, max_length=30)
    room_ids: tuple[UUID, ...] = ()

    @field_validator("roles", "permissions", mode="before")
    @classmethod
    def normalize_codes(cls, value: Any) -> tuple[str, ...]:
        return _sorted_unique(tuple(value or ()))

    @field_validator("room_ids", mode="before")
    @classmethod
    def normalize_room_ids(cls, value: Any) -> tuple[UUID, ...]:
        return tuple(sorted(set(value or ()), key=str))


class ArtifactRef(FrozenContract):
    artifact_type: str = Field(min_length=1, max_length=50)
    artifact_id: UUID | None = None
    label: str | None = Field(default=None, max_length=200)


class ResourceRef(FrozenContract):
    resource_type: str = Field(min_length=1, max_length=100)
    resource_id: UUID


class AgentTask(FrozenContract):
    task_id: UUID
    agent_run_id: UUID
    parent_task_id: UUID | None = None
    target_agent: AgentCode
    objective: str = Field(min_length=1, max_length=2000)
    structured_input: dict[str, Any] = Field(default_factory=dict, repr=False)
    depends_on: tuple[UUID, ...] = ()
    constraints: tuple[str, ...] = ()
    max_steps: int = Field(default=6, ge=1, le=6)

    @field_validator("depends_on", mode="before")
    @classmethod
    def normalize_dependencies(cls, value: Any) -> tuple[UUID, ...]:
        return tuple(sorted(set(value or ()), key=str))

    @field_validator("constraints", mode="before")
    @classmethod
    def normalize_constraints(cls, value: Any) -> tuple[str, ...]:
        return _sorted_unique(tuple(value or ()))


class AgentResult(FrozenContract):
    task_id: UUID
    agent_code: AgentCode
    status: Literal["succeeded", "partial", "failed", "needs_input"]
    summary: str = Field(min_length=1, max_length=2000)
    structured_output: dict[str, Any] = Field(default_factory=dict, repr=False)
    artifacts: tuple[ArtifactRef, ...] = ()
    error: ErrorDetail | None = None


class SupervisorPlan(FrozenContract):
    status: Literal["ready", "needs_input"]
    route: "RouteDecision"
    tasks: tuple[AgentTask, ...] = Field(default=(), max_length=3)
    reason_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,63}$")

    @model_validator(mode="after")
    def validate_plan(self) -> "SupervisorPlan":
        if self.status == "needs_input":
            if self.route.target_agent != "clarify" or self.tasks:
                raise ValueError("needs_input plans cannot contain executable tasks")
            return self
        if self.route.target_agent == "clarify" or not self.tasks:
            raise ValueError("ready plans require executable tasks")
        seen_ids: set[UUID] = set()
        seen_agents: set[str] = set()
        for task in self.tasks:
            if task.target_agent in seen_agents:
                raise ValueError("supervisor plan cannot repeat an agent")
            if task.parent_task_id is not None and task.parent_task_id not in seen_ids:
                raise ValueError("parent task must precede its child")
            if not set(task.depends_on) <= seen_ids:
                raise ValueError("task dependencies must precede the task")
            seen_agents.add(task.target_agent)
            seen_ids.add(task.task_id)
        return self


class RouteDecision(FrozenContract):
    target_agent: RouteTarget
    confidence: Decimal = Field(ge=0, le=1, max_digits=5, decimal_places=4)
    source: Literal["rule", "local_model", "deepseek"]
    reason_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,63}$")
    model_version_id: UUID | None = None
    candidate_agents: tuple[RouteTarget, ...] = Field(default=(), max_length=3)

    @field_validator("candidate_agents", mode="before")
    @classmethod
    def normalize_candidates(cls, value: Any) -> tuple[str, ...]:
        return _sorted_unique(tuple(value or ()))


class AgentDefinition(FrozenContract):
    code: AgentCode
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=500)
    enabled: bool = True


class AgentVersion(FrozenContract):
    agent_code: AgentCode
    version: str = Field(pattern=r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
    system_prompt: str = Field(min_length=1, max_length=20000, repr=False)
    output_schema: dict[str, Any] = Field(repr=False)
    tool_allowlist: tuple[str, ...] = ()
    status: Literal["draft", "active", "inactive"] = "draft"

    @field_validator("tool_allowlist", mode="before")
    @classmethod
    def normalize_tool_allowlist(cls, value: Any) -> tuple[str, ...]:
        values = _sorted_unique(tuple(value or ()))
        pattern = r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$"
        import re
        if any(re.fullmatch(pattern, item) is None for item in values):
            raise ValueError("tool_allowlist contains an invalid tool name")
        return values

    @model_validator(mode="after")
    def validate_output_schema(self) -> "AgentVersion":
        if self.output_schema.get("type") != "object":
            raise ValueError("output_schema must describe an object")
        return self


class AgentRegistration(FrozenContract):
    definition: AgentDefinition
    version: AgentVersion

    @model_validator(mode="after")
    def validate_matching_code(self) -> "AgentRegistration":
        if self.definition.code != self.version.agent_code:
            raise ValueError("agent definition and version codes must match")
        return self


class AgentCatalogItem(FrozenContract):
    code: AgentCode
    name: str
    description: str
    version: str
    enabled: bool
    tool_allowlist: tuple[str, ...]


class ToolDefinition(FrozenContract):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
    version: str = Field(pattern=r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
    module: Literal["m1", "m2", "m3", "m4", "m5"]
    description: str = Field(min_length=1, max_length=500)
    input_schema: dict[str, Any] = Field(repr=False)
    output_schema: dict[str, Any] = Field(repr=False)
    required_permissions: tuple[str, ...] = ()
    risk_level: RiskLevel
    timeout_ms: int = Field(ge=100, le=60000)
    idempotent: bool
    requires_approval: bool
    visibility: ToolVisibility
    enabled: bool = True

    @field_validator("required_permissions", mode="before")
    @classmethod
    def normalize_permissions(cls, value: Any) -> tuple[str, ...]:
        return _sorted_unique(tuple(value or ()))

    @model_validator(mode="after")
    def validate_definition(self) -> "ToolDefinition":
        if self.input_schema.get("type") != "object":
            raise ValueError("input_schema must describe an object")
        if self.output_schema.get("type") != "object":
            raise ValueError("output_schema must describe an object")
        if (
            self.risk_level in {"r2", "r3"}
            and self.visibility != "runtime_internal"
            and not self.requires_approval
        ):
            raise ValueError("external r2/r3 tools require approval")
        return self


class ToolCallRequest(FrozenContract):
    agent_run_id: UUID
    step_id: UUID
    tool_name: str = Field(pattern=r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
    tool_version: str = Field(pattern=r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
    arguments: dict[str, Any] = Field(default_factory=dict, repr=False)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128, repr=False)
    approval_id: UUID | None = Field(default=None, repr=False)


class ToolCallResult(FrozenContract):
    tool_call_id: UUID
    status: Literal["succeeded", "failed", "rejected", "expired"]
    data: dict[str, Any] | None = Field(default=None, repr=False)
    error: ErrorDetail | None = None
    duration_ms: int = Field(ge=0)
    resource_refs: tuple[ResourceRef, ...] = ()
    audit_id: UUID | None = None


class ApprovalRequest(FrozenContract):
    approval_id: UUID
    agent_run_id: UUID
    tool_call_id: UUID
    user_id: UUID
    action: str = Field(min_length=1, max_length=100)
    display_summary: str = Field(min_length=1, max_length=1000)
    arguments_hash: str = Field(pattern=r"^[0-9a-f]{64}$", repr=False)
    status: Literal["pending", "approved", "rejected", "expired", "consumed"]
    expires_at: datetime
    created_at: datetime
    decided_by: UUID | None = None
    decided_at: datetime | None = None

    @model_validator(mode="after")
    def validate_lifecycle(self) -> "ApprovalRequest":
        if self.expires_at <= self.created_at:
            raise ValueError("expires_at must be later than created_at")
        is_decided = self.status in {"approved", "rejected", "consumed"}
        if is_decided != (self.decided_by is not None and self.decided_at is not None):
            raise ValueError("decision actor and time must match approval status")
        return self

from collections.abc import Iterable, Mapping
from typing import Any
from uuid import UUID, uuid4

from pydantic import ValidationError

from app.modules.agent_platform.domain.contracts import (
    ToolDefinition,
    ToolInvocationContext,
    UserContext,
)
from app.modules.agent_platform.tool_gateway.catalog import (
    GovernanceAuditInput,
    GovernanceAuditOutput,
    GovernanceAuthorizeInput,
    GovernanceAuthorizeOutput,
    GovernanceCheckInput,
    GovernanceCheckOutput,
    GovernanceHit,
    ToolModel,
)
from app.modules.agent_platform.tool_gateway.errors import (
    ToolArgumentInvalid,
    ToolForbidden,
    ToolNotFound,
)
from app.modules.agent_platform.tool_gateway.registry import ToolRegistry
from app.modules.platform.audit import AuditService
from app.modules.platform.moderation import ModerationService


class M4ContentSafetyAdapter:
    """Apply M4 moderation to Tool strings without retaining raw payloads."""

    def __init__(self, moderation: ModerationService) -> None:
        self._moderation = moderation

    async def check_input(
        self, context: UserContext, definition: ToolDefinition, payload: ToolModel
    ) -> ToolModel:
        if definition.name == "governance.check_content":
            return payload
        return await self._sanitize_model(payload, "tool_input")

    async def check_output(
        self, context: UserContext, definition: ToolDefinition, payload: ToolModel
    ) -> ToolModel:
        if definition.name == "governance.check_content":
            return payload
        return await self._sanitize_model(payload, "tool_output")

    async def _sanitize_model(self, payload: ToolModel, scope: str) -> ToolModel:
        safe_data = await self._sanitize_value(payload.model_dump(mode="python"), scope)
        try:
            return type(payload).model_validate(safe_data)
        except ValidationError as exc:
            raise ToolArgumentInvalid() from exc

    async def _sanitize_value(self, value: Any, scope: str) -> Any:
        if isinstance(value, str):
            result = await self._moderation.scan(scope=scope, text=value)
            if result.action in {"review", "block"}:
                raise ToolForbidden()
            return result.sanitized_text if result.action == "mask" else value
        if isinstance(value, Mapping):
            return {
                key: await self._sanitize_value(item, scope)
                for key, item in value.items()
            }
        if isinstance(value, tuple):
            return tuple([await self._sanitize_value(item, scope) for item in value])
        if isinstance(value, list):
            return [await self._sanitize_value(item, scope) for item in value]
        return value


class M4ToolAuthorizationAdapter:
    """Deterministic M4 authorization; resource checks remain in domain services."""

    async def authorize(
        self,
        *,
        context: UserContext,
        definition: ToolDefinition,
        agent_allowlist: Iterable[str],
        trusted_runtime: bool,
    ) -> None:
        if definition.name not in set(agent_allowlist):
            raise ToolForbidden()
        if not set(definition.required_permissions) <= set(context.permissions):
            raise ToolForbidden()
        if definition.visibility == "runtime_internal" and not trusted_runtime:
            raise ToolForbidden()


class M4AuditAdapter:
    """Bridge Tool execution audit facts into the existing M4 AuditService."""

    def __init__(self, audit: AuditService) -> None:
        self._audit = audit

    async def record(
        self,
        *,
        context: UserContext,
        definition: ToolDefinition,
        result: str,
        duration_ms: int,
        error_code: str | None,
    ) -> UUID:
        safe_snapshot = {
            "tool_name": definition.name,
            "tool_version": definition.version,
            "result": result,
            "duration_ms": duration_ms,
        }
        if result == "success":
            entry = self._audit.record_success(
                action="agent.tool.execute",
                resource_type="agent_tool",
                resource_id=definition.name,
                request_id=context.request_id,
                actor_user_id=context.user_id,
                actor_username=context.username,
                after_data=safe_snapshot,
            )
        else:
            entry = self._audit.record_failure(
                action="agent.tool.execute",
                resource_type="agent_tool",
                resource_id=definition.name,
                request_id=context.request_id,
                actor_user_id=context.user_id,
                actor_username=context.username,
                after_data=safe_snapshot,
                error_code=error_code or "TOOL_EXECUTION_FAILED",
            )
        if entry.id is None:
            entry.id = uuid4()
        return entry.id


class GovernanceCheckContentHandler:
    def __init__(self, moderation: ModerationService) -> None:
        self._moderation = moderation

    async def __call__(
        self, invocation: ToolInvocationContext, payload: ToolModel
    ) -> GovernanceCheckOutput:
        context = invocation.user
        data = GovernanceCheckInput.model_validate(payload)
        result = await self._moderation.scan(scope=data.scope, text=data.text)
        return GovernanceCheckOutput(
            risk_level=result.risk_level,
            action=result.action,
            hits=tuple(
                GovernanceHit(rule=hit.rule, action=hit.action)
                for hit in result.hits
            ),
            sanitized_text=result.sanitized_text,
            policy_version=result.policy_version,
        )


class GovernanceAuthorizeToolHandler:
    def __init__(
        self,
        *,
        registry: ToolRegistry,
        authorization: M4ToolAuthorizationAdapter,
        agent_allowlists: Mapping[str, Iterable[str]],
    ) -> None:
        self._registry = registry
        self._authorization = authorization
        self._agent_allowlists = agent_allowlists

    async def __call__(
        self, invocation: ToolInvocationContext, payload: ToolModel
    ) -> GovernanceAuthorizeOutput:
        context = invocation.user
        data = GovernanceAuthorizeInput.model_validate(payload)
        if data.user_id != context.user_id:
            return GovernanceAuthorizeOutput(
                allowed=False, reason_code="USER_CONTEXT_MISMATCH"
            )
        try:
            contract = self._registry.resolve(data.tool_name)
            allowlist = self._agent_allowlists.get(data.agent_code, ())
            await self._authorization.authorize(
                context=context,
                definition=contract.definition,
                agent_allowlist=allowlist,
                trusted_runtime=True,
            )
        except (ToolForbidden, ToolNotFound):
            return GovernanceAuthorizeOutput(
                allowed=False, reason_code="TOOL_FORBIDDEN"
            )
        return GovernanceAuthorizeOutput(allowed=True, reason_code=None)


class GovernanceWriteAuditHandler:
    def __init__(self, audit: AuditService) -> None:
        self._audit = audit

    async def __call__(
        self, invocation: ToolInvocationContext, payload: ToolModel
    ) -> GovernanceAuditOutput:
        context = invocation.user
        data = GovernanceAuditInput.model_validate(payload)
        if data.result == "success":
            entry = self._audit.record_success(
                action=data.action,
                resource_type="agent_runtime",
                request_id=data.request_id,
                actor_user_id=context.user_id,
                actor_username=context.username,
                after_data={"result": data.result, "metadata": data.metadata},
            )
        else:
            entry = self._audit.record_failure(
                action=data.action,
                resource_type="agent_runtime",
                request_id=data.request_id,
                actor_user_id=context.user_id,
                actor_username=context.username,
                after_data={"result": data.result, "metadata": data.metadata},
                error_code="AGENT_OPERATION_DENIED"
                if data.result == "denied"
                else "AGENT_OPERATION_FAILED",
            )
        if entry.id is None:
            entry.id = uuid4()
        return GovernanceAuditOutput(audit_id=entry.id)

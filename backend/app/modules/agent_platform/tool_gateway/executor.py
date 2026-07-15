import asyncio
import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from time import perf_counter
from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import ValidationError

from app.core.errors import AppError
from app.modules.agent_platform.domain.contracts import (
    ToolCallRequest,
    ToolCallResult,
    ToolDefinition,
    UserContext,
)
from app.modules.agent_platform.tool_gateway.catalog import ToolContract, ToolModel
from app.modules.agent_platform.tool_gateway.errors import (
    ToolApprovalInvalid,
    ToolApprovalRequired,
    ToolArgumentInvalid,
    ToolDependencyUnavailable,
    ToolForbidden,
    ToolTimeout,
)
from app.modules.agent_platform.tool_gateway.mocks import (
    MockDependencyUnavailable,
    MockResourceForbidden,
    MockToolConflict,
    ToolHandler,
)
from app.modules.agent_platform.tool_gateway.registry import ToolRegistry


def canonical_arguments_hash(payload: ToolModel) -> str:
    serialized = json.dumps(
        payload.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode()).hexdigest()


@dataclass(frozen=True)
class PreparedToolCall:
    contract: ToolContract
    payload: ToolModel
    arguments_hash: str


class ContentSafetyPort(Protocol):
    async def check_input(
        self, context: UserContext, definition: ToolDefinition, payload: ToolModel
    ) -> None: ...

    async def check_output(
        self, context: UserContext, definition: ToolDefinition, payload: ToolModel
    ) -> None: ...


class ApprovalVerifierPort(Protocol):
    async def verify_and_consume(
        self,
        *,
        approval_id: UUID,
        user_id: UUID,
        tool_name: str,
        tool_version: str,
        arguments_hash: str,
    ) -> bool: ...


class AuditPort(Protocol):
    async def record(
        self,
        *,
        context: UserContext,
        definition: ToolDefinition,
        result: str,
        duration_ms: int,
        error_code: str | None,
    ) -> UUID | None: ...


class AllowContentSafety:
    async def check_input(
        self, context: UserContext, definition: ToolDefinition, payload: ToolModel
    ) -> None:
        return None

    async def check_output(
        self, context: UserContext, definition: ToolDefinition, payload: ToolModel
    ) -> None:
        return None


class InMemoryApprovalVerifier:
    def __init__(self) -> None:
        self._approvals: dict[UUID, tuple[UUID, str, str, str]] = {}

    def grant(
        self,
        *,
        approval_id: UUID,
        user_id: UUID,
        tool_name: str,
        tool_version: str,
        arguments_hash: str,
    ) -> None:
        self._approvals[approval_id] = (
            user_id, tool_name, tool_version, arguments_hash
        )

    async def verify_and_consume(
        self,
        *,
        approval_id: UUID,
        user_id: UUID,
        tool_name: str,
        tool_version: str,
        arguments_hash: str,
    ) -> bool:
        expected = self._approvals.get(approval_id)
        actual = (user_id, tool_name, tool_version, arguments_hash)
        if expected != actual:
            return False
        del self._approvals[approval_id]
        return True


@dataclass(frozen=True)
class AuditEvent:
    request_id: str
    tool_name: str
    result: str
    duration_ms: int
    error_code: str | None


class InMemoryAuditPort:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    async def record(
        self,
        *,
        context: UserContext,
        definition: ToolDefinition,
        result: str,
        duration_ms: int,
        error_code: str | None,
    ) -> UUID:
        self.events.append(AuditEvent(
            request_id=context.request_id,
            tool_name=definition.name,
            result=result,
            duration_ms=duration_ms,
            error_code=error_code,
        ))
        return uuid5(
            NAMESPACE_URL,
            f"campuspilot:audit:{context.request_id}:{definition.name}:{len(self.events)}",
        )


class ToolExecutor:
    def __init__(
        self,
        *,
        registry: ToolRegistry,
        handlers: Mapping[str, ToolHandler],
        content_safety: ContentSafetyPort,
        approval_verifier: ApprovalVerifierPort,
        audit: AuditPort,
    ) -> None:
        self._registry = registry
        self._handlers = dict(handlers)
        self._content_safety = content_safety
        self._approval_verifier = approval_verifier
        self._audit = audit

    def prepare(self, request: ToolCallRequest) -> PreparedToolCall:
        contract = self._registry.resolve(request.tool_name, request.tool_version)
        return self._validate_payload(contract, request)

    @staticmethod
    def _validate_payload(
        contract: ToolContract, request: ToolCallRequest
    ) -> PreparedToolCall:
        try:
            payload = contract.input_model.model_validate(request.arguments)
        except ValidationError as exc:
            raise ToolArgumentInvalid() from exc
        return PreparedToolCall(
            contract=contract,
            payload=payload,
            arguments_hash=canonical_arguments_hash(payload),
        )

    def authorize(
        self,
        context: UserContext,
        prepared: PreparedToolCall,
        agent_allowlist: Iterable[str],
        *,
        trusted_runtime: bool,
    ) -> None:
        definition = prepared.contract.definition
        if definition.name not in set(agent_allowlist):
            raise ToolForbidden()
        if not set(definition.required_permissions) <= set(context.permissions):
            raise ToolForbidden()
        if definition.visibility == "runtime_internal" and not trusted_runtime:
            raise ToolForbidden()

    async def execute(
        self,
        *,
        context: UserContext,
        request: ToolCallRequest,
        agent_allowlist: Iterable[str],
        trusted_runtime: bool = False,
    ) -> ToolCallResult:
        started = perf_counter()
        contract = self._registry.resolve(request.tool_name, request.tool_version)
        definition = contract.definition
        try:
            prepared = self._validate_payload(contract, request)
            self.authorize(
                context, prepared, agent_allowlist, trusted_runtime=trusted_runtime
            )
            if (
                definition.risk_level in {"r2", "r3"}
                and definition.visibility != "runtime_internal"
                and not request.idempotency_key
            ):
                raise ToolArgumentInvalid()
            if definition.requires_approval:
                if request.approval_id is None:
                    raise ToolApprovalRequired()
                approved = await self._approval_verifier.verify_and_consume(
                    approval_id=request.approval_id,
                    user_id=context.user_id,
                    tool_name=definition.name,
                    tool_version=definition.version,
                    arguments_hash=prepared.arguments_hash,
                )
                if not approved:
                    raise ToolApprovalInvalid()

            await self._content_safety.check_input(
                context, definition, prepared.payload
            )
            handler = self._handlers.get(definition.name)
            if handler is None:
                raise ToolDependencyUnavailable()
            try:
                raw_output = await asyncio.wait_for(
                    handler(context, prepared.payload),
                    timeout=definition.timeout_ms / 1000,
                )
            except TimeoutError as exc:
                raise ToolTimeout() from exc
            except MockResourceForbidden as exc:
                raise ToolForbidden() from exc
            except MockDependencyUnavailable as exc:
                raise ToolDependencyUnavailable() from exc
            except MockToolConflict as exc:
                raise AppError(
                    status_code=409,
                    code="CONFLICT",
                    message="工具调用与资源当前状态冲突",
                ) from exc
            except AppError:
                raise
            except Exception as exc:
                raise ToolDependencyUnavailable() from exc

            try:
                output = prepared.contract.output_model.model_validate(raw_output)
            except ValidationError as exc:
                raise ToolDependencyUnavailable() from exc
            await self._content_safety.check_output(context, definition, output)
            duration_ms = max(0, int((perf_counter() - started) * 1000))
            audit_id = await self._audit.record(
                context=context,
                definition=definition,
                result="success",
                duration_ms=duration_ms,
                error_code=None,
            )
            return ToolCallResult(
                tool_call_id=uuid5(
                    NAMESPACE_URL,
                    f"campuspilot:tool-call:{request.agent_run_id}:{request.step_id}:{definition.name}",
                ),
                status="succeeded",
                data=output.model_dump(mode="json"),
                duration_ms=duration_ms,
                audit_id=audit_id,
            )
        except AppError as exc:
            duration_ms = max(0, int((perf_counter() - started) * 1000))
            await self._audit.record(
                context=context,
                definition=definition,
                result="denied" if exc.status_code == 403 else "failure",
                duration_ms=duration_ms,
                error_code=exc.code,
            )
            raise

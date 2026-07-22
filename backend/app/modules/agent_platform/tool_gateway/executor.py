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
    ToolInvocationContext,
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

_JSON_TYPE_CHECKS = {
    "string": lambda value: isinstance(value, str),
    "number": lambda value: isinstance(value, (int, float)) and not isinstance(value, bool),
    "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
    "boolean": lambda value: isinstance(value, bool),
    "object": lambda value: isinstance(value, dict),
    "array": lambda value: isinstance(value, list),
}


def _schema_type_matches(fragment: Mapping[str, object], value: object) -> bool:
    if "anyOf" in fragment:
        return any(
            _schema_type_matches(variant, value)
            for variant in fragment.get("anyOf") or ()
            if isinstance(variant, Mapping)
        )
    type_check = _JSON_TYPE_CHECKS.get(fragment.get("type") or "")
    return type_check(value) if type_check is not None else False


def canonicalize_tool_arguments(
    arguments: Mapping[str, object], input_schema: Mapping[str, object]
) -> dict[str, object]:
    """Repair an unambiguous missing-required-key alias produced by an LLM.

    真实模型偶发参数名漂移（如 amount → amount_cny）。仅当恰好缺一个必填键、
    且恰好有一个类型匹配的未知键时才重命名；其余情况原样交给 Schema 校验，
    不做任何猜测性改写。
    """

    values = dict(arguments)
    properties = input_schema.get("properties") or {}
    required = [key for key in (input_schema.get("required") or ()) if key in properties]
    missing = [key for key in required if key not in values]
    unknown = [key for key in values if key not in properties]
    if len(missing) != 1 or not unknown:
        return values
    target = missing[0]
    candidates = [
        key
        for key in unknown
        if isinstance(properties.get(target), Mapping)
        and _schema_type_matches(properties[target], values[key])
    ]
    if len(candidates) != 1:
        return values
    values[target] = values.pop(candidates[0])
    return values


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
    ) -> ToolModel: ...

    async def check_output(
        self, context: UserContext, definition: ToolDefinition, payload: ToolModel
    ) -> ToolModel: ...


class ToolAuthorizationPort(Protocol):
    async def authorize(
        self,
        *,
        context: UserContext,
        definition: ToolDefinition,
        agent_allowlist: Iterable[str],
        trusted_runtime: bool,
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
    ) -> ToolModel:
        return payload

    async def check_output(
        self, context: UserContext, definition: ToolDefinition, payload: ToolModel
    ) -> ToolModel:
        return payload


class DeterministicToolAuthorization:
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
        authorization: ToolAuthorizationPort | None = None,
        approval_verifier: ApprovalVerifierPort,
        audit: AuditPort,
    ) -> None:
        self._registry = registry
        self._handlers = dict(handlers)
        self._content_safety = content_safety
        self._authorization = authorization or DeterministicToolAuthorization()
        self._approval_verifier = approval_verifier
        self._audit = audit

    def prepare(self, request: ToolCallRequest) -> PreparedToolCall:
        contract = self._registry.resolve(request.tool_name, request.tool_version)
        arguments = canonicalize_tool_arguments(
            request.arguments, contract.definition.input_schema
        )
        if arguments != dict(request.arguments):
            request = request.model_copy(update={"arguments": arguments})
        return self._validate_payload(contract, request)

    @staticmethod
    def _validate_payload(
        contract: ToolContract, request: ToolCallRequest
    ) -> PreparedToolCall:
        try:
            payload = contract.input_model.model_validate(request.arguments)
        except ValidationError as exc:
            raise ToolArgumentInvalid.from_validation_errors(exc.errors()) from exc
        return PreparedToolCall(
            contract=contract,
            payload=payload,
            arguments_hash=canonical_arguments_hash(payload),
        )

    async def authorize(
        self,
        context: UserContext,
        prepared: PreparedToolCall,
        agent_allowlist: Iterable[str],
        *,
        trusted_runtime: bool,
    ) -> None:
        await self._authorization.authorize(
            context=context,
            definition=prepared.contract.definition,
            agent_allowlist=agent_allowlist,
            trusted_runtime=trusted_runtime,
        )

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
            await self.authorize(
                context, prepared, agent_allowlist, trusted_runtime=trusted_runtime
            )
            if (
                definition.risk_level in {"r2", "r3"}
                and definition.visibility != "runtime_internal"
                and not request.idempotency_key
            ):
                raise ToolArgumentInvalid()
            safe_input = await self._content_safety.check_input(
                context, definition, prepared.payload
            )
            handler = self._handlers.get(definition.name)
            if handler is None:
                raise ToolDependencyUnavailable()
            preflight = getattr(handler, "preflight", None)
            if preflight is not None:
                await preflight(context, safe_input)

            # Parameter and read-only business validation must finish before an
            # approval is requested or consumed. Approval only guards mutation.
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

            invocation = ToolInvocationContext(
                user=context,
                agent_run_id=request.agent_run_id,
                step_id=request.step_id,
                idempotency_key=request.idempotency_key,
                arguments_hash=prepared.arguments_hash,
                approval_id=request.approval_id,
                approval_verified=definition.requires_approval,
            )
            try:
                raw_output = await asyncio.wait_for(
                    handler(invocation, safe_input),
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
            output = await self._content_safety.check_output(
                context, definition, output
            )
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

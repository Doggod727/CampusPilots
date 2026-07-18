from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from typing import Any, Callable
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import Settings
from app.core.errors import AppError
from app.modules.agent_platform.domain.contracts import SupervisorPlan, ToolCallRequest, UserContext
from app.modules.agent_platform.models import AgentRuntimeCheckpoint
from app.modules.agent_platform.orchestration.runtime import RuntimeCheckpoint
from app.modules.agent_platform.runtime_persistence import RuntimeCheckpointRepository, RuntimeEventRepository


class InvalidRuntimeCheckpoint(AppError):
    def __init__(self) -> None:
        super().__init__(status_code=409, code="AGENT_CHECKPOINT_INVALID", message="运行恢复状态无效")


class CheckpointCodec:
    """Authenticated encryption for short-lived runtime state."""

    def __init__(self, secret: str) -> None:
        if not secret:
            raise ValueError("checkpoint secret must not be empty")
        key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
        self._fernet = Fernet(key)

    def encode(self, state: RuntimeCheckpoint) -> tuple[str, str]:
        payload = {
            "user": state.user.model_dump(mode="json"),
            "objective": state.objective,
            "context": state.context,
            "plan": state.plan.model_dump(mode="json"),
            "next_task": state.next_task,
            "pending_step_id": str(state.pending_step_id) if state.pending_step_id else None,
            "pending_tool_call_id": str(state.pending_tool_call_id) if state.pending_tool_call_id else None,
            "pending_request": state.pending_request.model_dump(mode="json") if state.pending_request else None,
            "pending_agent_code": state.pending_agent_code,
            "terminal": state.terminal,
            "had_failures": state.had_failures,
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        return self._fernet.encrypt(raw).decode("ascii"), hashlib.sha256(raw).hexdigest()

    def decode(self, ciphertext: str, expected_sha256: str) -> RuntimeCheckpoint:
        try:
            raw = self._fernet.decrypt(ciphertext.encode("ascii"))
            if not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), expected_sha256):
                raise InvalidRuntimeCheckpoint()
            payload: dict[str, Any] = json.loads(raw)
            return RuntimeCheckpoint(
                user=UserContext.model_validate(payload["user"]),
                objective=payload["objective"],
                context=payload["context"],
                plan=SupervisorPlan.model_validate(payload["plan"]),
                next_task=payload["next_task"],
                pending_step_id=UUID(payload["pending_step_id"]) if payload["pending_step_id"] else None,
                pending_tool_call_id=UUID(payload["pending_tool_call_id"]) if payload["pending_tool_call_id"] else None,
                pending_request=ToolCallRequest.model_validate(payload["pending_request"]) if payload["pending_request"] else None,
                pending_agent_code=payload["pending_agent_code"],
                terminal=payload["terminal"],
                had_failures=payload["had_failures"],
            )
        except InvalidRuntimeCheckpoint:
            raise
        except (InvalidToken, ValueError, TypeError, KeyError, json.JSONDecodeError):
            raise InvalidRuntimeCheckpoint() from None


class DatabaseRuntimeCheckpointStore:
    def __init__(self, repository: RuntimeCheckpointRepository, codec: CheckpointCodec, *, ttl: timedelta,
                 clock: Callable[[], datetime] | None = None) -> None:
        self._repository = repository
        self._codec = codec
        self._ttl = ttl
        self._clock = clock or (lambda: datetime.now(UTC))

    @classmethod
    def from_settings(cls, repository: RuntimeCheckpointRepository, settings: Settings,
                      *, clock: Callable[[], datetime] | None = None) -> "DatabaseRuntimeCheckpointStore":
        if settings.agent_checkpoint_secret is None:
            raise ValueError("AGENT_CHECKPOINT_SECRET is required for the recoverable runtime")
        return cls(
            repository,
            CheckpointCodec(settings.agent_checkpoint_secret.get_secret_value()),
            ttl=timedelta(seconds=settings.agent_checkpoint_ttl_seconds),
            clock=clock,
        )

    async def load(self, run_id: UUID) -> RuntimeCheckpoint | None:
        row = await self._repository.get(run_id)
        if row is None:
            return None
        if row.expires_at <= self._clock():
            raise InvalidRuntimeCheckpoint()
        state = self._codec.decode(row.encrypted_state, row.state_sha256)
        state.checkpoint_version = row.state_version
        return state

    async def save(self, run_id: UUID, state: RuntimeCheckpoint) -> None:
        now = self._clock()
        ciphertext, digest = self._codec.encode(state)
        next_version = state.checkpoint_version + 1
        if state.checkpoint_version == 0:
            self._repository.add(AgentRuntimeCheckpoint(
                run_id=run_id, state_version=next_version,
                encrypted_state=ciphertext, state_sha256=digest,
                expires_at=now + self._ttl, updated_at=now,
            ))
        else:
            updated = await self._repository.update_if_version(
                run_id, state.checkpoint_version, state_version=next_version,
                encrypted_state=ciphertext, state_sha256=digest,
                expires_at=now + self._ttl, updated_at=now,
            )
            if not updated:
                raise InvalidRuntimeCheckpoint()
        state.checkpoint_version = next_version

    async def delete(self, run_id: UUID) -> None:
        await self._repository.delete(run_id)

class PersistentRuntimeEventSink:
    def __init__(self, repository: RuntimeEventRepository, *, request_id: str | None = None,
                 clock: Callable[[], datetime] | None = None) -> None:
        self._repository = repository
        self._request_id = request_id
        self._clock = clock or (lambda: datetime.now(UTC))

    async def publish(self, run_id: UUID, event: str, data: dict[str, Any]):
        return await self._repository.append(
            run_id=run_id, event=event, data=data,
            request_id=self._request_id, occurred_at=self._clock(),
        )


class RuntimeTerminalCoordinator:
    """Delete short-lived recovery state and append one terminal SSE fact."""

    def __init__(
        self,
        checkpoints: RuntimeCheckpointRepository,
        events: RuntimeEventRepository,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._checkpoints = checkpoints
        self._events = events
        self._clock = clock or (lambda: datetime.now(UTC))

    async def complete(
        self,
        *,
        run_id: UUID,
        status: str,
        request_id: str | None,
        error_code: str | None = None,
    ) -> None:
        await self._checkpoints.delete(run_id)
        data: dict[str, Any] = {"status": status}
        if error_code is not None:
            data["error_code"] = error_code
        await self._events.append(
            run_id=run_id,
            event="error" if status == "failed" else "done",
            data=data,
            request_id=request_id,
            occurred_at=self._clock(),
        )

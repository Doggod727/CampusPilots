from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import json
from pathlib import Path
import re
import sys
from time import monotonic
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import delete, func, select

from app.core.config import Settings, get_settings
from app.infrastructure.database import Database
from app.modules.agent_platform.approvals import ApprovalRepository, ApprovalService
from app.modules.agent_platform.checkpointing import (
    DatabaseRuntimeCheckpointStore,
    InvalidRuntimeCheckpoint,
)
from app.modules.agent_platform.composition import RuntimeCompositionFactory
from app.modules.agent_platform.domain.contracts import (
    AgentTask,
    RouteDecision,
    SupervisorPlan,
    ToolCallRequest,
)
from app.modules.agent_platform.internal_auth import InternalUserContextLoader
from app.modules.agent_platform.models import (
    AgentRun,
    AgentRunEvent,
    AgentRuntimeCheckpoint,
    AgentRuntimeCommand,
    AgentStep,
    ApprovalRequestModel,
    ToolCall,
)
from app.modules.agent_platform.orchestration.runtime import RuntimeCheckpoint
from app.modules.agent_platform.runtime_persistence import (
    RuntimeCheckpointRepository,
    RuntimeCommandRepository,
    RuntimeEventRepository,
)
from app.modules.agent_platform.runtime_worker import OutboxRuntimeDispatcher
from app.modules.agent_platform.tool_gateway.catalog import ElectricityTopupInput
from app.modules.agent_platform.tool_gateway.executor import canonical_arguments_hash
from app.modules.campus_service.models import ElectricityTopupRequest
from app.modules.campus_service.repositories import ElectricityRepository
from app.modules.platform.models import AuditLog
from app.modules.platform.repositories import RbacRepository, UserRepository


PROBE_INPUT = "runtime-checkpoint-recovery-verification"
TOOL_NAME = "electricity.create_topup_request"
TAG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{7,39}$")


class ProbeFailure(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _probe_uuid(tag: str, label: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"campuspilot:checkpoint-probe:{tag}:{label}")


def validate_tag(value: Any) -> str:
    if not isinstance(value, str) or TAG_PATTERN.fullmatch(value) is None:
        raise ProbeFailure("CHECKPOINT_PROBE_TAG_INVALID")
    return value


@dataclass(frozen=True)
class ProbeManifest:
    tag: str
    run_id: UUID
    step_id: UUID
    tool_call_id: UUID
    approval_id: UUID
    room_id: UUID
    request_id: str
    balance_before: Decimal
    created_at: datetime

    @property
    def idempotency_key(self) -> str:
        return f"rtcp-topup-{self.tag}"

    @property
    def client_request_id(self) -> str:
        return f"rtcp-{self.tag}"

    def to_json(self) -> dict[str, Any]:
        return {
            "tag": self.tag,
            "run_id": str(self.run_id),
            "step_id": str(self.step_id),
            "tool_call_id": str(self.tool_call_id),
            "approval_id": str(self.approval_id),
            "room_id": str(self.room_id),
            "request_id": self.request_id,
            "balance_before": str(self.balance_before),
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_json(cls, value: Any) -> "ProbeManifest":
        expected = {
            "tag", "run_id", "step_id", "tool_call_id", "approval_id",
            "room_id", "request_id", "balance_before", "created_at",
        }
        if not isinstance(value, dict) or set(value) != expected:
            raise ProbeFailure("CHECKPOINT_PROBE_MANIFEST_INVALID")
        try:
            manifest = cls(
                tag=validate_tag(value["tag"]),
                run_id=UUID(value["run_id"]),
                step_id=UUID(value["step_id"]),
                tool_call_id=UUID(value["tool_call_id"]),
                approval_id=UUID(value["approval_id"]),
                room_id=UUID(value["room_id"]),
                request_id=value["request_id"],
                balance_before=Decimal(value["balance_before"]),
                created_at=datetime.fromisoformat(value["created_at"]),
            )
        except (TypeError, ValueError, ArithmeticError) as exc:
            raise ProbeFailure("CHECKPOINT_PROBE_MANIFEST_INVALID") from exc
        if (
            manifest.created_at.tzinfo is None
            or manifest.run_id != _probe_uuid(manifest.tag, "run")
            or manifest.step_id != _probe_uuid(manifest.tag, "step")
            or manifest.tool_call_id != _probe_uuid(manifest.tag, "tool-call")
            or manifest.approval_id != _probe_uuid(manifest.tag, "approval")
            or manifest.request_id != f"rtcp-{manifest.tag}"
        ):
            raise ProbeFailure("CHECKPOINT_PROBE_MANIFEST_INVALID")
        return manifest


def _load_manifest(path: Path) -> ProbeManifest:
    try:
        return ProbeManifest.from_json(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProbeFailure("CHECKPOINT_PROBE_MANIFEST_INVALID") from exc


async def preflight(database: Database) -> dict[str, Any]:
    async with database.session() as session:
        active = (
            await session.execute(
                select(func.count()).select_from(AgentRuntimeCommand).where(
                    AgentRuntimeCommand.status.in_(("pending", "processing"))
                )
            )
        ).scalar_one()
    return {"ok": active == 0, "active_command_count": active}


async def seed(
    database: Database,
    settings: Settings,
    *,
    tag: str,
    manifest_path: Path,
) -> dict[str, Any]:
    validate_tag(tag)
    if manifest_path.exists():
        raise ProbeFailure("CHECKPOINT_PROBE_MANIFEST_EXISTS")
    now = datetime.now(UTC)
    request_id = f"rtcp-{tag}"
    run_id = _probe_uuid(tag, "run")
    step_id = _probe_uuid(tag, "step")
    tool_call_id = _probe_uuid(tag, "tool-call")
    approval_id = _probe_uuid(tag, "approval")

    async with database.session() as session:
        async with session.begin():
            active = (
                await session.execute(
                    select(func.count()).select_from(AgentRuntimeCommand).where(
                        AgentRuntimeCommand.status.in_(("pending", "processing"))
                    )
                )
            ).scalar_one()
            if active:
                raise ProbeFailure("CHECKPOINT_PROBE_ACTIVE_COMMANDS_PRESENT")
            existing = (
                await session.execute(select(AgentRun.id).where(AgentRun.id == run_id))
            ).scalar_one_or_none()
            if existing is not None:
                raise ProbeFailure("CHECKPOINT_PROBE_ALREADY_EXISTS")

            user = await UserRepository(session).get_by_username("student01")
            if user is None:
                raise ProbeFailure("CHECKPOINT_PROBE_USER_UNAVAILABLE")
            context = await InternalUserContextLoader(
                UserRepository(session), RbacRepository(session)
            ).load(user.id, request_id)
            room_ids = await ElectricityRepository(session).list_room_ids_for_user(user.id)
            if not room_ids or "electricity:topup_request:create" not in context.permissions:
                raise ProbeFailure("CHECKPOINT_PROBE_SCOPE_UNAVAILABLE")
            room_id = room_ids[0]
            account = await ElectricityRepository(session).get_account_for_user(room_id, user.id)
            if account is None:
                raise ProbeFailure("CHECKPOINT_PROBE_SCOPE_UNAVAILABLE")
            context = context.model_copy(update={"room_ids": room_ids})

            agents, _ = await RuntimeCompositionFactory(settings).load_catalogs(session)
            service_agent = agents.get_active("service_agent")
            if TOOL_NAME not in service_agent.version.tool_allowlist:
                raise ProbeFailure("CHECKPOINT_PROBE_CATALOG_UNAVAILABLE")

            arguments = {"room_id": str(room_id), "amount_cny": "1.00"}
            payload = ElectricityTopupInput.model_validate(arguments)
            arguments_hash = canonical_arguments_hash(payload)
            route = RouteDecision(
                target_agent="service",
                confidence=Decimal("1.0"),
                source="rule",
                reason_code="ROUTE_RULE_SINGLE",
            )
            task = AgentTask(
                task_id=_probe_uuid(tag, "task"),
                agent_run_id=run_id,
                target_agent="service_agent",
                objective=PROBE_INPUT,
                structured_input={},
            )
            plan = SupervisorPlan(
                status="ready",
                route=route,
                tasks=(task,),
                reason_code="SUPERVISOR_PLAN_READY",
            )
            idempotency_key = f"rtcp-topup-{tag}"
            request = ToolCallRequest(
                agent_run_id=run_id,
                step_id=step_id,
                tool_name=TOOL_NAME,
                tool_version="1.0.0",
                arguments=arguments,
                idempotency_key=idempotency_key,
            )

            run = AgentRun(
                id=run_id,
                user_id=user.id,
                client_request_id=request_id,
                input_summary=PROBE_INPUT,
                status="awaiting_approval",
                route_decision=route.model_dump(mode="json"),
                step_count=1,
                specialist_count=1,
                started_at=now,
                created_at=now,
                updated_at=now,
            )
            step = AgentStep(
                id=step_id,
                run_id=run_id,
                sequence_no=1,
                agent_code="service_agent",
                task_type="generate",
                status="awaiting_approval",
                input_summary={},
                output_summary={},
                started_at=now,
                created_at=now,
            )
            tool_call = ToolCall(
                id=tool_call_id,
                run_id=run_id,
                step_id=step_id,
                tool_name=TOOL_NAME,
                tool_version="1.0.0",
                arguments_hash=arguments_hash,
                arguments_summary={"amount_cny": "1.00", "room_id": str(room_id)},
                status="awaiting_approval",
                idempotency_key=idempotency_key,
                result_summary={},
                created_at=now,
            )
            session.add_all((run, step, tool_call))
            await session.flush()

            approval_service = ApprovalService(
                ApprovalRepository(session), ttl_seconds=max(300, settings.approval_ttl_seconds)
            )
            approval = await approval_service.create(
                run_id=run_id,
                tool_call_id=tool_call_id,
                user_id=user.id,
                action=TOOL_NAME,
                display_summary="Runtime checkpoint recovery probe",
                arguments_hash=arguments_hash,
            )
            approval.id = approval_id
            await session.flush()
            await approval_service.decide(
                approval_id=approval_id,
                run_id=run_id,
                user_id=user.id,
                decision="approve",
                arguments_hash=arguments_hash,
            )

            checkpoint = RuntimeCheckpoint(
                user=context,
                objective=PROBE_INPUT,
                context={},
                plan=plan,
                pending_step_id=step_id,
                pending_tool_call_id=tool_call_id,
                pending_request=request,
                pending_agent_code="service_agent",
            )
            await DatabaseRuntimeCheckpointStore.from_settings(
                RuntimeCheckpointRepository(session), settings
            ).save(run_id, checkpoint)
            events = RuntimeEventRepository(session)
            await events.append(
                run_id=run_id,
                event="route",
                data=route.model_dump(mode="json"),
                request_id=request_id,
                occurred_at=now,
            )
            await events.append(
                run_id=run_id,
                event="approval_required",
                data={"approval_id": str(approval_id), "tool_name": TOOL_NAME},
                request_id=request_id,
                occurred_at=now,
            )
            await OutboxRuntimeDispatcher(
                RuntimeCommandRepository(session), max_attempts=2, now=lambda: now
            ).resume(run_id, approval_id)

            manifest = ProbeManifest(
                tag=tag,
                run_id=run_id,
                step_id=step_id,
                tool_call_id=tool_call_id,
                approval_id=approval_id,
                room_id=room_id,
                request_id=request_id,
                balance_before=account.balance,
                created_at=now,
            )
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            with manifest_path.open("x", encoding="utf-8") as handle:
                json.dump(manifest.to_json(), handle, ensure_ascii=False, indent=2)
    return {"ok": True, "tag": tag, "checkpoint_version": 1}


async def cas_probe(
    database: Database,
    settings: Settings,
    manifest: ProbeManifest,
) -> dict[str, Any]:
    async with database.session() as first, database.session() as stale:
        await first.begin()
        await stale.begin()
        first_store = DatabaseRuntimeCheckpointStore.from_settings(
            RuntimeCheckpointRepository(first), settings
        )
        stale_store = DatabaseRuntimeCheckpointStore.from_settings(
            RuntimeCheckpointRepository(stale), settings
        )
        first_state = await first_store.load(manifest.run_id)
        stale_state = await stale_store.load(manifest.run_id)
        if first_state is None or stale_state is None:
            raise ProbeFailure("CHECKPOINT_PROBE_STATE_MISSING")
        initial_version = first_state.checkpoint_version
        await first_store.save(manifest.run_id, first_state)
        await first.commit()
        stale_rejected = False
        try:
            await stale_store.save(manifest.run_id, stale_state)
        except InvalidRuntimeCheckpoint:
            stale_rejected = True
        finally:
            await stale.rollback()
    async with database.session() as session:
        row = await RuntimeCheckpointRepository(session).get(manifest.run_id)
    ok = row is not None and row.state_version == initial_version + 1 and stale_rejected
    return {
        "ok": ok,
        "initial_version": initial_version,
        "committed_version": row.state_version if row is not None else None,
        "stale_writer_rejected": stale_rejected,
    }


async def hold_run_lock(
    database: Database,
    manifest: ProbeManifest,
    *,
    ready_file: Path,
    release_file: Path,
    timeout_seconds: int,
) -> dict[str, Any]:
    if ready_file.exists() or release_file.exists():
        raise ProbeFailure("CHECKPOINT_PROBE_BARRIER_INVALID")
    async with database.session() as session:
        async with session.begin():
            locked = (
                await session.execute(
                    select(AgentRun.id)
                    .where(AgentRun.id == manifest.run_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if locked is None:
                raise ProbeFailure("CHECKPOINT_PROBE_RUN_MISSING")
            ready_file.parent.mkdir(parents=True, exist_ok=True)
            ready_file.write_text("ready", encoding="ascii")
            deadline = monotonic() + timeout_seconds
            while not release_file.exists():
                if monotonic() >= deadline:
                    raise ProbeFailure("CHECKPOINT_PROBE_BARRIER_TIMEOUT")
                await asyncio.sleep(0.1)
    return {"ok": True, "released": True}


async def snapshot(
    database: Database,
    settings: Settings,
    manifest: ProbeManifest,
) -> dict[str, Any]:
    async with database.session() as session:
        run = (
            await session.execute(select(AgentRun).where(AgentRun.id == manifest.run_id))
        ).scalar_one_or_none()
        command = (
            await session.execute(
                select(AgentRuntimeCommand).where(
                    AgentRuntimeCommand.run_id == manifest.run_id,
                    AgentRuntimeCommand.action == "resume",
                )
            )
        ).scalar_one_or_none()
        checkpoint = await RuntimeCheckpointRepository(session).get(manifest.run_id)
        approval = (
            await session.execute(
                select(ApprovalRequestModel).where(ApprovalRequestModel.id == manifest.approval_id)
            )
        ).scalar_one_or_none()
        step = (
            await session.execute(select(AgentStep).where(AgentStep.id == manifest.step_id))
        ).scalar_one_or_none()
        tool_call = (
            await session.execute(select(ToolCall).where(ToolCall.id == manifest.tool_call_id))
        ).scalar_one_or_none()
        topup_count = (
            await session.execute(
                select(func.count()).select_from(ElectricityTopupRequest).where(
                    ElectricityTopupRequest.agent_run_id == manifest.run_id,
                    ElectricityTopupRequest.approval_id == manifest.approval_id,
                    ElectricityTopupRequest.idempotency_key == manifest.idempotency_key,
                )
            )
        ).scalar_one()
        audit_count = (
            await session.execute(
                select(func.count()).select_from(AuditLog).where(
                    AuditLog.request_id == manifest.request_id,
                    AuditLog.action == "agent.tool.execute",
                )
            )
        ).scalar_one()
        event_count = (
            await session.execute(
                select(func.count()).select_from(AgentRunEvent).where(
                    AgentRunEvent.run_id == manifest.run_id
                )
            )
        ).scalar_one()
    now = datetime.now(UTC)
    lease_expires_at = None
    lease_expired = False
    if command is not None and command.claimed_at is not None:
        lease_expires_at = command.claimed_at + timedelta(
            seconds=settings.agent_runtime_claim_timeout_seconds
        )
        lease_expired = now > lease_expires_at
    return {
        "ok": run is not None and command is not None,
        "run_status": run.status if run is not None else None,
        "command_status": command.status if command is not None else None,
        "command_attempt_count": command.attempt_count if command is not None else None,
        "command_claimed_by": command.claimed_by if command is not None else None,
        "checkpoint_version": checkpoint.state_version if checkpoint is not None else None,
        "approval_status": approval.status if approval is not None else None,
        "step_status": step.status if step is not None else None,
        "tool_call_status": tool_call.status if tool_call is not None else None,
        "topup_count": topup_count,
        "audit_count": audit_count,
        "event_count": event_count,
        "lease_expires_at": lease_expires_at.isoformat() if lease_expires_at else None,
        "lease_expired": lease_expired,
    }


async def verify_final(
    database: Database,
    manifest: ProbeManifest,
    *,
    recovery_worker: str,
) -> dict[str, Any]:
    problems: list[str] = []
    async with database.session() as session:
        run = (
            await session.execute(select(AgentRun).where(AgentRun.id == manifest.run_id))
        ).scalar_one_or_none()
        commands = tuple(
            (
                await session.execute(
                    select(AgentRuntimeCommand).where(
                        AgentRuntimeCommand.run_id == manifest.run_id
                    )
                )
            )
            .scalars()
            .all()
        )
        steps = tuple(
            (await session.execute(select(AgentStep).where(AgentStep.run_id == manifest.run_id)))
            .scalars()
            .all()
        )
        tools = tuple(
            (await session.execute(select(ToolCall).where(ToolCall.run_id == manifest.run_id)))
            .scalars()
            .all()
        )
        events = tuple(
            (
                await session.execute(
                    select(AgentRunEvent)
                    .where(AgentRunEvent.run_id == manifest.run_id)
                    .order_by(AgentRunEvent.sequence)
                )
            )
            .scalars()
            .all()
        )
        checkpoint = await RuntimeCheckpointRepository(session).get(manifest.run_id)
        approval = (
            await session.execute(
                select(ApprovalRequestModel).where(ApprovalRequestModel.id == manifest.approval_id)
            )
        ).scalar_one_or_none()
        topups = tuple(
            (
                await session.execute(
                    select(ElectricityTopupRequest).where(
                        ElectricityTopupRequest.agent_run_id == manifest.run_id,
                        ElectricityTopupRequest.approval_id == manifest.approval_id,
                        ElectricityTopupRequest.idempotency_key == manifest.idempotency_key,
                    )
                )
            )
            .scalars()
            .all()
        )
        audit_count = (
            await session.execute(
                select(func.count()).select_from(AuditLog).where(
                    AuditLog.request_id == manifest.request_id,
                    AuditLog.action == "agent.tool.execute",
                    AuditLog.result == "success",
                )
            )
        ).scalar_one()
        account = await ElectricityRepository(session).get_account_for_user(
            manifest.room_id, run.user_id if run is not None else UUID(int=0)
        )

    if run is None or run.status != "succeeded" or run.finish_reason != "completed":
        problems.append("RUN_NOT_SUCCEEDED")
    if len(commands) != 1:
        problems.append("COMMAND_CARDINALITY_INVALID")
    else:
        command = commands[0]
        if (
            command.action != "resume"
            or command.status != "succeeded"
            or command.attempt_count != 2
            or command.claimed_by != recovery_worker
            or command.error_code is not None
        ):
            problems.append("COMMAND_RECOVERY_INVALID")
    if len(steps) != 1 or steps[0].status != "succeeded":
        problems.append("STEP_CARDINALITY_INVALID")
    if len(tools) != 1 or tools[0].status != "succeeded":
        problems.append("TOOL_CALL_CARDINALITY_INVALID")
    if approval is None or approval.status != "consumed":
        problems.append("APPROVAL_NOT_CONSUMED")
    if len(topups) != 1:
        problems.append("TOPUP_SIDE_EFFECT_INVALID")
    if audit_count != 1:
        problems.append("TOOL_AUDIT_INVALID")
    if checkpoint is not None:
        problems.append("TERMINAL_CHECKPOINT_RETAINED")
    if [item.sequence for item in events] != list(range(1, len(events) + 1)):
        problems.append("EVENT_SEQUENCE_INVALID")
    if [item.event for item in events] != ["route", "approval_required", "done"]:
        problems.append("EVENT_TERMINAL_INVALID")
    if account is None or account.balance != manifest.balance_before:
        problems.append("ELECTRICITY_BALANCE_CHANGED")

    return {
        "ok": not problems,
        "run_status": run.status if run is not None else None,
        "command_count": len(commands),
        "command_attempt_count": commands[0].attempt_count if len(commands) == 1 else None,
        "recovery_worker": commands[0].claimed_by if len(commands) == 1 else None,
        "step_count": len(steps),
        "tool_call_count": len(tools),
        "event_count": len(events),
        "topup_count": len(topups),
        "audit_count": audit_count,
        "checkpoint_count": 0 if checkpoint is None else 1,
        "balance_unchanged": account is not None and account.balance == manifest.balance_before,
        "problems": problems,
    }


async def cleanup(database: Database, *, tag: str) -> dict[str, Any]:
    tag = validate_tag(tag)
    run_id = _probe_uuid(tag, "run")
    request_id = f"rtcp-{tag}"
    async with database.session() as session:
        async with session.begin():
            run = (
                await session.execute(select(AgentRun).where(AgentRun.id == run_id).with_for_update())
            ).scalar_one_or_none()
            if run is not None and (
                run.input_summary != PROBE_INPUT or run.client_request_id != request_id
            ):
                raise ProbeFailure("CHECKPOINT_PROBE_CLEANUP_SCOPE_INVALID")
            topups = await session.execute(
                delete(ElectricityTopupRequest).where(
                    ElectricityTopupRequest.agent_run_id == run_id,
                    ElectricityTopupRequest.idempotency_key == f"rtcp-topup-{tag}",
                )
            )
            audits = await session.execute(
                delete(AuditLog).where(
                    AuditLog.request_id == request_id,
                    AuditLog.action == "agent.tool.execute",
                )
            )
            runs = await session.execute(
                delete(AgentRun).where(
                    AgentRun.id == run_id,
                    AgentRun.input_summary == PROBE_INPUT,
                    AgentRun.client_request_id == request_id,
                )
            )
    return {
        "ok": True,
        "deleted_run_count": runs.rowcount,
        "deleted_topup_count": topups.rowcount,
        "deleted_audit_count": audits.rowcount,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Safe M5 checkpoint recovery probe")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("preflight")
    seed_parser = commands.add_parser("seed")
    seed_parser.add_argument("--tag", required=True)
    seed_parser.add_argument("--manifest", type=Path, required=True)
    for name in ("cas", "snapshot"):
        child = commands.add_parser(name)
        child.add_argument("--manifest", type=Path, required=True)
    hold = commands.add_parser("hold-lock")
    hold.add_argument("--manifest", type=Path, required=True)
    hold.add_argument("--ready-file", type=Path, required=True)
    hold.add_argument("--release-file", type=Path, required=True)
    hold.add_argument("--timeout-seconds", type=int, default=60)
    verify = commands.add_parser("verify")
    verify.add_argument("--manifest", type=Path, required=True)
    verify.add_argument("--recovery-worker", required=True)
    cleanup_parser = commands.add_parser("cleanup")
    cleanup_parser.add_argument("--tag", required=True)
    return parser


async def _run(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    settings = get_settings()
    database = Database.from_settings(settings)
    try:
        if args.command == "preflight":
            result = await preflight(database)
        elif args.command == "seed":
            result = await seed(
                database, settings, tag=args.tag, manifest_path=args.manifest
            )
        elif args.command == "cleanup":
            result = await cleanup(database, tag=args.tag)
        else:
            manifest = _load_manifest(args.manifest)
            if args.command == "cas":
                result = await cas_probe(database, settings, manifest)
            elif args.command == "snapshot":
                result = await snapshot(database, settings, manifest)
            elif args.command == "hold-lock":
                result = await hold_run_lock(
                    database,
                    manifest,
                    ready_file=args.ready_file,
                    release_file=args.release_file,
                    timeout_seconds=args.timeout_seconds,
                )
            else:
                result = await verify_final(
                    database, manifest, recovery_worker=args.recovery_worker
                )
        return result, 0 if result.get("ok") else 1
    finally:
        await database.dispose()


def main() -> int:
    try:
        result, exit_code = asyncio.run(_run(_parser().parse_args()))
    except ProbeFailure as exc:
        result, exit_code = {"ok": False, "error_code": exc.code}, 2
    except Exception as exc:  # Never print connection strings, ciphertext, or tool arguments.
        result, exit_code = {
            "ok": False,
            "error_code": "CHECKPOINT_PROBE_FAILED",
            "error_type": type(exc).__name__,
        }, 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())

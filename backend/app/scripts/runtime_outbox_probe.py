from __future__ import annotations

import argparse
import asyncio
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
import re
import sys
from typing import Any, Sequence
from uuid import UUID, NAMESPACE_URL, uuid5

from sqlalchemy import delete, func, select

from app.core.config import get_settings
from app.infrastructure.database import Database
from app.modules.agent_platform.models import (
    AgentRun,
    AgentRunEvent,
    AgentRuntimeCheckpoint,
    AgentRuntimeCommand,
    AgentStep,
    ToolCall,
)
from app.modules.agent_platform.runtime_persistence import RuntimeCommandRepository
from app.modules.agent_platform.runtime_worker import OutboxRuntimeDispatcher
from app.modules.agent_platform.traces import TraceRepository, TraceService


PROBE_INPUT = "runtime-outbox-concurrency-verification"
TAG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{7,39}$")
TERMINAL_COMMAND_STATUSES = {"succeeded", "failed"}


class ProbeFailure(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ProbeManifest:
    tag: str
    run_ids: tuple[UUID, ...]
    created_at: datetime

    @property
    def request_prefix(self) -> str:
        return f"rtcon-{self.tag}-"

    def to_json(self) -> dict[str, Any]:
        return {
            "tag": self.tag,
            "run_ids": [str(item) for item in self.run_ids],
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_json(cls, value: Any) -> "ProbeManifest":
        if not isinstance(value, dict) or set(value) != {"tag", "run_ids", "created_at"}:
            raise ProbeFailure("RUNTIME_PROBE_MANIFEST_INVALID")
        tag = validate_tag(value["tag"])
        raw_ids = value["run_ids"]
        if not isinstance(raw_ids, list) or not raw_ids:
            raise ProbeFailure("RUNTIME_PROBE_MANIFEST_INVALID")
        try:
            run_ids = tuple(UUID(item) for item in raw_ids)
            created_at = datetime.fromisoformat(value["created_at"])
        except (TypeError, ValueError) as exc:
            raise ProbeFailure("RUNTIME_PROBE_MANIFEST_INVALID") from exc
        if len(run_ids) != len(set(run_ids)) or created_at.tzinfo is None:
            raise ProbeFailure("RUNTIME_PROBE_MANIFEST_INVALID")
        return cls(tag=tag, run_ids=run_ids, created_at=created_at)


@dataclass(frozen=True)
class ProbeSnapshot:
    runs: tuple[AgentRun, ...]
    commands: tuple[AgentRuntimeCommand, ...]
    events: tuple[AgentRunEvent, ...]
    steps: tuple[AgentStep, ...]
    tool_calls: tuple[ToolCall, ...]
    checkpoints: tuple[AgentRuntimeCheckpoint, ...]


def validate_tag(value: Any) -> str:
    if not isinstance(value, str) or TAG_PATTERN.fullmatch(value) is None:
        raise ProbeFailure("RUNTIME_PROBE_TAG_INVALID")
    return value


def validate_snapshot(
    manifest: ProbeManifest,
    snapshot: ProbeSnapshot,
    expected_workers: Sequence[str],
) -> tuple[dict[str, Any], tuple[str, ...]]:
    workers = tuple(expected_workers)
    if len(workers) != 2 or len(set(workers)) != 2 or any(not item or len(item) > 100 for item in workers):
        raise ProbeFailure("RUNTIME_PROBE_WORKERS_INVALID")

    expected_ids = set(manifest.run_ids)
    problems: list[str] = []
    runs_by_id = {item.id: item for item in snapshot.runs}
    commands_by_run: dict[UUID, list[AgentRuntimeCommand]] = defaultdict(list)
    events_by_run: dict[UUID, list[AgentRunEvent]] = defaultdict(list)
    for item in snapshot.commands:
        commands_by_run[item.run_id].append(item)
    for item in snapshot.events:
        events_by_run[item.run_id].append(item)

    if set(runs_by_id) != expected_ids:
        problems.append("RUN_SET_MISMATCH")
    for run_id in expected_ids:
        run = runs_by_id.get(run_id)
        if run is None:
            continue
        expected_request_id = f"{manifest.request_prefix}{manifest.run_ids.index(run_id) + 1:03d}"
        if (
            run.client_request_id != expected_request_id
            or run.input_summary != PROBE_INPUT
            or run.status != "cancelled"
            or run.finish_reason != "user_cancelled"
            or run.finished_at is None
            or run.step_count != 0
        ):
            problems.append("RUN_TERMINAL_STATE_INVALID")

        commands = commands_by_run.get(run_id, [])
        if len(commands) != 1:
            problems.append("COMMAND_CARDINALITY_INVALID")
        else:
            command = commands[0]
            if (
                command.action != "cancel"
                or command.status != "succeeded"
                or command.attempt_count != 1
                or command.claimed_by not in workers
                or command.claimed_at is None
                or command.completed_at is None
                or command.completed_at < command.claimed_at
                or command.error_code is not None
            ):
                problems.append("COMMAND_TERMINAL_STATE_INVALID")

        events = sorted(events_by_run.get(run_id, []), key=lambda item: item.sequence)
        if (
            len(events) != 1
            or events[0].sequence != 1
            or events[0].event != "done"
            or events[0].data != {"status": "cancelled"}
        ):
            problems.append("EVENT_CARDINALITY_INVALID")

    worker_counts = Counter(
        item.claimed_by for item in snapshot.commands if item.claimed_by is not None
    )
    if set(worker_counts) != set(workers) or any(worker_counts[item] == 0 for item in workers):
        problems.append("WORKER_DISTRIBUTION_INVALID")

    intervals = [
        item
        for item in snapshot.commands
        if item.claimed_at is not None and item.completed_at is not None
    ]
    workers_overlapped = any(
        left.claimed_by != right.claimed_by
        and left.claimed_at <= right.completed_at
        and right.claimed_at <= left.completed_at
        for index, left in enumerate(intervals)
        for right in intervals[index + 1 :]
    )
    if not workers_overlapped:
        problems.append("WORKER_INTERVALS_DID_NOT_OVERLAP")

    if snapshot.steps:
        problems.append("UNEXPECTED_STEPS")
    if snapshot.tool_calls:
        problems.append("UNEXPECTED_TOOL_CALLS")
    if snapshot.checkpoints:
        problems.append("UNEXPECTED_CHECKPOINTS")

    summary = {
        "tag": manifest.tag,
        "run_count": len(snapshot.runs),
        "command_count": len(snapshot.commands),
        "event_count": len(snapshot.events),
        "step_count": len(snapshot.steps),
        "tool_call_count": len(snapshot.tool_calls),
        "checkpoint_count": len(snapshot.checkpoints),
        "worker_claims": {item: worker_counts[item] for item in workers},
        "workers_overlapped": workers_overlapped,
        "active_command_count": sum(
            item.status in {"pending", "processing"} for item in snapshot.commands
        ),
        "failed_command_count": sum(item.status == "failed" for item in snapshot.commands),
    }
    return summary, tuple(dict.fromkeys(problems))


async def _load_snapshot(session: Any, run_ids: tuple[UUID, ...]) -> ProbeSnapshot:
    async def load(model: Any) -> tuple[Any, ...]:
        statement = select(model).where(model.run_id.in_(run_ids))
        return tuple((await session.execute(statement)).scalars().all())

    runs = tuple(
        (await session.execute(select(AgentRun).where(AgentRun.id.in_(run_ids)))).scalars().all()
    )
    return ProbeSnapshot(
        runs=runs,
        commands=await load(AgentRuntimeCommand),
        events=await load(AgentRunEvent),
        steps=await load(AgentStep),
        tool_calls=await load(ToolCall),
        checkpoints=await load(AgentRuntimeCheckpoint),
    )


def _load_manifest(path: Path) -> ProbeManifest:
    try:
        return ProbeManifest.from_json(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProbeFailure("RUNTIME_PROBE_MANIFEST_INVALID") from exc


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


async def seed(database: Database, *, tag: str, count: int, manifest_path: Path) -> dict[str, Any]:
    validate_tag(tag)
    if count < 20 or count > 200:
        raise ProbeFailure("RUNTIME_PROBE_COUNT_INVALID")
    if manifest_path.exists():
        raise ProbeFailure("RUNTIME_PROBE_MANIFEST_EXISTS")

    now = datetime.now(UTC)
    run_ids: list[UUID] = []
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
                raise ProbeFailure("RUNTIME_PROBE_ACTIVE_COMMANDS_PRESENT")
            trace = TraceService(TraceRepository(session), now=lambda: now)
            dispatcher = OutboxRuntimeDispatcher(
                RuntimeCommandRepository(session), now=lambda: now
            )
            user_id = uuid5(NAMESPACE_URL, f"campuspilot-runtime-probe:{tag}")
            for index in range(1, count + 1):
                run = trace.create_run(
                    user_id=user_id,
                    client_request_id=f"rtcon-{tag}-{index:03d}",
                    input_summary=PROBE_INPUT,
                )
                await dispatcher.cancel(run.id)
                run_ids.append(run.id)

    manifest = ProbeManifest(tag=tag, run_ids=tuple(run_ids), created_at=now)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with manifest_path.open("x", encoding="utf-8") as handle:
        json.dump(manifest.to_json(), handle, ensure_ascii=False, indent=2)
    return {"ok": True, "tag": tag, "run_count": count}


async def status(database: Database, manifest: ProbeManifest) -> dict[str, Any]:
    async with database.session() as session:
        snapshot = await _load_snapshot(session, manifest.run_ids)
    return {
        "ok": True,
        "expected_command_count": len(manifest.run_ids),
        "terminal_command_count": sum(
            item.status in TERMINAL_COMMAND_STATUSES for item in snapshot.commands
        ),
        "failed_command_count": sum(item.status == "failed" for item in snapshot.commands),
    }


async def verify(
    database: Database,
    manifest: ProbeManifest,
    expected_workers: Sequence[str],
) -> dict[str, Any]:
    async with database.session() as session:
        snapshot = await _load_snapshot(session, manifest.run_ids)
    summary, problems = validate_snapshot(manifest, snapshot, expected_workers)
    return {"ok": not problems, **summary, "problems": list(problems)}


async def cleanup(database: Database, manifest: ProbeManifest) -> dict[str, Any]:
    async with database.session() as session:
        async with session.begin():
            runs = tuple(
                (
                    await session.execute(
                        select(AgentRun).where(AgentRun.id.in_(manifest.run_ids)).with_for_update()
                    )
                )
                .scalars()
                .all()
            )
            if any(
                item.input_summary != PROBE_INPUT
                or not item.client_request_id.startswith(manifest.request_prefix)
                for item in runs
            ):
                raise ProbeFailure("RUNTIME_PROBE_CLEANUP_SCOPE_INVALID")
            result = await session.execute(
                delete(AgentRun).where(
                    AgentRun.id.in_(manifest.run_ids),
                    AgentRun.input_summary == PROBE_INPUT,
                    AgentRun.client_request_id.startswith(manifest.request_prefix),
                )
            )
            deleted = result.rowcount
    return {"ok": deleted == len(runs), "deleted_run_count": deleted}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Safe M5 runtime outbox concurrency probe")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("preflight")
    seed_parser = subparsers.add_parser("seed")
    seed_parser.add_argument("--tag", required=True)
    seed_parser.add_argument("--count", type=int, default=40)
    seed_parser.add_argument("--manifest", type=Path, required=True)
    for name in ("status", "cleanup"):
        command_parser = subparsers.add_parser(name)
        command_parser.add_argument("--manifest", type=Path, required=True)
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--manifest", type=Path, required=True)
    verify_parser.add_argument("--worker", action="append", required=True)
    return parser


async def _run(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    settings = get_settings()
    database = Database.from_settings(settings)
    try:
        if args.command == "preflight":
            result = await preflight(database)
        elif args.command == "seed":
            result = await seed(
                database,
                tag=args.tag,
                count=args.count,
                manifest_path=args.manifest,
            )
        else:
            manifest = _load_manifest(args.manifest)
            if args.command == "status":
                result = await status(database, manifest)
            elif args.command == "verify":
                result = await verify(database, manifest, args.worker)
            else:
                result = await cleanup(database, manifest)
        return result, 0 if result.get("ok") else 1
    finally:
        await database.dispose()


def main() -> int:
    try:
        result, exit_code = asyncio.run(_run(_parser().parse_args()))
    except ProbeFailure as exc:
        result, exit_code = {"ok": False, "error_code": exc.code}, 2
    except Exception as exc:  # The verifier must never print connection strings or secrets.
        result, exit_code = {
            "ok": False,
            "error_code": "RUNTIME_PROBE_FAILED",
            "error_type": type(exc).__name__,
        }, 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())

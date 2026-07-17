import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from sqlalchemy.dialects import postgresql

from app.modules.agent_platform.models import AgentRuntimeCommand
from app.modules.agent_platform.runtime_persistence import RuntimeCheckpointRepository, RuntimeCommandRepository, RuntimeEventRepository

NOW=datetime(2026,7,15,tzinfo=UTC); RUN=uuid4(); COMMAND=uuid4()

def result(*, scalars=(), scalar=None, rowcount=1):
    value=MagicMock(); value.scalars.return_value.all.return_value=list(scalars); value.scalar_one.return_value=scalar; value.scalar_one_or_none.return_value=scalar; value.rowcount=rowcount; return value

def test_claim_uses_skip_locked_and_updates_only_claimed_entities():
    command=AgentRuntimeCommand(id=COMMAND,run_id=RUN,action="start",payload={},status="pending",attempt_count=0,max_attempts=3,available_at=NOW,created_at=NOW,updated_at=NOW)
    session=MagicMock(); statements=[]
    async def execute(stmt): statements.append(stmt); return result(scalars=(command,))
    session.execute=AsyncMock(side_effect=execute); claimed=asyncio.run(RuntimeCommandRepository(session).claim_batch(worker_id="worker-1",now=NOW,stale_after=timedelta(minutes=1)))
    sql=str(statements[0].compile(dialect=postgresql.dialect(),compile_kwargs={"literal_binds":True}))
    assert claimed==(command,) and command.status=="processing" and command.attempt_count==1
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "attempt_count < agent_platform.agent_runtime_commands.max_attempts" in sql
    assert "ORDER BY agent_platform.agent_runtime_commands.available_at" in sql
    assert "LIMIT 10" in sql

def test_command_complete_and_retry_require_the_claim_owner_and_do_not_manage_session():
    command=AgentRuntimeCommand(id=COMMAND,status="processing",attempt_count=1,max_attempts=3)
    session=MagicMock(); statements=[]
    async def execute(stmt):
        statements.append(stmt)
        return result(rowcount=1) if len(statements)==1 else result(scalar=command)
    session.execute=AsyncMock(side_effect=execute)
    repo=RuntimeCommandRepository(session); assert asyncio.run(repo.complete(COMMAND,"worker-1",NOW))
    assert asyncio.run(repo.fail_or_retry(COMMAND,worker_id="worker-1",now=NOW,retry_at=NOW+timedelta(seconds=5),error_code="SAFE"))=="pending"
    complete_sql=str(statements[0].compile(dialect=postgresql.dialect(),compile_kwargs={"literal_binds":True}))
    retry_sql=str(statements[1].compile(dialect=postgresql.dialect(),compile_kwargs={"literal_binds":True}))
    assert "claimed_by = 'worker-1'" in complete_sql
    assert "claimed_by = 'worker-1'" in retry_sql and "FOR UPDATE" in retry_sql
    for name in ("commit","rollback","flush","close"): assert not getattr(session,name).called

def test_checkpoint_cas_compiles_version_condition():
    session=MagicMock(); statements=[]
    async def execute(stmt): statements.append(stmt); return result(rowcount=1)
    session.execute=AsyncMock(side_effect=execute); assert asyncio.run(RuntimeCheckpointRepository(session).update_if_version(RUN,2,state_version=3,encrypted_state="cipher",state_sha256="a"*64,expires_at=NOW+timedelta(hours=1),updated_at=NOW))
    sql=str(statements[0].compile(dialect=postgresql.dialect(),compile_kwargs={"literal_binds":True}))
    assert "state_version = 2" in sql and "state_version=3" in sql.replace(" ","")

def test_event_append_allocates_next_sequence_and_redacts_data():
    session=MagicMock(); statements=[]
    async def execute(stmt): statements.append(stmt); return result() if len(statements)==1 else result(scalar=4)
    session.execute=AsyncMock(side_effect=execute); repository=RuntimeEventRepository(session)
    item=asyncio.run(repository.append(run_id=RUN,event="tool_call",data={"token":"raw","safe":"ok"},request_id="runtime-request",occurred_at=NOW))
    lock_sql=str(statements[0].compile(dialect=postgresql.dialect(),compile_kwargs={"literal_binds":True}))
    sequence_sql=str(statements[1].compile(dialect=postgresql.dialect(),compile_kwargs={"literal_binds":True}))
    assert "agent_platform.agent_runs.id" in lock_sql and "FOR UPDATE" in lock_sql
    assert "max(agent_platform.agent_run_events.sequence)" in sequence_sql
    assert item.sequence==5 and item.data=={"token":"***","safe":"ok"}; session.add.assert_called_once_with(item)

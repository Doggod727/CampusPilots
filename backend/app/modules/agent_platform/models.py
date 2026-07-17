from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean, CHAR, CheckConstraint, DateTime, Double, ForeignKey, Index, Integer,
    SmallInteger, String, Text, UniqueConstraint, text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base

SCHEMA = "agent_platform"


def _uuid_pk():
    return mapped_column(PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))


def _timestamp():
    return mapped_column(DateTime(timezone=True), server_default=text("now()"))


class AgentDefinition(Base):
    __tablename__ = "agent_definitions"
    __table_args__ = (CheckConstraint("code ~ '^[a-z][a-z0-9_]{2,49}$'", name="ck_agent_code"), {"schema": SCHEMA})
    id: Mapped[UUID] = _uuid_pk(); code: Mapped[str] = mapped_column(String(50), unique=True)
    name: Mapped[str] = mapped_column(String(100)); description: Mapped[str] = mapped_column(String(500))
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    created_at: Mapped[datetime] = _timestamp(); updated_at: Mapped[datetime] = _timestamp()


class AgentVersion(Base):
    __tablename__ = "agent_versions"
    __table_args__ = (
        UniqueConstraint("agent_id", "version"),
        CheckConstraint("status IN ('draft', 'active', 'inactive')", name="ck_agent_version_status"),
        CheckConstraint("jsonb_typeof(output_schema) = 'object'", name="ck_agent_output_schema"),
        CheckConstraint("jsonb_typeof(tool_allowlist) = 'array'", name="ck_agent_tool_allowlist"),
        Index("uq_agent_one_active_version", "agent_id", unique=True, postgresql_where=text("status = 'active'")),
        {"schema": SCHEMA},
    )
    id: Mapped[UUID] = _uuid_pk(); agent_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey(f"{SCHEMA}.agent_definitions.id", ondelete="CASCADE"))
    version: Mapped[str] = mapped_column(String(30)); system_prompt: Mapped[str] = mapped_column(Text)
    output_schema: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb")); tool_allowlist: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    status: Mapped[str] = mapped_column(String(16), server_default=text("'draft'")); created_by: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True)); created_at: Mapped[datetime] = _timestamp()


class ToolDefinition(Base):
    __tablename__ = "tool_definitions"
    __table_args__ = (
        CheckConstraint("name ~ '^[a-z][a-z0-9_]*(\\.[a-z][a-z0-9_]*)+$'", name="ck_tool_name"),
        CheckConstraint("module IN ('m1', 'm2', 'm3', 'm4', 'm5')", name="ck_tool_module"),
        CheckConstraint("risk_level IN ('r0', 'r1', 'r2', 'r3')", name="ck_tool_risk"),
        CheckConstraint("visibility IN ('agent', 'runtime_internal', 'mcp')", name="ck_tool_visibility"), {"schema": SCHEMA},
    )
    id: Mapped[UUID] = _uuid_pk(); name: Mapped[str] = mapped_column(String(100), unique=True); module: Mapped[str] = mapped_column(String(10))
    description: Mapped[str] = mapped_column(String(500)); risk_level: Mapped[str] = mapped_column(String(2)); visibility: Mapped[str] = mapped_column(String(20))
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("true")); created_at: Mapped[datetime] = _timestamp(); updated_at: Mapped[datetime] = _timestamp()


class ToolVersion(Base):
    __tablename__ = "tool_versions"
    __table_args__ = (
        UniqueConstraint("tool_id", "version"), CheckConstraint("jsonb_typeof(input_schema) = 'object'", name="ck_tool_version_input"),
        CheckConstraint("jsonb_typeof(output_schema) = 'object'", name="ck_tool_version_output"), CheckConstraint("jsonb_typeof(required_permissions) = 'array'", name="ck_tool_version_permissions"),
        CheckConstraint("timeout_ms BETWEEN 100 AND 60000", name="ck_tool_version_timeout"), CheckConstraint("status IN ('draft', 'active', 'inactive')", name="ck_tool_version_status"),
        Index("uq_tool_one_active_version", "tool_id", unique=True, postgresql_where=text("status = 'active'")), {"schema": SCHEMA},
    )
    id: Mapped[UUID] = _uuid_pk(); tool_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey(f"{SCHEMA}.tool_definitions.id", ondelete="CASCADE")); version: Mapped[str] = mapped_column(String(30))
    input_schema: Mapped[dict] = mapped_column(JSONB); output_schema: Mapped[dict] = mapped_column(JSONB); required_permissions: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    timeout_ms: Mapped[int] = mapped_column(Integer, server_default=text("10000")); idempotent: Mapped[bool] = mapped_column(Boolean); requires_approval: Mapped[bool] = mapped_column(Boolean)
    implementation_ref: Mapped[str] = mapped_column(String(200)); status: Mapped[str] = mapped_column(String(16), server_default=text("'draft'")); created_at: Mapped[datetime] = _timestamp()


class Dataset(Base):
    __tablename__ = "datasets"
    __table_args__ = (CheckConstraint("purpose IN ('agent_router', 'instruction_tuning', 'rag_reranker', 'evaluation')", name="ck_dataset_purpose"), Index("uq_dataset_active_name", text("lower(name)"), unique=True, postgresql_where=text("deleted_at IS NULL")), {"schema": SCHEMA})
    id: Mapped[UUID] = _uuid_pk(); name: Mapped[str] = mapped_column(String(100)); purpose: Mapped[str] = mapped_column(String(30)); description: Mapped[str | None] = mapped_column(String(500))
    owner_user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True)); deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True)); created_at: Mapped[datetime] = _timestamp(); updated_at: Mapped[datetime] = _timestamp()


class DatasetVersion(Base):
    __tablename__ = "dataset_versions"
    __table_args__ = (UniqueConstraint("dataset_id", "version"), CheckConstraint("format IN ('jsonl', 'csv')", name="ck_dataset_version_format"), CheckConstraint("sample_count >= 0", name="ck_dataset_version_count"), CheckConstraint("jsonb_typeof(split_config) = 'object'", name="ck_dataset_version_split"), CheckConstraint("jsonb_typeof(validation_report) = 'object'", name="ck_dataset_version_report"), CheckConstraint("validation_status IN ('pending', 'valid', 'invalid')", name="ck_dataset_validation_status"), {"schema": SCHEMA})
    id: Mapped[UUID] = _uuid_pk(); dataset_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey(f"{SCHEMA}.datasets.id", ondelete="CASCADE")); version: Mapped[int] = mapped_column(Integer)
    artifact_key: Mapped[str] = mapped_column(String(500)); artifact_sha256: Mapped[str] = mapped_column(CHAR(64)); format: Mapped[str] = mapped_column(String(16)); sample_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    split_config: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb")); validation_status: Mapped[str] = mapped_column(String(16), server_default=text("'pending'")); validation_report: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb")); contains_sensitive_data: Mapped[bool] = mapped_column(Boolean, server_default=text("false")); frozen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True)); created_by: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True)); created_at: Mapped[datetime] = _timestamp()


class TrainingJob(Base):
    __tablename__ = "training_jobs"
    __table_args__ = (CheckConstraint("method IN ('lora', 'qlora')", name="ck_training_method"), CheckConstraint("jsonb_typeof(config) = 'object'", name="ck_training_config"), CheckConstraint("jsonb_typeof(resource_limits) = 'object'", name="ck_training_resources"), CheckConstraint("jsonb_typeof(metrics) = 'object'", name="ck_training_metrics"), CheckConstraint("status IN ('queued', 'preparing', 'training', 'evaluating', 'succeeded', 'failed', 'cancelled')", name="ck_training_status"), CheckConstraint("progress BETWEEN 0 AND 100", name="ck_training_progress"), Index("ix_training_jobs_queue", "status", "created_at"), {"schema": SCHEMA})
    id: Mapped[UUID] = _uuid_pk(); dataset_version_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey(f"{SCHEMA}.dataset_versions.id", ondelete="RESTRICT")); base_model: Mapped[str] = mapped_column(String(200)); method: Mapped[str] = mapped_column(String(16)); config: Mapped[dict] = mapped_column(JSONB); resource_limits: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb")); status: Mapped[str] = mapped_column(String(16), server_default=text("'queued'")); progress: Mapped[int] = mapped_column(SmallInteger, server_default=text("0")); artifact_key: Mapped[str | None] = mapped_column(String(500)); artifact_sha256: Mapped[str | None] = mapped_column(CHAR(64)); metrics: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb")); error_code: Mapped[str | None] = mapped_column(String(100)); error_message: Mapped[str | None] = mapped_column(String(500)); created_by: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True)); started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True)); finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True)); created_at: Mapped[datetime] = _timestamp(); updated_at: Mapped[datetime] = _timestamp()


class ModelVersion(Base):
    __tablename__ = "model_versions"
    __table_args__ = (UniqueConstraint("name", "version"), CheckConstraint("purpose IN ('complex_generation', 'agent_router', 'rag_reranker', 'embedding')", name="ck_model_purpose"), CheckConstraint("provider IN ('deepseek', 'local', 'rule')", name="ck_model_provider"), CheckConstraint("jsonb_typeof(config) = 'object'", name="ck_model_config"), CheckConstraint("jsonb_typeof(metrics) = 'object'", name="ck_model_metrics"), CheckConstraint("status IN ('candidate', 'active', 'inactive', 'failed')", name="ck_model_status"), Index("uq_model_one_active_purpose", "purpose", unique=True, postgresql_where=text("status = 'active'")), {"schema": SCHEMA})
    id: Mapped[UUID] = _uuid_pk(); name: Mapped[str] = mapped_column(String(100)); purpose: Mapped[str] = mapped_column(String(30)); provider: Mapped[str] = mapped_column(String(20)); base_model: Mapped[str] = mapped_column(String(200)); version: Mapped[str] = mapped_column(String(50)); quantization: Mapped[str | None] = mapped_column(String(30)); artifact_key: Mapped[str | None] = mapped_column(String(500)); artifact_sha256: Mapped[str | None] = mapped_column(CHAR(64)); config: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb")); metrics: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb")); status: Mapped[str] = mapped_column(String(16), server_default=text("'candidate'")); training_job_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey(f"{SCHEMA}.training_jobs.id", ondelete="SET NULL")); created_by: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True)); created_at: Mapped[datetime] = _timestamp(); activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EvaluationJob(Base):
    __tablename__ = "evaluation_jobs"
    __table_args__ = (CheckConstraint("target_type IN ('agent', 'tool', 'model', 'rag', 'system')", name="ck_evaluation_target"), CheckConstraint("status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')", name="ck_evaluation_status"), CheckConstraint("jsonb_typeof(config) = 'object'", name="ck_evaluation_config"), CheckConstraint("jsonb_typeof(summary) = 'object'", name="ck_evaluation_summary"), {"schema": SCHEMA})
    id: Mapped[UUID] = _uuid_pk(); target_type: Mapped[str] = mapped_column(String(20)); target_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True)); dataset_version_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey(f"{SCHEMA}.dataset_versions.id", ondelete="RESTRICT")); status: Mapped[str] = mapped_column(String(16), server_default=text("'queued'")); config: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb")); summary: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb")); report_key: Mapped[str | None] = mapped_column(String(500)); error_code: Mapped[str | None] = mapped_column(String(100)); created_by: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True)); started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True)); finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True)); created_at: Mapped[datetime] = _timestamp()


class EvaluationMetric(Base):
    __tablename__ = "evaluation_metrics"; __table_args__ = (UniqueConstraint("evaluation_id", "name", "slice_name"), {"schema": SCHEMA})
    id: Mapped[UUID] = _uuid_pk(); evaluation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey(f"{SCHEMA}.evaluation_jobs.id", ondelete="CASCADE")); name: Mapped[str] = mapped_column(String(100)); value: Mapped[float] = mapped_column(Double); unit: Mapped[str | None] = mapped_column(String(30)); slice_name: Mapped[str] = mapped_column(String(100), server_default=text("'all'")); created_at: Mapped[datetime] = _timestamp()


class AgentRun(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (UniqueConstraint("user_id", "client_request_id"), CheckConstraint("status IN ('created', 'routing', 'running', 'awaiting_approval', 'succeeded', 'partial', 'failed', 'cancelled')", name="ck_agent_run_status"), CheckConstraint("route_decision IS NULL OR jsonb_typeof(route_decision) = 'object'", name="ck_agent_run_route"), CheckConstraint("step_count BETWEEN 0 AND 6", name="ck_agent_run_steps"), CheckConstraint("specialist_count BETWEEN 0 AND 3", name="ck_agent_run_specialists"), Index("ix_agent_runs_user_created", "user_id", text("created_at DESC")), Index("ix_agent_runs_status_created", "status", text("created_at DESC")), {"schema": SCHEMA})
    id: Mapped[UUID] = _uuid_pk(); user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True)); conversation_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True)); client_request_id: Mapped[str] = mapped_column(String(64)); input_summary: Mapped[str] = mapped_column(String(1000)); status: Mapped[str] = mapped_column(String(24), server_default=text("'created'")); route_decision: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True)); model_name: Mapped[str | None] = mapped_column(String(100)); model_version_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey(f"{SCHEMA}.model_versions.id", ondelete="SET NULL")); step_count: Mapped[int] = mapped_column(SmallInteger, server_default=text("0")); specialist_count: Mapped[int] = mapped_column(SmallInteger, server_default=text("0")); finish_reason: Mapped[str | None] = mapped_column(String(50)); error_code: Mapped[str | None] = mapped_column(String(100)); started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True)); finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True)); created_at: Mapped[datetime] = _timestamp(); updated_at: Mapped[datetime] = _timestamp()


class AgentStep(Base):
    __tablename__ = "agent_steps"
    __table_args__ = (UniqueConstraint("run_id", "sequence_no"), CheckConstraint("sequence_no BETWEEN 1 AND 6", name="ck_agent_step_sequence"), CheckConstraint("status IN ('created', 'running', 'awaiting_approval', 'succeeded', 'partial', 'failed', 'cancelled')", name="ck_agent_step_status"), CheckConstraint("jsonb_typeof(input_summary) = 'object'", name="ck_agent_step_input"), CheckConstraint("jsonb_typeof(output_summary) = 'object'", name="ck_agent_step_output"), Index("ix_agent_steps_run", "run_id", "sequence_no"), Index("ix_agent_steps_signature", "run_id", "signature_hash", postgresql_where=text("signature_hash IS NOT NULL")), {"schema": SCHEMA})
    id: Mapped[UUID] = _uuid_pk(); run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey(f"{SCHEMA}.agent_runs.id", ondelete="CASCADE")); parent_step_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey(f"{SCHEMA}.agent_steps.id", ondelete="SET NULL")); sequence_no: Mapped[int] = mapped_column(SmallInteger); agent_code: Mapped[str] = mapped_column(String(50)); task_type: Mapped[str] = mapped_column(String(50)); status: Mapped[str] = mapped_column(String(16), server_default=text("'created'")); input_summary: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb")); output_summary: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb")); signature_hash: Mapped[str | None] = mapped_column(CHAR(64)); error_code: Mapped[str | None] = mapped_column(String(100)); started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True)); finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True)); created_at: Mapped[datetime] = _timestamp()


class ToolCall(Base):
    __tablename__ = "tool_calls"
    __table_args__ = (CheckConstraint("status IN ('prepared', 'awaiting_approval', 'authorized', 'running', 'succeeded', 'failed', 'rejected', 'expired')", name="ck_tool_call_status"), CheckConstraint("jsonb_typeof(arguments_summary) = 'object'", name="ck_tool_call_arguments"), CheckConstraint("jsonb_typeof(result_summary) = 'object'", name="ck_tool_call_result"), CheckConstraint("duration_ms IS NULL OR duration_ms >= 0", name="ck_tool_call_duration"), Index("ix_tool_calls_run_created", "run_id", "created_at"), Index("uq_tool_calls_idempotency", "run_id", "tool_name", "idempotency_key", unique=True, postgresql_where=text("idempotency_key IS NOT NULL")), {"schema": SCHEMA})
    id: Mapped[UUID] = _uuid_pk(); run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey(f"{SCHEMA}.agent_runs.id", ondelete="CASCADE")); step_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey(f"{SCHEMA}.agent_steps.id", ondelete="CASCADE")); tool_name: Mapped[str] = mapped_column(String(100)); tool_version: Mapped[str] = mapped_column(String(30)); arguments_hash: Mapped[str] = mapped_column(CHAR(64)); arguments_summary: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb")); status: Mapped[str] = mapped_column(String(24), server_default=text("'prepared'")); idempotency_key: Mapped[str | None] = mapped_column(String(128)); resource_type: Mapped[str | None] = mapped_column(String(100)); resource_id: Mapped[str | None] = mapped_column(String(100)); result_summary: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb")); duration_ms: Mapped[int | None] = mapped_column(Integer); error_code: Mapped[str | None] = mapped_column(String(100)); audit_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True)); started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True)); finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True)); created_at: Mapped[datetime] = _timestamp()


class ApprovalRequestModel(Base):
    __tablename__ = "approval_requests"
    __table_args__ = (CheckConstraint("status IN ('pending', 'approved', 'rejected', 'expired', 'consumed')", name="ck_approval_status"), CheckConstraint("expires_at > created_at", name="ck_approval_expiry"), CheckConstraint("(status = 'pending' AND decided_at IS NULL) OR (status <> 'pending' AND status = 'expired') OR (status IN ('approved', 'rejected', 'consumed') AND decided_by IS NOT NULL AND decided_at IS NOT NULL)", name="ck_approval_decision"), Index("uq_approval_one_pending_tool_call", "tool_call_id", unique=True, postgresql_where=text("status = 'pending'")), Index("ix_approval_user_pending", "user_id", "expires_at", postgresql_where=text("status = 'pending'")), {"schema": SCHEMA})
    id: Mapped[UUID] = _uuid_pk(); run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey(f"{SCHEMA}.agent_runs.id", ondelete="CASCADE")); tool_call_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey(f"{SCHEMA}.tool_calls.id", ondelete="CASCADE")); user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True)); action: Mapped[str] = mapped_column(String(100)); display_summary: Mapped[str] = mapped_column(String(1000)); arguments_hash: Mapped[str] = mapped_column(CHAR(64)); status: Mapped[str] = mapped_column(String(16), server_default=text("'pending'")); expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True)); decided_by: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True)); decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True)); created_at: Mapped[datetime] = _timestamp()


class AgentHandoff(Base):
    __tablename__ = "agent_handoffs"
    __table_args__ = (CheckConstraint("from_agent <> to_agent", name="ck_handoff_distinct_agents"), CheckConstraint("jsonb_typeof(structured_context) = 'object'", name="ck_handoff_context"), CheckConstraint("jsonb_typeof(constraints) = 'array'", name="ck_handoff_constraints"), CheckConstraint("jsonb_typeof(artifact_refs) = 'array'", name="ck_handoff_artifacts"), CheckConstraint("status IN ('created', 'accepted', 'running', 'succeeded', 'failed', 'cancelled')", name="ck_handoff_status"), Index("ix_handoffs_run_created", "run_id", "created_at"), {"schema": SCHEMA})
    id: Mapped[UUID] = _uuid_pk(); run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey(f"{SCHEMA}.agent_runs.id", ondelete="CASCADE")); from_agent: Mapped[str] = mapped_column(String(50)); to_agent: Mapped[str] = mapped_column(String(50)); task_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True)); context_summary: Mapped[str] = mapped_column(String(1000)); structured_context: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb")); constraints: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'::jsonb")); artifact_refs: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'::jsonb")); status: Mapped[str] = mapped_column(String(16), server_default=text("'created'")); error_code: Mapped[str | None] = mapped_column(String(100)); created_at: Mapped[datetime] = _timestamp(); completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentRuntimeCommand(Base):
    __tablename__ = "agent_runtime_commands"
    __table_args__ = (
        CheckConstraint("action IN ('start', 'resume', 'cancel')", name="ck_runtime_command_action"),
        CheckConstraint("(action = 'resume' AND approval_id IS NOT NULL) OR (action <> 'resume' AND approval_id IS NULL)", name="ck_runtime_command_approval"),
        CheckConstraint("jsonb_typeof(payload) = 'object'", name="ck_runtime_command_payload"),
        CheckConstraint("status IN ('pending', 'processing', 'succeeded', 'failed')", name="ck_runtime_command_status"),
        CheckConstraint("attempt_count >= 0 AND max_attempts BETWEEN 1 AND 10 AND attempt_count <= max_attempts", name="ck_runtime_command_attempts"),
        CheckConstraint(
            "(status = 'processing' AND claimed_by IS NOT NULL AND claimed_at IS NOT NULL) OR status <> 'processing'",
            name="ck_runtime_command_claim",
        ),
        CheckConstraint(
            "(status IN ('succeeded', 'failed') AND completed_at IS NOT NULL) OR "
            "(status NOT IN ('succeeded', 'failed') AND completed_at IS NULL)",
            name="ck_runtime_command_completion",
        ),
        Index("ix_runtime_commands_queue", "status", "available_at", "created_at"),
        Index("ix_runtime_commands_run", "run_id", "created_at"),
        Index("uq_runtime_command_active_action", "run_id", "action", text("COALESCE(approval_id, '00000000-0000-0000-0000-000000000000'::uuid)"), unique=True, postgresql_where=text("status IN ('pending', 'processing')")),
        {"schema": SCHEMA},
    )
    id: Mapped[UUID] = _uuid_pk(); run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey(f"{SCHEMA}.agent_runs.id", ondelete="CASCADE")); action: Mapped[str] = mapped_column(String(16)); approval_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey(f"{SCHEMA}.approval_requests.id", ondelete="SET NULL")); payload: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb")); status: Mapped[str] = mapped_column(String(16), server_default=text("'pending'")); attempt_count: Mapped[int] = mapped_column(SmallInteger, server_default=text("0")); max_attempts: Mapped[int] = mapped_column(SmallInteger, server_default=text("3")); available_at: Mapped[datetime] = _timestamp(); claimed_by: Mapped[str | None] = mapped_column(String(100)); claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True)); completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True)); error_code: Mapped[str | None] = mapped_column(String(100)); created_at: Mapped[datetime] = _timestamp(); updated_at: Mapped[datetime] = _timestamp()


class AgentRuntimeCheckpoint(Base):
    __tablename__ = "agent_runtime_checkpoints"
    __table_args__ = (
        CheckConstraint("state_version > 0", name="ck_runtime_checkpoint_version"),
        CheckConstraint("state_sha256 ~ '^[0-9a-f]{64}$'", name="ck_runtime_checkpoint_hash"),
        CheckConstraint("expires_at > updated_at", name="ck_runtime_checkpoint_expiry"),
        Index("ix_runtime_checkpoints_expiry", "expires_at"), {"schema": SCHEMA},
    )
    run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey(f"{SCHEMA}.agent_runs.id", ondelete="CASCADE"), primary_key=True); state_version: Mapped[int] = mapped_column(Integer); encrypted_state: Mapped[str] = mapped_column(Text); state_sha256: Mapped[str] = mapped_column(CHAR(64)); expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True)); updated_at: Mapped[datetime] = _timestamp()


class AgentRunEvent(Base):
    __tablename__ = "agent_run_events"
    __table_args__ = (
        UniqueConstraint("run_id", "sequence", name="uq_agent_run_event_sequence"),
        CheckConstraint("sequence > 0", name="ck_agent_run_event_sequence"),
        CheckConstraint("event IN ('meta', 'route', 'agent_step', 'tool_call', 'approval_required', 'handoff', 'delta', 'sources', 'done', 'error')", name="ck_agent_run_event_type"),
        CheckConstraint("jsonb_typeof(data) = 'object'", name="ck_agent_run_event_data"),
        Index("ix_agent_run_events_replay", "run_id", "sequence"), {"schema": SCHEMA},
    )
    id: Mapped[UUID] = _uuid_pk(); run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey(f"{SCHEMA}.agent_runs.id", ondelete="CASCADE")); sequence: Mapped[int] = mapped_column(Integer); event: Mapped[str] = mapped_column(String(32)); data: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb")); request_id: Mapped[str | None] = mapped_column(String(64)); occurred_at: Mapped[datetime] = _timestamp()


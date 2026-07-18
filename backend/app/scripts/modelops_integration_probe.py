"""ModelOps 真实集成验收探针（#197）。

真实 PostgreSQL + 真实 API + 真实训练/评估执行，覆盖：
1. 全链：数据集上传→校验→冻结→真实 LoRA 训练→模型登记→本地评估→激活原子切换。
2. 取消：领取前取消的任务不被执行。
3. 敏感数据版本创建训练被拒绝（TRAINING_DATASET_NOT_READY）。
4. 崩溃恢复：卡 training 状态的过期任务被 requeue 并真实训练完成。
5. 并发：两个 Worker 并发领取两个任务，每个任务恰好执行一次。
探针数据全部精确清理；输出公开摘要，不含密钥/连接串。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
from sqlalchemy import delete, func, select, text

API_BASE = "http://127.0.0.1:8000"
ADMIN = {"username": "admin01", "password": "CampusPilot-Demo-2026!"}
TINY_MODEL = "hf-internal-testing/tiny-random-gpt2"
TRAIN_CONFIG = {"epochs": 1, "learning_rate": 0.001, "batch_size": 2}
TAG = f"mop-{uuid4().hex[:8]}"
DATASET_TEXT = (
    '{"text": "四川大学望江校区位于成都市武侯区一环路南一段24号。"}\n'
    '{"text": "学生可通过校园服务中心提交宿舍报修工单。"}\n'
    '{"text": "电费充值申请提交后由系统统一处理。"}\n'
    '{"text": "图书馆提供自习座位预约服务。"}\n'
)


class ProbeFailure(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def require(condition: bool, code: str) -> None:
    if not condition:
        raise ProbeFailure(code)


async def main() -> None:
    os.environ.setdefault("MODELOPS_EXECUTION_MODE", "local")
    from app.core.config import Settings
    from app.infrastructure.database import Database
    from app.modules.agent_platform.artifact_store import DatasetArtifactStore
    from app.modules.agent_platform.evaluation_worker import EvaluationWorker, build_production_evaluator_registry
    from app.modules.agent_platform.models import EvaluationJob, ModelVersion, TrainingJob
    from app.modules.agent_platform.training_worker import LoraTrainingBackend, TrainingWorker

    settings = Settings()
    database = Database.from_settings(settings)
    evidence: dict[str, Any] = {}
    model_root = Path(settings.model_artifact_root)
    created_job_ids: list[UUID] = []
    created_eval_ids: list[UUID] = []
    created_model_ids: list[UUID] = []
    created_dataset_ids: list[UUID] = []
    artifact_dirs: list[Path] = []

    def training_worker(worker_id: str) -> TrainingWorker:
        return TrainingWorker(
            database.session,
            LoraTrainingBackend(model_root),
            DatasetArtifactStore(Path(settings.dataset_artifact_root), ttl_seconds=10 * 365 * 86400),
            model_root,
            poll_interval=0.5,
            stale_after=timedelta(seconds=3),
        )

    try:
        async with httpx.AsyncClient(base_url=API_BASE, timeout=120) as client:
            login = await client.post("/api/v1/auth/login", json=ADMIN)
            require(login.status_code == 200, "MODELOPS_PROBE_LOGIN")
            token = login.json()["data"]["access_token"]

            def headers(key: str) -> dict[str, str]:
                return {"Authorization": f"Bearer {token}", "Idempotency-Key": f"{TAG}-{key}"}

            # ---- 1. 数据集全生命周期 ----
            response = await client.post(
                "/api/v1/datasets",
                json={"name": f"probe-modelops-{TAG}", "purpose": "evaluation", "description": "modelops probe"},
                headers=headers("ds-create"),
            )
            require(response.status_code == 201, "MODELOPS_PROBE_DATASET_CREATE")
            dataset_id = UUID(response.json()["data"]["id"])
            created_dataset_ids.append(dataset_id)
            upload = await client.post(
                f"/api/v1/datasets/{dataset_id}/uploads",
                files={"file": ("probe.jsonl", DATASET_TEXT.encode("utf-8"), "application/jsonl")},
                headers=headers("ds-upload"),
            )
            require(upload.status_code == 201, "MODELOPS_PROBE_DATASET_UPLOAD")
            artifact = upload.json()["data"]
            version = await client.post(
                f"/api/v1/datasets/{dataset_id}/versions",
                json={"artifact_key": artifact["artifact_key"], "artifact_sha256": artifact["artifact_sha256"], "format": "jsonl", "sample_count": 4, "split_config": {}, "contains_sensitive_data": False},
                headers=headers("ds-version"),
            )
            require(version.status_code == 201 and version.json()["data"]["validation_status"] == "valid", "MODELOPS_PROBE_DATASET_VERSION")
            freeze = await client.post(f"/api/v1/datasets/{dataset_id}/versions/1/freeze", headers=headers("ds-freeze"))
            require(freeze.status_code == 200, "MODELOPS_PROBE_DATASET_FREEZE")
            evidence["dataset_chain"] = True

            # ---- 2. 真实 LoRA 训练全链 ----
            training = await client.post(
                "/api/v1/training-jobs",
                json={"dataset_id": str(dataset_id), "dataset_version": 1, "base_model": TINY_MODEL, "method": "lora", "config": TRAIN_CONFIG, "resource_limits": {"max_steps": 4}},
                headers=headers("train-main"),
            )
            require(training.status_code == 202, "MODELOPS_PROBE_TRAIN_CREATE")
            job_id = UUID(training.json()["data"]["id"])
            created_job_ids.append(job_id)
            await training_worker("probe-main").run_once()
            job = await _training_row(database, job_id)
            require(job["status"] == "succeeded" and job["progress"] == 100, "MODELOPS_PROBE_TRAIN_TERMINAL")
            artifact_path = model_root / "artifacts" / str(job_id) / "adapter_model.safetensors"
            require(artifact_path.is_file(), "MODELOPS_PROBE_ARTIFACT_MISSING")
            artifact_dirs.append(artifact_path.parent)
            digest = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
            require(digest == job["artifact_sha256"], "MODELOPS_PROBE_ARTIFACT_HASH")
            require(job["metrics"].get("steps", 0) > 0 and "final_loss" in job["metrics"], "MODELOPS_PROBE_TRAIN_METRICS")
            evidence["training_chain"] = True

            # ---- 3. 登记 → 本地评估 → 激活 ----
            register = await client.post(
                "/api/v1/models",
                json={"name": f"probe-modelops-{TAG}", "purpose": "agent_router", "provider": "local", "base_model": TINY_MODEL, "version": "v1", "artifact_key": job["artifact_key"], "artifact_sha256": job["artifact_sha256"], "training_job_id": str(job_id), "config": {}},
                headers=headers("model-register"),
            )
            require(register.status_code == 201, "MODELOPS_PROBE_MODEL_REGISTER")
            model_id = UUID(register.json()["data"]["id"])
            created_model_ids.append(model_id)
            evaluation = await client.post(
                "/api/v1/evaluations",
                json={"target_type": "model", "target_id": str(model_id), "dataset_id": str(dataset_id), "dataset_version": 1, "config": {}},
                headers=headers("eval-create"),
            )
            require(evaluation.status_code in (201, 202), "MODELOPS_PROBE_EVAL_CREATE")
            evaluation_id = UUID(evaluation.json()["data"]["id"])
            created_eval_ids.append(evaluation_id)
            eval_worker = EvaluationWorker(database.session, build_production_evaluator_registry(settings, database.session))
            await eval_worker.run_once()
            eval_row = await _evaluation_row(database, evaluation_id)
            require(eval_row["status"] == "succeeded", "MODELOPS_PROBE_EVAL_TERMINAL")
            metric_names = {row[0] for row in eval_row["metrics"]}
            require({"base_loss", "lora_loss", "loss_improvement"} <= metric_names, "MODELOPS_PROBE_EVAL_METRICS")
            evidence["local_evaluation"] = True

            activate = await client.post(f"/api/v1/models/{model_id}/activate", headers=headers("model-activate"))
            require(activate.status_code == 200, "MODELOPS_PROBE_MODEL_ACTIVATE")
            async with database.session() as session:
                active_count = (
                    await session.execute(
                        select(func.count()).select_from(ModelVersion).where(
                            ModelVersion.purpose == "agent_router",
                            ModelVersion.status == "active",
                        )
                    )
                ).scalar_one()
            require(active_count == 1, "MODELOPS_PROBE_ACTIVATE_ATOMIC")
            evidence["activation_atomic"] = True

            # ---- 4. 取消：领取前取消不执行 ----
            cancel_create = await client.post(
                "/api/v1/training-jobs",
                json={"dataset_id": str(dataset_id), "dataset_version": 1, "base_model": TINY_MODEL, "method": "lora", "config": TRAIN_CONFIG, "resource_limits": {"max_steps": 2}},
                headers=headers("train-cancel"),
            )
            cancel_job = UUID(cancel_create.json()["data"]["id"])
            created_job_ids.append(cancel_job)
            cancel_response = await client.post(f"/api/v1/training-jobs/{cancel_job}/cancel", headers=headers("train-cancel-do"))
            require(cancel_response.status_code == 200, "MODELOPS_PROBE_CANCEL_REQUEST")
            await training_worker("probe-cancel").run_once()
            cancelled = await _training_row(database, cancel_job)
            require(cancelled["status"] == "cancelled" and cancelled["artifact_key"] is None, "MODELOPS_PROBE_CANCEL_SKIPPED")
            evidence["cancel_skips_execution"] = True

            # ---- 5. 敏感数据版本拒绝训练 ----
            sensitive = await client.post(
                "/api/v1/datasets",
                json={"name": f"probe-sensitive-{TAG}", "purpose": "evaluation", "description": "sensitive probe"},
                headers=headers("ds-create-sensitive"),
            )
            sensitive_id = UUID(sensitive.json()["data"]["id"])
            created_dataset_ids.append(sensitive_id)
            upload2 = await client.post(
                f"/api/v1/datasets/{sensitive_id}/uploads",
                files={"file": ("probe.jsonl", DATASET_TEXT.encode("utf-8"), "application/jsonl")},
                headers=headers("ds-upload-sensitive"),
            )
            artifact2 = upload2.json()["data"]
            await client.post(
                f"/api/v1/datasets/{sensitive_id}/versions",
                json={"artifact_key": artifact2["artifact_key"], "artifact_sha256": artifact2["artifact_sha256"], "format": "jsonl", "sample_count": 4, "split_config": {}, "contains_sensitive_data": True},
                headers=headers("ds-version-sensitive"),
            )
            await client.post(f"/api/v1/datasets/{sensitive_id}/versions/1/freeze", headers=headers("ds-freeze-sensitive"))
            rejected = await client.post(
                "/api/v1/training-jobs",
                json={"dataset_id": str(sensitive_id), "dataset_version": 1, "base_model": TINY_MODEL, "method": "lora", "config": TRAIN_CONFIG, "resource_limits": {}},
                headers=headers("train-sensitive"),
            )
            require(rejected.status_code == 409 and rejected.json().get("code") == "TRAINING_DATASET_NOT_READY", "MODELOPS_PROBE_SENSITIVE_REJECT")
            evidence["sensitive_rejected"] = True

            # ---- 6. 崩溃恢复：过期 training 任务被 requeue 并真实完成 ----
            async with database.session() as session:
                version_row = (
                    await session.execute(
                        text("SELECT id FROM agent_platform.dataset_versions WHERE dataset_id=:d LIMIT 1"), {"d": dataset_id}
                    )
                ).mappings().first()
                stale_id = uuid4()
                await session.execute(
                    text(
                        "INSERT INTO agent_platform.training_jobs (id, dataset_version_id, base_model, method, config, status, progress, metrics, created_by, created_at, updated_at)"
                        " VALUES (:id, :dv, :bm, 'lora', :cfg, 'training', 40, '{}', (SELECT id FROM platform.users WHERE username='admin01'), now() - interval '1 hour', now() - interval '1 hour')"
                    ),
                    {"id": stale_id, "dv": version_row["id"], "bm": TINY_MODEL, "cfg": json.dumps(TRAIN_CONFIG)},
                )
                await session.commit()
            created_job_ids.append(stale_id)
            await training_worker("probe-stale").run_once()
            stale = await _training_row(database, stale_id)
            require(stale["status"] == "succeeded" and stale["progress"] == 100, "MODELOPS_PROBE_STALE_RECOVERY")
            artifact_dirs.append(model_root / "artifacts" / str(stale_id))
            evidence["stale_recovery"] = True

            # ---- 7. 并发领取：两个 Worker 各执行一个任务 ----
            concurrent_ids: list[UUID] = []
            for index in range(2):
                response = await client.post(
                    "/api/v1/training-jobs",
                    json={"dataset_id": str(dataset_id), "dataset_version": 1, "base_model": TINY_MODEL, "method": "lora", "config": TRAIN_CONFIG, "resource_limits": {"max_steps": 2}},
                    headers=headers(f"train-concurrent-{index}"),
                )
                concurrent_ids.append(UUID(response.json()["data"]["id"]))
            created_job_ids.extend(concurrent_ids)
            worker_a, worker_b = training_worker("probe-a"), training_worker("probe-b")
            claimed = await asyncio.gather(worker_a.run_once(), worker_b.run_once())
            require(set(claimed) == set(concurrent_ids), "MODELOPS_PROBE_CONCURRENT_CLAIM")
            for current in concurrent_ids:
                row = await _training_row(database, current)
                require(
                    row["status"] == "succeeded",
                    f"MODELOPS_PROBE_CONCURRENT_TERMINAL_{row.get('status')}_{row.get('error_code')}",
                )
                artifact_dirs.append(model_root / "artifacts" / str(current))
            evidence["concurrent_once_each"] = True
    finally:
        async with database.session() as session:
            async with session.begin():
                if created_eval_ids:
                    await session.execute(delete(EvaluationJob).where(EvaluationJob.id.in_(created_eval_ids)))
                if created_model_ids:
                    await session.execute(delete(ModelVersion).where(ModelVersion.id.in_(created_model_ids)))
                if created_job_ids:
                    await session.execute(delete(TrainingJob).where(TrainingJob.id.in_(created_job_ids)))
                for dataset_id in created_dataset_ids:
                    await session.execute(
                        text("UPDATE agent_platform.datasets SET deleted_at=now() WHERE id=:d"), {"d": dataset_id}
                    )
                await session.execute(text("DELETE FROM platform.audit_logs WHERE request_id LIKE :p"), {"p": f"%{TAG}%"})
                await session.execute(
                    text("DELETE FROM platform.idempotency_records WHERE idempotency_key LIKE :p"), {"p": f"{TAG}-%"}
                )
        for artifact_dir in artifact_dirs:
            if artifact_dir.is_dir():
                for item in artifact_dir.iterdir():
                    item.unlink()
                artifact_dir.rmdir()
        # 恢复 agent_router 种子 active 状态（并发激活探针原子切换会影响它）
        async with database.session() as session:
            async with session.begin():
                await session.execute(
                    text("UPDATE agent_platform.model_versions SET status='active', activated_at=now() WHERE id='c659915f-3553-4f8b-8c10-19ed507e9649' AND status <> 'active'")
                )
        await database.dispose()
    evidence["cleanup"] = True
    print(json.dumps({"ok": True, "evidence": evidence}, ensure_ascii=False, sort_keys=True))


async def _training_row(database, job_id: UUID) -> dict[str, Any]:
    async with database.session() as session:
        row = (
            await session.execute(
                text("SELECT status, progress, artifact_key, artifact_sha256, metrics, error_code FROM agent_platform.training_jobs WHERE id=:id"),
                {"id": job_id},
            )
        ).mappings().first()
    return dict(row or {})


async def _evaluation_row(database, evaluation_id: UUID) -> dict[str, Any]:
    async with database.session() as session:
        row = (
            await session.execute(
                text("SELECT status, error_code FROM agent_platform.evaluation_jobs WHERE id=:id"), {"id": evaluation_id}
            )
        ).mappings().first()
        metrics = (
            await session.execute(
                text("SELECT name, value FROM agent_platform.evaluation_metrics WHERE evaluation_id=:id"), {"id": evaluation_id}
            )
        ).all()
    return {**dict(row or {}), "metrics": metrics}


def run() -> None:
    try:
        asyncio.run(main())
    except ProbeFailure as exc:
        print(json.dumps({"ok": False, "error": exc.code}, ensure_ascii=False))
        raise SystemExit(1) from exc


if __name__ == "__main__":
    run()

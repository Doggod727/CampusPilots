"""真实 Training Worker：SKIP LOCKED 领取训练任务并执行最小真实 LoRA 训练。

- 仅本模块惰性加载 torch/transformers/peft（[modelops] 可选依赖组）；API 与其余测试不依赖。
- LoRA 支持 CPU/CUDA；QLoRA 仅在 CUDA 与 bitsandbytes 可用时执行，否则稳定失败。
- 基座模型、缓存与产物固定存放配置的模型根目录；逐阶段提交 progress，支持取消与安全错误。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent_platform.artifact_store import DatasetArtifactStore
from app.modules.agent_platform.models import DatasetVersion, TrainingJob
from app.modules.agent_platform.training import TrainingRepository

TRAINING_EXECUTION_FAILED = "TRAINING_EXECUTION_FAILED"
TRAINING_RESOURCE_UNAVAILABLE = "TRAINING_RESOURCE_UNAVAILABLE"
TRAINING_DATASET_INVALID = "TRAINING_DATASET_INVALID"
ADAPTER_FILENAME = "adapter_model.safetensors"


class TrainingCancelled(Exception):
    """取消请求后的安全停止信号。"""


class TrainingResourceUnavailable(Exception):
    """QLoRA 等资源前置不满足时的稳定失败信号。"""


@dataclass(frozen=True)
class TrainingOutcome:
    artifact: bytes
    artifact_sha256: str
    metrics: dict[str, Any]


class TrainingBackendPort(Protocol):
    def execute_sync(
        self,
        job: TrainingJob,
        samples: Sequence[str],
        on_progress: Callable[[int], None],
        should_cancel: Callable[[], bool],
    ) -> TrainingOutcome: ...


def parse_training_samples(artifact: bytes, fmt: str) -> tuple[str, ...]:
    """从数据集产物解析可用文本样本；无可用样本时按稳定错误拒绝。"""

    if fmt != "jsonl":
        raise ValueError(TRAINING_DATASET_INVALID)
    samples: list[str] = []
    for raw_line in artifact.decode("utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            text = row.get("text")
            if isinstance(text, str) and text.strip():
                samples.append(text.strip())
                continue
            query, positive = row.get("query"), row.get("positive")
            if isinstance(query, str) and isinstance(positive, str) and query.strip() and positive.strip():
                samples.append(f"{query.strip()}\n{positive.strip()}")
    if not samples:
        raise ValueError(TRAINING_DATASET_INVALID)
    return tuple(samples)


class LoraTrainingBackend:
    """最小真实 LoRA 后端：本地基座模型 + 手动训练循环（CPU/CUDA）。"""

    def __init__(self, model_root: Path) -> None:
        self._model_root = model_root

    def execute_sync(
        self,
        job: TrainingJob,
        samples: Sequence[str],
        on_progress: Callable[[int], None],
        should_cancel: Callable[[], bool],
    ) -> TrainingOutcome:
        if job.method == "qlora" and not self._qlora_available():
            raise TrainingResourceUnavailable()
        import torch
        from peft import LoraConfig, get_peft_model
        from transformers import AutoModelForCausalLM, AutoTokenizer

        base_dir = self._resolve_base_model(job.base_model)
        tokenizer = AutoTokenizer.from_pretrained(base_dir)
        if tokenizer.pad_token is None:
            tokenizer.pad_token = tokenizer.eos_token
        model = AutoModelForCausalLM.from_pretrained(base_dir)
        config = job.config or {}
        lora = LoraConfig(
            r=int(config.get("lora_r", 8)),
            lora_alpha=int(config.get("lora_alpha", 16)),
            lora_dropout=float(config.get("lora_dropout", 0.05)),
            target_modules=self._target_modules(model),
            task_type="CAUSAL_LM",
        )
        model = get_peft_model(model, lora)
        model.train()
        device = "cuda" if torch.cuda.is_available() else "cpu"
        model.to(device)

        encoded = [tokenizer(text, truncation=True, max_length=256)["input_ids"] for text in samples]
        batch_size = int(config.get("batch_size", 2))
        epochs = int(config.get("epochs", 1))
        learning_rate = float(config.get("learning_rate", 0.0002))
        max_steps = int((job.resource_limits or {}).get("max_steps", 0)) or None
        optimizer = torch.optim.AdamW(model.parameters(), lr=learning_rate)

        def collate(batch: list[list[int]]) -> dict[str, Any]:
            width = max(len(item) for item in batch)
            input_ids = [item + [tokenizer.pad_token_id] * (width - len(item)) for item in batch]
            labels = [item + [-100] * (width - len(item)) for item in batch]
            return {
                "input_ids": torch.tensor(input_ids, dtype=torch.long, device=device),
                "labels": torch.tensor(labels, dtype=torch.long, device=device),
            }

        batches = [encoded[index : index + batch_size] for index in range(0, len(encoded), batch_size)]
        if not batches:
            raise ValueError(TRAINING_DATASET_INVALID)
        total_steps = epochs * len(batches)
        if max_steps is not None:
            total_steps = min(total_steps, max_steps)

        def mean_loss() -> float:
            model.eval()
            losses: list[float] = []
            with torch.no_grad():
                for batch in batches:
                    outputs = model(**collate(batch))
                    losses.append(float(outputs.loss.item()))
            model.train()
            return sum(losses) / len(losses)

        initial_loss = mean_loss()
        step = 0
        final_loss = initial_loss
        for _epoch in range(epochs):
            for batch in batches:
                if should_cancel():
                    raise TrainingCancelled()
                if max_steps is not None and step >= max_steps:
                    break
                outputs = model(**collate(batch))
                outputs.loss.backward()
                optimizer.step()
                optimizer.zero_grad()
                step += 1
                final_loss = float(outputs.loss.item())
                on_progress(min(85, 10 + int(75 * step / max(total_steps, 1))))
            if max_steps is not None and step >= max_steps:
                break
        evaluated_loss = mean_loss()
        on_progress(90)

        artifact_dir = self._model_root / "artifacts" / str(job.id)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        model.save_pretrained(artifact_dir, safe_serialization=True)
        artifact_path = artifact_dir / ADAPTER_FILENAME
        artifact = artifact_path.read_bytes()
        return TrainingOutcome(
            artifact=artifact,
            artifact_sha256=hashlib.sha256(artifact).hexdigest(),
            metrics={
                "initial_loss": round(initial_loss, 6),
                "final_loss": round(evaluated_loss, 6),
                "last_train_loss": round(final_loss, 6),
                "steps": step,
                "samples": len(samples),
                "epochs": epochs,
                "device": device,
            },
        )

    def _resolve_base_model(self, base_model: str) -> Path:
        safe = base_model.replace("/", "--")
        target = self._model_root / "base-models" / safe
        if (target / "config.json").exists():
            return target
        import os

        from huggingface_hub import snapshot_download

        os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
        target.mkdir(parents=True, exist_ok=True)
        snapshot_download(repo_id=base_model, local_dir=str(target))
        return target

    @staticmethod
    def _target_modules(model: Any) -> list[str]:
        names = {name.split(".")[-1] for name, _ in model.named_modules()}
        for candidates in (("c_attn",), ("q_proj", "v_proj"), ("query_key_value",)):
            if all(item in names for item in candidates):
                return list(candidates)
        return ["c_attn"]

    @staticmethod
    def _qlora_available() -> bool:
        try:
            import bitsandbytes  # noqa: F401
            import torch

            return torch.cuda.is_available()
        except ImportError:
            return False


class TrainingWorker:
    def __init__(
        self,
        sessions: Callable[[], AbstractAsyncContextManager],
        backend: TrainingBackendPort,
        dataset_store: DatasetArtifactStore,
        artifact_root: Path,
        *,
        poll_interval: float = 5.0,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._sessions = sessions
        self._backend = backend
        self._datasets = dataset_store
        self._artifact_root = artifact_root
        self._poll_interval = poll_interval
        self._now = now or (lambda: datetime.now(UTC))

    async def run_once(self) -> UUID | None:
        async with self._sessions() as session:
            async with session.begin():
                claimed = await TrainingRepository(session).claim(1)
                if not claimed:
                    return None
                job = claimed[0]
                job.status = "preparing"
                job.started_at = self._now()
                job.progress = 5
                job.updated_at = self._now()
                job_id = job.id
        cancel_event = threading.Event()
        progress_state = {"value": 5}
        watcher = asyncio.create_task(self._watch(job_id, cancel_event, progress_state))
        try:
            async with self._sessions() as session:
                version = await session.get(DatasetVersion, job.dataset_version_id)
            if version is None:
                raise ValueError(TRAINING_DATASET_INVALID)
            artifact = await self._datasets.read(version.artifact_key, expected_sha256=version.artifact_sha256)
            samples = parse_training_samples(artifact, version.format)
            await self._persist(job_id, status="training", progress=10)
            outcome = await asyncio.to_thread(
                self._backend.execute_sync,
                job,
                samples,
                lambda value: progress_state.__setitem__("value", value),
                cancel_event.is_set,
            )
            self._write_artifact(job_id, outcome.artifact)
            await self._persist(
                job_id,
                status="succeeded",
                progress=100,
                artifact_key=f"training/{job_id}/{ADAPTER_FILENAME}",
                artifact_sha256=outcome.artifact_sha256,
                metrics=outcome.metrics,
                finished=True,
            )
        except TrainingCancelled:
            await self._persist(job_id, status="cancelled", finished=True)
        except TrainingResourceUnavailable:
            await self._persist(job_id, status="failed", error_code=TRAINING_RESOURCE_UNAVAILABLE, finished=True)
        except ValueError as exc:
            code = exc.args[0] if exc.args and isinstance(exc.args[0], str) else TRAINING_EXECUTION_FAILED
            if code not in {TRAINING_DATASET_INVALID, TRAINING_EXECUTION_FAILED}:
                code = TRAINING_EXECUTION_FAILED
            await self._persist(job_id, status="failed", error_code=code, finished=True)
        except Exception:
            import traceback

            traceback.print_exc()
            await self._persist(job_id, status="failed", error_code=TRAINING_EXECUTION_FAILED, finished=True)
        finally:
            watcher.cancel()
        return job_id

    def _write_artifact(self, job_id: UUID, artifact: bytes) -> None:
        target_dir = self._artifact_root / "artifacts" / str(job_id)
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / ADAPTER_FILENAME).write_bytes(artifact)

    async def _watch(self, job_id: UUID, cancel_event: threading.Event, progress_state: dict) -> None:
        last_progress = progress_state["value"]
        try:
            while True:
                await asyncio.sleep(2)
                async with self._sessions() as session:
                    current = await session.get(TrainingJob, job_id)
                if current is None:
                    return
                if current.status == "cancelled":
                    cancel_event.set()
                    return
                if progress_state["value"] != last_progress:
                    last_progress = progress_state["value"]
                    await self._persist(job_id, progress=last_progress)
        except asyncio.CancelledError:
            return

    async def _persist(self, job_id: UUID, **changes: Any) -> None:
        finished = changes.pop("finished", False)
        async with self._sessions() as session:
            async with session.begin():
                job = (await TrainingRepository(session).get(job_id, lock=True))
                if job is None:
                    return
                for key, value in changes.items():
                    setattr(job, key, value)
                if finished:
                    job.finished_at = self._now()
                job.updated_at = self._now()

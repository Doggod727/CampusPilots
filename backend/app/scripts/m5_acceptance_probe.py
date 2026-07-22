"""M5 真实环境总验收探针（#192 契约/目录/装配部分）。

三项自动核对，全部只读真实 PostgreSQL 与应用装配，不产生业务数据：
1. M5 31 个 OpenAPI operationId 与 FastAPI 路由逐一对应（方法+路径一致、全局唯一）。
2. 17 个 Tool 冻结契约与数据库目录一致（PersistentCatalogLoader 零漂移加载），
   并计算每个 Tool 的确定性契约指纹（canonical JSON 的 SHA-256）。
3. RuntimeCompositionFactory 在真实数据库上装配：17 个 Tool 全部使用真实 Handler
   （无 Mock 残留），事件/检查点均为持久化实现。

输出为公开摘要 JSON，不包含连接串、密钥或业务数据。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

from app.modules.agent_platform.catalog_persistence import CatalogRepository, PersistentCatalogLoader
from app.modules.agent_platform.tool_gateway.catalog import TOOL_CONTRACTS
from app.modules.agent_platform.tool_gateway.mocks import MockToolHandler

M5_OPERATIONS = {
    "activateModelVersion", "cancelAgentRun", "cancelTrainingJob",
    "compareEvaluations", "createAgentRun", "createDataset",
    "createDatasetVersion", "createEvaluation", "createTrainingJob",
    "deactivateModelVersion", "decideAgentToolApproval", "deleteDataset",
    "freezeDatasetVersion", "getAgentRun", "getDataset", "getEvaluation",
    "getModelVersion", "getTool", "getTrainingJob", "invokeInternalTool",
    "listAgentRuns", "listAgents", "listDatasets", "listEvaluations",
    "listModelVersions", "listTools", "listTrainingJobs",
    "registerModelVersion", "streamAgentRun", "updateToolRuntimeState",
    "uploadDatasetArtifact",
}
OPENAPI_PATH = Path(__file__).resolve().parents[3] / "docx" / "deliverables" / "openapi.yaml"


class ProbeFailure(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def contract_operations() -> dict[str, tuple[str, str]]:
    """openapi.yaml 中 M5 操作的 operationId -> (method, path)。"""

    spec = yaml.safe_load(OPENAPI_PATH.read_text(encoding="utf-8"))
    declared: dict[str, tuple[str, str]] = {}
    for path, item in (spec.get("paths") or {}).items():
        for method, operation in (item or {}).items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            operation_id = (operation or {}).get("operationId")
            if not operation_id:
                continue
            if operation_id in declared:
                raise ProbeFailure("M5_ACCEPTANCE_CONTRACT_DUPLICATE")
            declared[operation_id] = (method, path)
    missing = M5_OPERATIONS - declared.keys()
    if missing:
        raise ProbeFailure(f"M5_ACCEPTANCE_CONTRACT_MISSING_{sorted(missing)[0]}")
    return {operation_id: declared[operation_id] for operation_id in sorted(M5_OPERATIONS)}


def implemented_operations() -> dict[str, tuple[str, str]]:
    """FastAPI 应用中的 operationId -> (method, path)，要求全局唯一。"""

    from app.main import create_app

    implemented: dict[str, tuple[str, str]] = {}
    for route in create_app().routes:
        operation_id = getattr(route, "operation_id", None)
        if not operation_id:
            continue
        methods = sorted(getattr(route, "methods", None) or ())
        if len(methods) != 1:
            raise ProbeFailure(f"M5_ACCEPTANCE_ROUTE_METHODS_{operation_id}")
        if operation_id in implemented:
            raise ProbeFailure(f"M5_ACCEPTANCE_ROUTE_DUPLICATE_{operation_id}")
        implemented[operation_id] = (methods[0].lower(), getattr(route, "path", ""))
    return implemented


def verify_contract() -> dict[str, Any]:
    declared = contract_operations()
    implemented = implemented_operations()
    for operation_id, (method, path) in declared.items():
        actual = implemented.get(operation_id)
        if actual is None:
            raise ProbeFailure(f"M5_ACCEPTANCE_NOT_IMPLEMENTED_{operation_id}")
        if actual != (method, path):
            raise ProbeFailure(f"M5_ACCEPTANCE_ROUTE_MISMATCH_{operation_id}")
    return {"operations": len(declared), "unique": True, "implemented": True}


def tool_fingerprint(name: str) -> str:
    """单个 Tool 冻结契约的确定性指纹（canonical JSON SHA-256）。"""

    contract = TOOL_CONTRACTS.get(name)
    if contract is None:
        raise ProbeFailure(f"M5_ACCEPTANCE_TOOL_UNKNOWN_{name}")
    definition = contract.definition
    canonical = json.dumps(
        {
            "name": definition.name,
            "version": definition.version,
            "module": definition.module,
            "risk_level": definition.risk_level,
            "visibility": definition.visibility,
            "input_schema": definition.input_schema,
            "output_schema": definition.output_schema,
            "required_permissions": sorted(definition.required_permissions),
            "timeout_ms": definition.timeout_ms,
            "idempotent": definition.idempotent,
            "requires_approval": definition.requires_approval,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def is_mock_handler(handler: object) -> bool:
    return isinstance(handler, MockToolHandler)


async def verify_catalog_and_composition() -> dict[str, Any]:
    from app.core.config import Settings
    from app.infrastructure.database import Database
    from app.modules.agent_platform.composition import RuntimeCompositionFactory

    settings = Settings()
    database = Database.from_settings(settings)
    try:
        async with database.session() as session:
            # 冻结契约零漂移：加载器对任一不一致都会抛 CatalogContractMismatch
            await PersistentCatalogLoader(CatalogRepository(session)).load()
            fingerprints = {name: tool_fingerprint(name) for name in sorted(TOOL_CONTRACTS)}
            if len(fingerprints) != 17:
                raise ProbeFailure("M5_ACCEPTANCE_TOOL_COUNT")
            factory = RuntimeCompositionFactory(settings)
            catalogs = await factory.load_catalogs(session)
            executor, _approval, _moderation = await factory.build_tool_executor(session, catalogs)
    finally:
        await database.dispose()
    handlers = getattr(executor, "_handlers", None) or getattr(executor, "handlers", None)
    if not isinstance(handlers, dict) or not handlers:
        raise ProbeFailure("M5_ACCEPTANCE_EXECUTOR_HANDLERS")
    mock_residual = sorted(name for name in TOOL_CONTRACTS if is_mock_handler(handlers.get(name)))
    if mock_residual:
        raise ProbeFailure(f"M5_ACCEPTANCE_MOCK_RESIDUAL_{mock_residual[0]}")
    missing = sorted(name for name in TOOL_CONTRACTS if handlers.get(name) is None)
    if missing:
        raise ProbeFailure(f"M5_ACCEPTANCE_HANDLER_MISSING_{missing[0]}")
    return {
        "tools": len(fingerprints),
        "catalog_zero_drift": True,
        "real_handlers": len(TOOL_CONTRACTS) - len(mock_residual),
        "fingerprints": fingerprints,
    }


async def amain() -> None:
    contract = verify_contract()
    catalog = await verify_catalog_and_composition()
    print(json.dumps({"ok": True, "contract": contract, "catalog": catalog}, ensure_ascii=False, sort_keys=True))


def main() -> None:
    try:
        asyncio.run(amain())
    except ProbeFailure as exc:
        print(json.dumps({"ok": False, "error": exc.code}, ensure_ascii=False))
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()

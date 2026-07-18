"""136 个 OpenAPI operationId 与实现的总审计（#199）。

- operationId 全局唯一且全部由 FastAPI 实现，方法+路径逐一对应。
- M5 关键 operation 的响应矩阵（429/409/502/504/SSE）不缺失。
- 仅健康检查与登出允许不声明 401（登录/刷新以 401 表达认证失败语义）。
"""

from pathlib import Path

import pytest
import yaml

from app.main import create_app

OPENAPI_PATH = Path(__file__).resolve().parents[2] / "docx" / "deliverables" / "openapi.yaml"
PUBLIC_WITHOUT_401 = {"getLiveness", "getReadiness", "logout"}
M5_RESPONSE_MATRIX: dict[str, set[str]] = {
    "createAgentRun": {"202", "429"},
    "decideAgentToolApproval": {"200", "409"},
    "cancelAgentRun": {"409"},
    "updateToolRuntimeState": {"409"},
    "invokeInternalTool": {"200", "202", "401", "403", "409", "429", "502", "504"},
    "createChatCompletion": {"502", "504"},
    "streamChatCompletion": {"502", "504"},
}
M5_SSE_OPERATIONS = {"streamAgentRun", "streamChatCompletion"}


def _declared():
    spec = yaml.safe_load(OPENAPI_PATH.read_text(encoding="utf-8"))
    declared: dict[str, tuple[str, str, dict]] = {}
    for path, item in (spec.get("paths") or {}).items():
        for method, operation in (item or {}).items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            operation_id = (operation or {}).get("operationId")
            if operation_id:
                declared[operation_id] = (method, path, operation)
    return declared


def _implemented():
    implemented: dict[str, tuple[str, str]] = {}
    for route in create_app().routes:
        operation_id = getattr(route, "operation_id", None)
        if not operation_id:
            continue
        methods = sorted(getattr(route, "methods", None) or ())
        implemented[operation_id] = (methods[0].lower(), getattr(route, "path", ""))
    return implemented


def test_all_operations_unique_implemented_and_matching():
    declared = _declared()
    implemented = _implemented()
    assert len(declared) == 136
    missing = set(declared) - set(implemented)
    assert not missing, f"operations missing from implementation: {sorted(missing)}"
    duplicates = [operation_id for operation_id in declared if list(declared).count(operation_id) > 1]
    assert not duplicates
    mismatched = {
        operation_id: (declared[operation_id][:2], implemented[operation_id])
        for operation_id in declared
        if operation_id in implemented and declared[operation_id][:2] != implemented[operation_id]
    }
    assert not mismatched


def test_m5_response_matrix_is_declared():
    declared = _declared()
    for operation_id, required_codes in M5_RESPONSE_MATRIX.items():
        assert operation_id in declared, operation_id
        codes = set(declared[operation_id][2].get("responses", {}).keys())
        missing = required_codes - codes
        assert not missing, f"{operation_id} missing responses {sorted(missing)}"


def test_sse_operations_declare_event_stream():
    declared = _declared()
    for operation_id in M5_SSE_OPERATIONS:
        operation = declared[operation_id][2]
        content = operation.get("responses", {}).get("200", {}).get("content", {})
        assert "text/event-stream" in content, operation_id


def test_only_documented_public_operations_skip_401():
    declared = _declared()
    without_401 = {
        operation_id
        for operation_id, (_method, _path, operation) in declared.items()
        if "401" not in operation.get("responses", {})
    }
    assert without_401 == PUBLIC_WITHOUT_401


@pytest.mark.parametrize("operation_id", sorted(M5_RESPONSE_MATRIX))
def test_m5_operations_implemented(operation_id):
    assert operation_id in _implemented()

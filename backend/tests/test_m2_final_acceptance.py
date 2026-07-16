import inspect
from pathlib import Path

import yaml

from app.main import create_app
from app.modules.agent_platform import composition


M2_OPERATIONS = {
    "listDepartments", "getDepartment", "listDepartmentContacts",
    "listServiceGuides", "getServiceGuide", "getServiceGuideChecklist",
    "createWorkOrder", "listWorkOrders", "getWorkOrder",
    "listWorkOrderEvents", "transitionWorkOrder", "rateWorkOrder",
    "getElectricityBalance", "createElectricityTopupRequest",
    "queryExternalServiceProgress",
}


def _openapi_operations() -> dict[str, dict[str, object]]:
    path = Path(__file__).parents[2] / "docx" / "deliverables" / "openapi.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    return {
        operation["operationId"]: operation
        for path_item in document["paths"].values()
        for method, operation in path_item.items()
        if method in {"get", "post", "put", "patch", "delete"}
    }


def test_all_m2_operations_are_unique_registered_and_authenticated() -> None:
    operations = _openapi_operations()
    assert M2_OPERATIONS <= operations.keys()
    assert len(operations) == len(set(operations))

    implemented = [
        route.operation_id
        for route in create_app().routes
        if getattr(route, "operation_id", None) in M2_OPERATIONS
    ]
    assert len(implemented) == len(M2_OPERATIONS)
    assert set(implemented) == M2_OPERATIONS
    assert all("401" in operations[operation_id]["responses"] for operation_id in M2_OPERATIONS)


def test_m2_contracts_keep_primary_error_envelopes() -> None:
    operations = _openapi_operations()
    assert "404" in operations["getDepartment"]["responses"]
    assert "404" in operations["getServiceGuide"]["responses"]
    assert "404" in operations["getWorkOrder"]["responses"]
    assert "409" in operations["createWorkOrder"]["responses"]
    assert "409" in operations["transitionWorkOrder"]["responses"]
    assert "422" in operations["queryExternalServiceProgress"]["responses"]
    assert "503" in operations["queryExternalServiceProgress"]["responses"]


def test_all_five_m2_tools_use_real_handlers_in_the_only_runtime_composition() -> None:
    source = inspect.getsource(composition.RuntimeCompositionFactory.build_tool_executor)
    expected = {
        '"service.get_guide": ServiceGuideToolHandler',
        '"work_order.create": WorkOrderCreateToolHandler',
        '"work_order.get": WorkOrderGetToolHandler',
        '"electricity.get_balance": ElectricityBalanceToolHandler',
        '"electricity.create_topup_request": ElectricityTopupToolHandler',
    }
    assert all(marker in source for marker in expected)
    assert not any(f'"{tool}": MockToolHandler' in source for tool in (
        "service.get_guide", "work_order.create", "work_order.get",
        "electricity.get_balance", "electricity.create_topup_request",
    ))

import asyncio
import inspect
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.modules.agent_platform import composition
from app.modules.agent_platform.domain.contracts import ToolInvocationContext, UserContext
from app.modules.agent_platform.tool_gateway.campus_service_adapters import (
    ServiceGuideToolHandler,
    WorkOrderCreateToolHandler,
    WorkOrderGetToolHandler,
)
from app.modules.agent_platform.tool_gateway.catalog import (
    ServiceGuideInput,
    WorkOrderCreateInput,
    WorkOrderGetInput,
)
from app.modules.agent_platform.tool_gateway.errors import (
    ToolApprovalInvalid,
    ToolArgumentInvalid,
    ToolDependencyUnavailable,
    ToolForbidden,
)
from app.modules.campus_service.guides import (
    GuideApplicabilityDTO,
    GuideDetailDTO,
    GuideMaterialRawDTO,
    GuideNotFound,
    GuidePageDTO,
    GuideStepDTO,
    GuideSummaryDTO,
)
from app.modules.campus_service.reference import DepartmentDTO, GuideCategoryDTO
from app.modules.campus_service.work_order_errors import CampusNotFound, WorkOrderNotFound
from app.modules.campus_service.work_order_schemas import WorkOrderData, WorkOrderEventData
from app.modules.campus_service.work_orders import WorkOrderMutationResult, WorkOrderToolView

USER_ID = UUID("90000000-0000-4000-8000-000000000001")
ROOM_ID = UUID("21000000-0000-4000-8000-000000000001")
ORDER_ID = UUID("70000000-0000-4000-8000-000000000001")
RUN_ID = UUID("30000000-0000-4000-8000-000000000001")
STEP_ID = UUID("30000000-0000-4000-8000-000000000002")
APPROVAL_ID = UUID("30000000-0000-4000-8000-000000000003")
NOW = datetime(2026, 7, 16, 8, tzinfo=UTC)


def _invocation(*, approved=True, key="tool-key"):
    user = UserContext(
        user_id=USER_ID, username="student01", roles=("student",),
        permissions=("service:read", "work_order:create", "work_order:read"),
        request_id="tool-request-1", campus_id="main", room_ids=(ROOM_ID,),
    )
    return ToolInvocationContext(
        user=user, agent_run_id=RUN_ID, step_id=STEP_ID,
        idempotency_key=key, arguments_hash="a" * 64,
        approval_id=APPROVAL_ID if approved else None,
        approval_verified=approved,
    )


def _work_order_data():
    return WorkOrderData(
        id=ORDER_ID, order_no="WO-DEMO-0001", created_by=USER_ID,
        campus_code="main", dormitory_area="演示宿舍区", building="A", room="101",
        fault_category="plumbing", description="宿舍水龙头持续漏水，需要安排检修。",
        preferred_start_at=NOW, preferred_end_at=NOW.replace(hour=10), status="submitted",
        assigned_to=None, assigned_department_id=None, rejection_reason=None,
        completion_note=None, rating=None, version=1, submitted_at=NOW,
        accepted_at=None, processing_at=None, completed_at=None, cancelled_at=None,
        rejected_at=None, created_at=NOW, updated_at=NOW,
    )


def test_guide_adapter_calls_real_search_with_frozen_contract_filters() -> None:
    service = MagicMock()
    summary = GuideSummaryDTO(
        id=UUID(int=9), code="student_card", title="学生证补办", summary="补办指南",
        category=GuideCategoryDTO(UUID(int=10), "student", "学生事务", 1),
        department=DepartmentDTO(UUID(int=11), "student_affairs", "学生事务中心", None),
        location="行政楼", service_hours=None, valid_until=None, updated_at=NOW, version=1,
    )
    service.search = AsyncMock(return_value=GuidePageDTO((summary,), 1, 10, 1, 1))
    service.get_detail = AsyncMock(side_effect=GuideNotFound())
    output = asyncio.run(ServiceGuideToolHandler(service)(
        _invocation(), ServiceGuideInput(query="学生证", campus_id="main", student_type="undergraduate")
    ))
    assert output.items[0].guide_id == summary.id and output.items[0].steps == ()
    assert output.items[0].department == "学生事务中心"
    assert service.search.await_args.kwargs == {
        "page": 1, "page_size": 10, "q": "学生证",
        "campus_code": "main", "student_type": "undergraduate",
    }


def test_guide_adapter_enriches_top_hits_with_materials_and_steps() -> None:
    service = MagicMock()
    summary = GuideSummaryDTO(
        id=UUID(int=9), code="enrollment_certificate", title="在读证明办理", summary="在读证明办理指南",
        category=GuideCategoryDTO(UUID(int=10), "student", "学生事务", 1),
        department=DepartmentDTO(UUID(int=11), "student_affairs", "学生事务中心", None),
        location="行政楼101", service_hours="工作日 9:00-17:00", valid_until=None,
        updated_at=NOW, version=1,
    )
    service.search = AsyncMock(return_value=GuidePageDTO((summary,), 1, 10, 1, 1))
    detail = GuideDetailDTO(
        summary=summary,
        source_url=None,
        applicability=GuideApplicabilityDTO(
            campus_code="jiangan", student_type="undergraduate", applicable=True, notes=None,
        ),
        materials=(
            GuideMaterialRawDTO(
                id=UUID(int=20), name="学生证", description=None,
                required=True, copies=1, condition={}, sort_order=1,
            ),
            GuideMaterialRawDTO(
                id=UUID(int=21), name="身份证复印件", description=None,
                required=True, copies=2, condition={}, sort_order=2,
            ),
        ),
        steps=(
            GuideStepDTO(step_no=1, title="提交申请", description="到学生事务中心提交", location=None, estimated_minutes=None),
            GuideStepDTO(step_no=2, title="领取证明", description="当场领取", location=None, estimated_minutes=None),
        ),
        contacts=(),
    )
    service.get_detail = AsyncMock(return_value=detail)
    output = asyncio.run(ServiceGuideToolHandler(service)(
        _invocation(), ServiceGuideInput(query="在读证明", campus_id="jiangan")
    ))
    item = output.items[0]
    assert item.department == "学生事务中心"
    assert item.service_hours == "工作日 9:00-17:00"
    assert item.materials == ("学生证", "身份证复印件（2份）")
    assert item.steps == ("1. 提交申请：到学生事务中心提交", "2. 领取证明：当场领取")


def test_guide_adapter_falls_back_to_enabled_campuses_for_detail() -> None:
    service = MagicMock()
    summary = GuideSummaryDTO(
        id=UUID(int=9), code="enrollment_certificate", title="在读证明办理", summary="指南",
        category=GuideCategoryDTO(UUID(int=10), "student", "学生事务", 1),
        department=DepartmentDTO(UUID(int=11), "student_affairs", "学生事务中心", None),
        location=None, service_hours=None, valid_until=None, updated_at=NOW, version=1,
    )
    service.search = AsyncMock(return_value=GuidePageDTO((summary,), 1, 10, 1, 1))
    service.get_detail = AsyncMock(side_effect=[GuideNotFound(), GuideNotFound(), MagicMock(
        materials=(), steps=(),
    )])
    campuses = MagicMock()
    campuses.list_enabled_campuses = AsyncMock(
        return_value=(SimpleNamespace(code="jiangan"), SimpleNamespace(code="wangjiang"))
    )
    handler = ServiceGuideToolHandler(service, campuses=campuses)
    output = asyncio.run(handler(_invocation(), ServiceGuideInput(query="在读证明")))
    codes = [call.kwargs["campus_code"] for call in service.get_detail.await_args_list]
    assert codes == ["main", "jiangan", "wangjiang"]
    assert output.items[0].guide_id == summary.id


def test_create_adapter_maps_room_fault_time_and_trusted_facts() -> None:
    service = MagicMock()
    service.create_from_room_in_transaction = AsyncMock(return_value=WorkOrderMutationResult(
        201, "tool-request-1", {"data": {"id": str(ORDER_ID), "status": "submitted", "created_at": NOW.isoformat()}}
    ))
    handler = WorkOrderCreateToolHandler(service, now=lambda: NOW)
    output = asyncio.run(handler(
        _invocation(),
        WorkOrderCreateInput(
            room_id=ROOM_ID, fault_type="water",
            description="宿舍水龙头持续漏水，需要安排检修。",
            available_time="2026-07-18T09:00:00+08:00/2026-07-18T11:00:00+08:00",
        ),
    ))
    assert output.work_order_id == ORDER_ID and output.status == "submitted"
    call = service.create_from_room_in_transaction.await_args.kwargs
    assert call["fault_category"] == "plumbing"
    assert call["room_ids"] == (ROOM_ID,)
    assert call["preferred_start_at"] == datetime(2026, 7, 18, 1, tzinfo=UTC)
    assert call["approval_verified"] is True and call["approval_id"] == APPROVAL_ID


def test_create_adapter_uses_default_window_and_rejects_lossy_inputs() -> None:
    service = MagicMock(); service.create_from_room_in_transaction = AsyncMock(return_value=WorkOrderMutationResult(
        201, "tool-request-1", {"data": {"id": str(ORDER_ID), "status": "submitted", "created_at": NOW.isoformat()}}
    ))
    handler = WorkOrderCreateToolHandler(service, now=lambda: NOW)
    valid = WorkOrderCreateInput(
        room_id=ROOM_ID, fault_type="electricity",
        description="宿舍插座无法供电，需要安排工作人员检修。",
    )
    asyncio.run(handler(_invocation(), valid))
    call = service.create_from_room_in_transaction.await_args.kwargs
    assert call["preferred_start_at"] == NOW + timedelta(days=1)
    assert call["preferred_end_at"] == NOW + timedelta(days=1, hours=2)
    invalid = [
        valid.model_copy(update={"fault_type": "air_conditioning"}),
        valid.model_copy(update={"description": "x" * 1001}),
        valid.model_copy(update={"attachments": ("object-key",)}),
        valid.model_copy(update={"available_time": "tomorrow afternoon"}),
        valid.model_copy(update={"available_time": "2026-07-18T11:00:00Z/2026-07-18T09:00:00Z"}),
    ]
    for payload in invalid:
        with pytest.raises(ToolArgumentInvalid):
            asyncio.run(handler(_invocation(), payload))
    with pytest.raises(ToolApprovalInvalid):
        asyncio.run(handler(_invocation(approved=False), valid))


def test_create_adapter_maps_natural_address_to_location_command() -> None:
    service = MagicMock()
    service.create_from_location_in_transaction = AsyncMock(return_value=WorkOrderMutationResult(
        201, "tool-request-1", {"data": {"id": str(ORDER_ID), "status": "submitted", "created_at": NOW.isoformat()}}
    ))
    handler = WorkOrderCreateToolHandler(service, now=lambda: NOW)
    output = asyncio.run(handler(
        _invocation(),
        WorkOrderCreateInput(
            campus="江安校区", dormitory_area="西苑", building="6舍3栋", room="601B",
            fault_type="electric", description="空调无法制冷，需要维修。",
        ),
    ))
    assert output.work_order_id == ORDER_ID and output.status == "submitted"
    call = service.create_from_location_in_transaction.await_args.kwargs
    assert call["campus"] == "江安校区"
    assert call["dormitory_area"] == "西苑"
    assert call["building"] == "6舍3栋" and call["room"] == "601B"
    assert call["fault_category"] == "electric"
    assert call["approval_verified"] is True and call["approval_id"] == APPROVAL_ID


def test_create_adapter_maps_unknown_campus_to_argument_error() -> None:
    service = MagicMock()
    service.create_from_location_in_transaction = AsyncMock(side_effect=CampusNotFound())
    handler = WorkOrderCreateToolHandler(service, now=lambda: NOW)
    with pytest.raises(ToolArgumentInvalid):
        asyncio.run(handler(
            _invocation(),
            WorkOrderCreateInput(
                campus="不存在校区", dormitory_area="西苑", building="6舍3栋", room="601B",
                fault_type="electric", description="空调无法制冷，需要维修。",
            ),
        ))


def test_create_input_requires_room_id_or_full_location() -> None:
    base = {"fault_type": "electric", "description": "空调无法制冷，需要维修。"}
    with pytest.raises(ValidationError):
        WorkOrderCreateInput.model_validate(base)
    with pytest.raises(ValidationError):
        WorkOrderCreateInput.model_validate({**base, "campus": "江安校区", "dormitory_area": "西苑"})
    with pytest.raises(ValidationError):
        WorkOrderCreateInput.model_validate({**base, "room": "601B"})
    parsed = WorkOrderCreateInput.model_validate({
        **base, "campus": "江安校区", "dormitory_area": "西苑",
        "building": "6舍3栋", "room": "601B",
    })
    assert parsed.room_id is None and parsed.campus == "江安校区"


def test_get_adapter_uses_real_view_and_never_exposes_event_reason() -> None:
    service = MagicMock()
    event = WorkOrderEventData(
        id=UUID(int=12), sequence_no=1, event_type="submitted", from_status=None,
        to_status="submitted", actor_user_id=USER_ID, actor_role="student",
        reason="包含敏感原因正文", created_at=NOW,
    )
    service.get_tool_view = AsyncMock(return_value=WorkOrderToolView(
        work_order=_work_order_data(), room_id=ROOM_ID, events=(event,)
    ))
    output = asyncio.run(WorkOrderGetToolHandler(service)(
        _invocation(), WorkOrderGetInput(work_order_id=ORDER_ID)
    ))
    assert output.room_id == ROOM_ID and output.fault_type == "plumbing"
    assert output.events[0].summary == "工单已提交"
    assert "敏感" not in str(output)


def test_get_adapter_maps_owner_and_location_failures_safely() -> None:
    service = MagicMock(); service.get_tool_view = AsyncMock(side_effect=WorkOrderNotFound())
    with pytest.raises(ToolForbidden):
        asyncio.run(WorkOrderGetToolHandler(service)(
            _invocation(), WorkOrderGetInput(work_order_id=ORDER_ID)
        ))
    service.get_tool_view = AsyncMock(return_value=WorkOrderToolView(
        work_order=_work_order_data(), room_id=None, events=()
    ))
    with pytest.raises(ToolDependencyUnavailable):
        asyncio.run(WorkOrderGetToolHandler(service)(
            _invocation(), WorkOrderGetInput(work_order_id=ORDER_ID)
        ))


def test_runtime_composition_overwrites_all_five_m2_mock_handlers() -> None:
    source = inspect.getsource(composition.RuntimeCompositionFactory.build_tool_executor)
    expected = {
        '"service.get_guide": ServiceGuideToolHandler',
        '"work_order.create": WorkOrderCreateToolHandler',
        '"work_order.get": WorkOrderGetToolHandler',
        '"electricity.get_balance": ElectricityBalanceToolHandler',
        '"electricity.create_topup_request": ElectricityTopupToolHandler',
    }
    assert all(marker in source for marker in expected)

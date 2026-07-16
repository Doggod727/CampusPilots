from datetime import UTC, datetime

import pytest

from app.modules.campus_service.work_order_errors import WorkOrderIllegalTransition
from app.modules.campus_service.work_order_state import WorkOrderStateMachine

NOW = datetime(2026, 7, 16, 9, tzinfo=UTC)


@pytest.mark.parametrize(
    ("source", "target", "timestamp_field"),
    [
        ("submitted", "accepted", "accepted_at"),
        ("submitted", "rejected", "rejected_at"),
        ("submitted", "cancelled", "cancelled_at"),
        ("accepted", "processing", "processing_at"),
        ("processing", "completed", "completed_at"),
    ],
)
def test_state_machine_accepts_only_documented_matrix(source, target, timestamp_field) -> None:
    effects = WorkOrderStateMachine.apply(
        current_status=source,
        target_status=target,
        reason="状态流转原因",
        completion_note="维修完成" if target == "completed" else None,
        now=NOW,
    )
    assert effects.event_type == target
    assert effects.updates["status"] == target
    assert effects.updates[timestamp_field] == NOW


@pytest.mark.parametrize(
    ("source", "target"),
    [("submitted", "completed"), ("accepted", "cancelled"), ("completed", "processing")],
)
def test_state_machine_rejects_skips_rollbacks_and_terminal_changes(source, target) -> None:
    with pytest.raises(WorkOrderIllegalTransition):
        WorkOrderStateMachine.apply(
            current_status=source,
            target_status=target,
            reason="非法流转",
            completion_note="完成",
            now=NOW,
        )


def test_completed_requires_non_blank_completion_note() -> None:
    with pytest.raises(WorkOrderIllegalTransition):
        WorkOrderStateMachine.apply(
            current_status="processing",
            target_status="completed",
            reason="处理完成",
            completion_note="  ",
            now=NOW,
        )

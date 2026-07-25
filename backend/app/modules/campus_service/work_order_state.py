from dataclasses import dataclass
from datetime import datetime

from app.modules.campus_service.work_order_errors import WorkOrderIllegalTransition


@dataclass(frozen=True)
class TransitionEffects:
    event_type: str
    updates: dict[str, object]


class WorkOrderStateMachine:
    """Pure transition matrix; authorization and persistence stay outside."""

    @staticmethod
    def apply(
        *,
        current_status: str,
        target_status: str,
        reason: str,
        completion_note: str | None,
        now: datetime,
    ) -> TransitionEffects:
        if (current_status, target_status) not in {
            ("submitted", "accepted"),
            ("submitted", "rejected"),
            ("submitted", "cancelled"),
            ("accepted", "processing"),
            ("processing", "completed"),
        }:
            raise WorkOrderIllegalTransition()
        updates: dict[str, object] = {"status": target_status, "updated_at": now}
        if target_status == "accepted":
            updates["accepted_at"] = now
        elif target_status == "processing":
            updates["processing_at"] = now
        elif target_status == "completed":
            if completion_note is None or not completion_note.strip():
                raise WorkOrderIllegalTransition()
            updates.update(completed_at=now, completion_note=completion_note.strip())
        elif target_status == "cancelled":
            updates["cancelled_at"] = now
        elif target_status == "rejected":
            updates.update(rejected_at=now, rejection_reason=reason.strip())
        return TransitionEffects(event_type=target_status, updates=updates)

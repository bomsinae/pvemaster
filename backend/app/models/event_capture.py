from datetime import UTC, datetime

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

from app.models.operation import Operation, OperationEvent
from app.models.provisioning import ProvisioningRequest


def _status_message(resource: str, status: str) -> str:
    labels = {
        "QUEUED": "Queued for worker execution",
        "RUNNING": "Worker execution started or resumed",
        "CANCEL_REQUESTED": "Cancellation was requested",
        "CANCELLED": "Operation was cancelled before execution",
        "SUCCEEDED": "Operation completed successfully",
        "FAILED": "Operation failed",
        "TIMEOUT": "Operation exceeded its polling deadline",
        "NEEDS_ATTENTION": "Manual operator review is required",
        "MANUAL_REVIEW": "Manual operator review is required",
    }
    return f"{resource}: {labels.get(status, 'Status changed')}"


@event.listens_for(Session, "before_flush")
def capture_operation_events(
    session: Session,
    _flush_context: object,
    _instances: object,
) -> None:
    occurred_at = datetime.now(UTC)
    for item in list(session.new):
        if isinstance(item, (Operation, ProvisioningRequest)):
            pending = session.info.setdefault("operation_center_created", [])
            pending.append((item, occurred_at))

    for item in list(session.dirty):
        if isinstance(item, Operation):
            state = inspect(item)
            if not state.attrs.status.history.has_changes():
                continue
            session.add(
                OperationEvent(
                    operation_id=item.id,
                    event_type="STATUS_CHANGED",
                    status=item.status,
                    message=_status_message("Operation", item.status),
                    occurred_at=occurred_at,
                    details={
                        "error_code": item.error_code,
                        "retryable": item.retryable,
                    },
                )
            )
        elif isinstance(item, ProvisioningRequest):
            state = inspect(item)
            status_changed = state.attrs.status.history.has_changes()
            step_changed = state.attrs.current_step.history.has_changes()
            if not status_changed and not step_changed:
                continue
            event_type = "STATUS_CHANGED" if status_changed else "STEP_CHANGED"
            message = (
                _status_message("Provisioning", item.status)
                if status_changed
                else "Provisioning advanced to the next safe step"
            )
            session.add(
                OperationEvent(
                    provisioning_request_id=item.id,
                    event_type=event_type,
                    status=item.status,
                    step=item.current_step,
                    message=message,
                    occurred_at=occurred_at,
                    details={"error_code": item.error_code},
                )
            )


@event.listens_for(Session, "after_flush_postexec")
def capture_created_operation_events(session: Session, _flush_context: object) -> None:
    pending = session.info.pop("operation_center_created", [])
    for item, occurred_at in pending:
        if isinstance(item, Operation):
            session.add(
                OperationEvent(
                    operation_id=item.id,
                    event_type="CREATED",
                    status=item.status,
                    message=_status_message("Operation", item.status),
                    actor_user_id=item.requested_by_id,
                    occurred_at=occurred_at,
                    details={"operation_type": item.operation_type},
                )
            )
        elif isinstance(item, ProvisioningRequest):
            session.add(
                OperationEvent(
                    provisioning_request_id=item.id,
                    event_type="CREATED",
                    status=item.status,
                    step=item.current_step,
                    message=_status_message("Provisioning", item.status),
                    actor_user_id=item.requested_by_id,
                    occurred_at=occurred_at,
                    details={},
                )
            )

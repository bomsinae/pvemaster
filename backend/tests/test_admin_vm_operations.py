from datetime import UTC, datetime
from uuid import uuid4

from app.models.auth import UserRole
from app.models.operation import AdminVmAction, Operation, Workload
from app.schemas.operation import VmSpecUpdateRequest
from app.security.access import Principal
from app.services.operations import OperationService


class RecordingSession:
    def __init__(self, workload: Workload) -> None:
        self.results: list[object | None] = [workload, None, None, None]
        self.events: list[str] = []
        self.operation: Operation | None = None

    async def scalar(self, _query: object) -> object | None:
        return self.results.pop(0)

    def add(self, value: object) -> None:
        self.events.append(f"add:{type(value).__name__}")
        if isinstance(value, Operation):
            self.operation = value

    async def flush(self) -> None:
        self.events.append("flush")

    async def commit(self) -> None:
        self.events.append("commit")
        if self.operation is not None and self.operation.requested_at is None:
            self.operation.requested_at = datetime.now(UTC)

    async def rollback(self) -> None:
        self.events.append("rollback")


async def test_admin_vm_operation_is_flushed_before_audit_reference(settings: object) -> None:
    workload = Workload(
        id=uuid4(),
        cluster_id=uuid4(),
        vmid=121,
        node="pve-a",
        kind="QEMU",
        name="mongodb1",
        power_state="STOPPED",
        cpu_cores=2,
        memory_bytes=4 * 1024**3,
        disk_bytes=16 * 1024**3,
        is_template=False,
        is_present=True,
        observed_at=datetime.now(UTC),
        version=3,
    )
    session = RecordingSession(workload)
    principal = Principal(
        user_id=uuid4(),
        email="admin@example.test",
        role=UserRole.SUPER_ADMIN,
        session_epoch=0,
    )
    service = OperationService(
        session=session,  # type: ignore[arg-type]
        settings=settings,  # type: ignore[arg-type]
        principal=principal,
        publisher=lambda _operation_id, _task_id: None,
        request_id=str(uuid4()),
        source_ip="127.0.0.1",
    )

    await service.request_admin_action(
        vm_id=workload.id,
        action=AdminVmAction.UPDATE_SPEC,
        idempotency_key="spec-change-key",
        payload=VmSpecUpdateRequest(
            cpu_cores=4,
            memory_gib=4,
            version=3,
        ),
    )

    assert session.events[:3] == ["add:Operation", "flush", "add:AuditLog"]
    assert "rollback" not in session.events

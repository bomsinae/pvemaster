import hmac
import json
import logging
from collections.abc import Callable
from hashlib import sha256
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError
from app.models.auth import UserRole
from app.models.operation import (
    AdminVmAction,
    Operation,
    OperationStatus,
    PowerAction,
    PveTask,
    Workload,
)
from app.schemas.operation import JobResponse, VmDeleteRequest, VmSpecUpdateRequest
from app.security.access import Principal, require_service_role
from app.services.audit import add_audit_event

OperationPublisher = Callable[[UUID, str], None]
logger = logging.getLogger(__name__)


class OperationService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        settings: Settings,
        principal: Principal,
        publisher: OperationPublisher,
        request_id: str,
        source_ip: str,
    ) -> None:
        self._session = session
        self._settings = settings
        self._principal = principal
        self._publisher = publisher
        self._request_id = request_id
        self._source_ip = source_ip

    async def request_power_action(
        self,
        *,
        workload_id: UUID,
        action: PowerAction,
        idempotency_key: str,
        reason: str | None,
    ) -> tuple[JobResponse, bool]:
        require_service_role(self._principal, UserRole.SUPER_ADMIN, UserRole.OPERATOR)
        workload = await self._session.scalar(
            select(Workload).where(
                Workload.id == workload_id,
                Workload.kind.in_(["QEMU", "LXC"]),
                Workload.is_present.is_(True),
                Workload.is_template.is_(False),
            )
        )
        if workload is None:
            raise AppError(
                status_code=404,
                code="VM_NOT_FOUND",
                message="The workload was not found.",
            )
        if workload.kind == "LXC" and action is PowerAction.RESET:
            raise AppError(
                status_code=409,
                code="POWER_ACTION_UNSUPPORTED",
                message="Reset is not supported for containers.",
            )

        key_hash = self._key_hash(idempotency_key)
        fingerprint = sha256(f"{workload_id}:{action.value}".encode()).digest()
        existing = await self._session.scalar(
            select(Operation).where(
                Operation.requested_by_id == self._principal.user_id,
                Operation.idempotency_key_hash == key_hash,
            )
        )
        if existing is not None:
            if not hmac.compare_digest(existing.request_fingerprint, fingerprint):
                raise AppError(
                    status_code=409,
                    code="IDEMPOTENCY_KEY_REUSED",
                    message="The idempotency key was already used for another request.",
                )
            return await self._response(existing), False

        conflict = await self._session.scalar(
            select(Operation).where(
                Operation.workload_id == workload_id,
                Operation.status.in_([OperationStatus.QUEUED.value, OperationStatus.RUNNING.value]),
            )
        )
        if conflict is not None:
            raise AppError(
                status_code=409,
                code="OPERATION_CONFLICT",
                message="Another power operation is already running for this workload.",
                details={"job_id": str(conflict.id)},
            )

        operation_id = uuid4()
        celery_task_id = str(uuid4())
        operation = Operation(
            id=operation_id,
            operation_type=f"POWER_{action.value.upper()}",
            action=action.value,
            status=OperationStatus.QUEUED.value,
            requested_by_id=self._principal.user_id,
            source_ip=self._source_ip,
            organization_id=workload.organization_id,
            cluster_id=workload.cluster_id,
            workload_id=workload.id,
            idempotency_key_hash=key_hash,
            request_fingerprint=fingerprint,
            celery_task_id=celery_task_id,
            result={
                "action_mode": self._action_mode(action),
                "reason_recorded": reason is not None,
                "workload_kind": workload.kind,
            },
            attempt_count=0,
            version=1,
        )
        self._session.add(operation)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise AppError(
                status_code=409,
                code="OPERATION_CONFLICT",
                message="A duplicate or conflicting operation already exists.",
            ) from exc
        add_audit_event(
            self._session,
            action=f"VM_POWER_{action.value.upper()}",
            outcome="ATTEMPTED",
            request_id=self._request_id,
            actor_user_id=self._principal.user_id,
            actor_role=self._principal.role,
            organization_id=workload.organization_id,
            workload_id=workload.id,
            operation_id=operation.id,
            source_ip=self._source_ip,
            target_type="workload",
            target_id=workload.id,
            details={
                "action_mode": self._action_mode(action),
                "workload_kind": workload.kind,
            },
        )
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            logger.exception(
                "Power operation database conflict",
                extra={"operation_id": str(operation.id)},
            )
            raise AppError(
                status_code=409,
                code="OPERATION_CONFLICT",
                message="A duplicate or conflicting operation already exists.",
            ) from exc

        try:
            self._publisher(operation.id, celery_task_id)
        except Exception:
            logger.exception(
                "Power operation enqueue failed; worker recovery will retry",
                extra={"operation_id": str(operation.id)},
            )
        return await self._response(operation), True

    async def request_admin_action(
        self,
        *,
        vm_id: UUID,
        action: AdminVmAction,
        idempotency_key: str,
        payload: VmSpecUpdateRequest | VmDeleteRequest,
    ) -> tuple[JobResponse, bool]:
        require_service_role(self._principal, UserRole.SUPER_ADMIN)
        workload = await self._session.scalar(
            select(Workload).where(
                Workload.id == vm_id,
                Workload.is_present.is_(True),
                Workload.is_template.is_(False),
            )
        )
        if workload is None:
            raise AppError(404, "VM_NOT_FOUND", "The virtual machine was not found.")

        if isinstance(payload, VmDeleteRequest):
            expected = workload.name or str(workload.vmid)
            if not hmac.compare_digest(payload.confirmation, expected):
                raise AppError(422, "VM_DELETE_CONFIRMATION_MISMATCH", "VM confirmation failed.")
            if workload.organization_id is not None:
                raise AppError(409, "VM_ASSIGNED", "Unassign the VM before deleting it.")
            if workload.power_state.upper() != "STOPPED":
                raise AppError(409, "VM_NOT_STOPPED", "Stop the VM before deleting it.")
            requested: dict[str, object] = {"reason_recorded": payload.reason is not None}
        else:
            if payload.version != workload.version:
                raise AppError(409, "WORKLOAD_VERSION_CONFLICT", "The VM inventory changed.")
            requested_disk = payload.disk_gib * 1024**3 if payload.disk_gib else None
            if requested_disk is not None and workload.disk_bytes is not None:
                if requested_disk < workload.disk_bytes:
                    raise AppError(422, "DISK_SHRINK_FORBIDDEN", "VM disks cannot be shrunk.")
            requested = {
                "cpu_cores": payload.cpu_cores,
                "memory_bytes": payload.memory_gib * 1024**3,
                "disk_bytes": requested_disk,
                "reason_recorded": payload.reason is not None,
            }

        key_hash = self._key_hash(idempotency_key)
        fingerprint = sha256(
            json.dumps(
                {"vm_id": str(vm_id), "action": action.value, **requested}, sort_keys=True
            ).encode()
        ).digest()
        existing = await self._session.scalar(
            select(Operation).where(
                Operation.requested_by_id == self._principal.user_id,
                Operation.idempotency_key_hash == key_hash,
            )
        )
        if existing is not None:
            if not hmac.compare_digest(existing.request_fingerprint, fingerprint):
                raise AppError(409, "IDEMPOTENCY_KEY_REUSED", "The idempotency key was reused.")
            return await self._response(existing), False
        conflict = await self._session.scalar(
            select(Operation.id).where(
                Operation.workload_id == workload.id,
                Operation.status.in_([OperationStatus.QUEUED.value, OperationStatus.RUNNING.value]),
            )
        )
        if conflict is not None:
            raise AppError(409, "OPERATION_CONFLICT", "Another VM operation is running.")

        operation = Operation(
            id=uuid4(),
            operation_type="VM_SPEC_UPDATE" if action is AdminVmAction.UPDATE_SPEC else "VM_DELETE",
            action=action.value,
            status=OperationStatus.QUEUED.value,
            requested_by_id=self._principal.user_id,
            source_ip=self._source_ip,
            organization_id=workload.organization_id,
            cluster_id=workload.cluster_id,
            workload_id=workload.id,
            idempotency_key_hash=key_hash,
            request_fingerprint=fingerprint,
            celery_task_id=str(uuid4()),
            result=requested,
            attempt_count=0,
            version=1,
        )
        self._session.add(operation)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise AppError(
                409,
                "OPERATION_CONFLICT",
                "A duplicate or conflicting operation already exists.",
            ) from exc
        add_audit_event(
            self._session,
            action=operation.operation_type,
            outcome="ATTEMPTED",
            request_id=self._request_id,
            actor_user_id=self._principal.user_id,
            actor_role=self._principal.role,
            organization_id=workload.organization_id,
            workload_id=workload.id,
            operation_id=operation.id,
            source_ip=self._source_ip,
            target_type="vm",
            target_id=workload.id,
            after=requested,
        )
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise AppError(409, "OPERATION_CONFLICT", "A conflicting operation exists.") from exc
        try:
            self._publisher(operation.id, operation.celery_task_id)
        except Exception:
            logger.exception(
                "VM operation enqueue failed", extra={"operation_id": str(operation.id)}
            )
        return await self._response(operation), True

    async def get_job(self, job_id: UUID) -> JobResponse:
        require_service_role(self._principal, UserRole.SUPER_ADMIN, UserRole.OPERATOR)
        operation = await self._session.get(Operation, job_id)
        if operation is None:
            raise AppError(status_code=404, code="JOB_NOT_FOUND", message="The job was not found.")
        return await self._response(operation)

    async def _response(self, operation: Operation) -> JobResponse:
        pve_task = await self._session.scalar(
            select(PveTask)
            .where(PveTask.operation_id == operation.id)
            .order_by(PveTask.submitted_at.desc())
        )
        result = dict(operation.result)
        return JobResponse(
            id=operation.id,
            job_id=operation.id,
            vm_id=operation.workload_id,
            workload_id=operation.workload_id,
            organization_id=operation.organization_id,
            action=(
                PowerAction(operation.action)
                if operation.action in {item.value for item in PowerAction}
                else AdminVmAction(operation.action)
                if operation.action in {item.value for item in AdminVmAction}
                else "backup"
            ),
            action_mode=str(
                result.get(
                    "action_mode",
                    self._action_mode(PowerAction(operation.action))
                    if operation.action in {item.value for item in PowerAction}
                    else "DESTRUCTIVE"
                    if operation.action == AdminVmAction.DELETE.value
                    else "CONFIGURATION"
                    if operation.action in {item.value for item in AdminVmAction}
                    else "BACKUP",
                )
            ),
            status=OperationStatus(operation.status),
            result=result,
            error_code=operation.error_code,
            error_summary=operation.error_summary,
            retryable=operation.retryable,
            pve_upid=pve_task.upid if pve_task is not None else None,
            pve_exit_status=pve_task.pve_exit_status if pve_task is not None else None,
            requested_at=operation.requested_at,
            started_at=operation.started_at,
            finished_at=operation.finished_at,
        )

    def _key_hash(self, key: str) -> bytes:
        secret = self._settings.app_secret_key.get_secret_value().encode()
        return hmac.new(secret, key.encode(), sha256).digest()

    @staticmethod
    def _action_mode(action: PowerAction) -> str:
        if action is PowerAction.SHUTDOWN:
            return "GRACEFUL"
        if action is PowerAction.STOP:
            return "FORCED"
        return "STANDARD"

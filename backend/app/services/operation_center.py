import hashlib
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast
from uuid import UUID, uuid4

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.config import Settings
from app.core.errors import AppError
from app.models.auth import AuditLog, Organization, User, UserRole
from app.models.backup import BackupRun, RestoreRun
from app.models.cluster import Cluster
from app.models.operation import (
    Operation,
    OperationAssignment,
    OperationEvent,
    OperationStatus,
    PveTask,
    Workload,
)
from app.models.provisioning import (
    ProvisioningRequest,
    ProvisioningStatus,
    ProvisioningStep,
    ProvisioningStepStatus,
)
from app.schemas.operation_center import (
    OperationAssignmentResponse,
    OperationCenterDetailResponse,
    OperationCenterItemResponse,
    OperationEventResponse,
    OperationStepResponse,
    OperationTaskResponse,
)
from app.security.access import Principal, require_service_role
from app.services.audit import add_audit_event
from app.services.outbox import BACKUP_EVENT, POWER_EVENT, RESTORE_EVENT, add_operation_event
from app.services.provisioning import PROVISIONING_STEPS

OperationPublisher = Callable[[UUID, str], None]
ResourceType = Literal["OPERATION", "PROVISIONING"]
logger = logging.getLogger(__name__)


class OperationCenterService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        settings: Settings,
        principal: Principal,
        publishers: dict[str, OperationPublisher],
        provisioning_publisher: OperationPublisher,
        request_id: str,
        source_ip: str,
    ) -> None:
        self._session = session
        self._settings = settings
        self._principal = principal
        self._publishers = publishers
        self._provisioning_publisher = provisioning_publisher
        self._request_id = request_id
        self._source_ip = source_ip
        require_service_role(principal, UserRole.SUPER_ADMIN, UserRole.OPERATOR)

    async def list_operations(
        self,
        *,
        status: str | None,
        operation_type: str | None,
        cluster_id: UUID | None,
        organization_id: UUID | None,
        actor_id: UUID | None,
        error_code: str | None,
        requested_from: datetime | None,
        requested_to: datetime | None,
        limit: int,
        offset: int,
    ) -> tuple[list[OperationCenterItemResponse], int]:
        operations = await self._operation_rows(
            status=status,
            operation_type=operation_type,
            cluster_id=cluster_id,
            organization_id=organization_id,
            actor_id=actor_id,
            error_code=error_code,
            requested_from=requested_from,
            requested_to=requested_to,
        )
        provisioning = await self._provisioning_rows(
            status=status,
            operation_type=operation_type,
            cluster_id=cluster_id,
            organization_id=organization_id,
            actor_id=actor_id,
            error_code=error_code,
            requested_from=requested_from,
            requested_to=requested_to,
        )
        items = [*operations, *provisioning]
        items.sort(key=lambda item: (item.requested_at, str(item.id)), reverse=True)
        return items[offset : offset + limit], len(items)

    async def get_detail(self, operation_id: UUID) -> OperationCenterDetailResponse:
        item = await self.get_item(operation_id)
        event_query = select(OperationEvent).order_by(
            OperationEvent.occurred_at,
            OperationEvent.id,
        )
        if item.resource_type == "OPERATION":
            event_query = event_query.where(OperationEvent.operation_id == operation_id)
        else:
            event_query = event_query.where(OperationEvent.provisioning_request_id == operation_id)
        events = (await self._session.scalars(event_query)).all()
        pve_tasks: list[OperationTaskResponse] = []
        provisioning_steps: list[OperationStepResponse] = []
        related_audit_count = 0
        related_backup_ids: list[UUID] = []
        if item.resource_type == "OPERATION":
            tasks = (
                await self._session.scalars(
                    select(PveTask)
                    .where(PveTask.operation_id == operation_id)
                    .order_by(PveTask.submitted_at)
                )
            ).all()
            pve_tasks = [
                OperationTaskResponse(
                    step_name=task.step_name,
                    status=task.status,
                    upid_reference=self._safe_upid_reference(task.upid),
                    pve_exit_status=task.pve_exit_status,
                    poll_attempts=task.poll_attempts,
                    error_code=task.error_code,
                    submitted_at=task.submitted_at,
                    last_polled_at=task.last_polled_at,
                    completed_at=task.completed_at,
                )
                for task in tasks
            ]
            related_audit_count = int(
                await self._session.scalar(
                    select(func.count(AuditLog.id)).where(AuditLog.operation_id == operation_id)
                )
                or 0
            )
            related_backup_ids = list(
                (
                    await self._session.scalars(
                        select(BackupRun.id).where(BackupRun.operation_id == operation_id)
                    )
                ).all()
            )
        else:
            steps = (
                await self._session.scalars(
                    select(ProvisioningStep)
                    .where(ProvisioningStep.provisioning_request_id == operation_id)
                    .order_by(ProvisioningStep.step_order)
                )
            ).all()
            provisioning_steps = [
                OperationStepResponse(
                    order=step.step_order,
                    name=step.step_name,
                    status=step.status,
                    attempt_count=step.attempt_count,
                    upid_reference=(
                        self._safe_upid_reference(step.pve_upid) if step.pve_upid else None
                    ),
                    error_code=step.error_code,
                    started_at=step.started_at,
                    finished_at=step.finished_at,
                )
                for step in steps
            ]
            related_audit_count = int(
                await self._session.scalar(
                    select(func.count(AuditLog.id)).where(
                        AuditLog.resource_type == "provisioning_request",
                        AuditLog.resource_id == str(operation_id),
                    )
                )
                or 0
            )
        return OperationCenterDetailResponse(
            **item.model_dump(),
            events=[
                OperationEventResponse(
                    id=event.id,
                    event_type=event.event_type,
                    status=event.status,
                    step=event.step,
                    message=event.message,
                    details=event.details,
                    actor_user_id=event.actor_user_id,
                    occurred_at=event.occurred_at,
                )
                for event in events
            ],
            pve_tasks=pve_tasks,
            provisioning_steps=provisioning_steps,
            related_audit_count=related_audit_count,
            related_backup_ids=related_backup_ids,
        )

    async def get_item(self, operation_id: UUID) -> OperationCenterItemResponse:
        operations = await self._operation_rows(operation_id=operation_id)
        if operations:
            return operations[0]
        provisioning = await self._provisioning_rows(operation_id=operation_id)
        if provisioning:
            return provisioning[0]
        raise AppError(404, "OPERATION_NOT_FOUND", "The operation was not found.")

    async def cancel(self, operation_id: UUID, *, version: int) -> OperationCenterItemResponse:
        resource_type, target = await self._target(operation_id, lock=True)
        if target.version != version:
            raise self._version_conflict()
        now = datetime.now(UTC)
        if resource_type == "OPERATION":
            operation = cast(Operation, target)
            if operation.status != OperationStatus.QUEUED.value:
                raise AppError(
                    409,
                    "OPERATION_CANCEL_UNSAFE",
                    "Only a queued operation can be cancelled safely.",
                )
            operation.status = OperationStatus.CANCELLED.value
            operation.cancel_requested_at = now
            operation.finished_at = now
            operation.heartbeat_at = now
            operation.retryable = False
            operation.version += 1
        else:
            request = cast(ProvisioningRequest, target)
            if request.status != ProvisioningStatus.QUEUED.value:
                raise AppError(
                    409,
                    "OPERATION_CANCEL_UNSAFE",
                    "Provisioning can only be cancelled before worker execution.",
                )
            request.status = ProvisioningStatus.CANCELLED.value
            request.current_step = "CANCELLED"
            request.finished_at = now
            request.heartbeat_at = now
            request.version += 1
        self._audit("OPERATION_CANCELLED", resource_type, target)
        await self._session.commit()
        return await self.get_item(operation_id)

    async def retry(
        self,
        operation_id: UUID,
        *,
        version: int,
    ) -> tuple[OperationCenterItemResponse, UUID]:
        resource_type, target = await self._target(operation_id, lock=True)
        if target.version != version:
            raise self._version_conflict()
        if resource_type == "OPERATION":
            created = await self._retry_operation(cast(Operation, target))
        else:
            created = await self._retry_provisioning(cast(ProvisioningRequest, target))
        return await self.get_item(operation_id), created

    async def assign(
        self,
        operation_id: UUID,
        *,
        assigned_to_id: UUID,
        version: int,
    ) -> OperationCenterItemResponse:
        resource_type, target = await self._target(operation_id, lock=True)
        assignee = await self._session.get(User, assigned_to_id)
        if (
            assignee is None
            or not assignee.is_active
            or assignee.role not in {UserRole.SUPER_ADMIN.value, UserRole.OPERATOR.value}
        ):
            raise AppError(404, "ASSIGNEE_NOT_FOUND", "The assignee was not found.")
        assignment = await self._assignment(resource_type, operation_id, lock=True)
        if target.version != version:
            raise self._version_conflict()
        now = datetime.now(UTC)
        assignment.assigned_to_id = assigned_to_id
        assignment.assigned_by_id = self._principal.user_id
        assignment.assigned_at = now
        assignment.version += 1
        target.version += 1
        self._add_manual_event(
            resource_type,
            operation_id,
            "ASSIGNED",
            "Operation ownership was assigned",
            {"assigned_to_id": str(assigned_to_id)},
        )
        self._audit("OPERATION_ASSIGNED", resource_type, target)
        await self._session.commit()
        return await self.get_item(operation_id)

    async def acknowledge(
        self,
        operation_id: UUID,
        *,
        version: int,
    ) -> OperationCenterItemResponse:
        resource_type, target = await self._target(operation_id, lock=True)
        assignment = await self._assignment(resource_type, operation_id, lock=True)
        if target.version != version:
            raise self._version_conflict()
        assignment.acknowledged_by_id = self._principal.user_id
        assignment.acknowledged_at = datetime.now(UTC)
        assignment.version += 1
        target.version += 1
        self._add_manual_event(
            resource_type,
            operation_id,
            "ACKNOWLEDGED",
            "An operator acknowledged the operation",
            {},
        )
        self._audit("OPERATION_ACKNOWLEDGED", resource_type, target)
        await self._session.commit()
        return await self.get_item(operation_id)

    async def resolve_manually(
        self,
        operation_id: UUID,
        *,
        resolution_note: str,
        version: int,
    ) -> OperationCenterItemResponse:
        resource_type, target = await self._target(operation_id, lock=True)
        if target.status not in {
            OperationStatus.NEEDS_ATTENTION.value,
            ProvisioningStatus.MANUAL_REVIEW.value,
            OperationStatus.FAILED.value,
            OperationStatus.TIMEOUT.value,
        }:
            raise AppError(
                409,
                "OPERATION_NOT_MANUALLY_RESOLVABLE",
                "This operation does not require manual resolution.",
            )
        assignment = await self._assignment(resource_type, operation_id, lock=True)
        if target.version != version:
            raise self._version_conflict()
        assignment.resolved_by_id = self._principal.user_id
        assignment.resolved_at = datetime.now(UTC)
        assignment.resolution_note = resolution_note.strip()
        assignment.version += 1
        target.version += 1
        self._add_manual_event(
            resource_type,
            operation_id,
            "RESOLVED_MANUALLY",
            "Manual review was completed with an operator note",
            {"resolution_recorded": True},
        )
        self._audit("OPERATION_RESOLVED_MANUALLY", resource_type, target)
        await self._session.commit()
        return await self.get_item(operation_id)

    async def _target(
        self,
        operation_id: UUID,
        *,
        lock: bool,
    ) -> tuple[ResourceType, Operation | ProvisioningRequest]:
        operation_query = select(Operation).where(Operation.id == operation_id)
        if lock:
            operation_query = operation_query.with_for_update()
        operation = await self._session.scalar(operation_query)
        if operation is not None:
            return "OPERATION", operation
        provisioning_query = select(ProvisioningRequest).where(
            ProvisioningRequest.id == operation_id
        )
        if lock:
            provisioning_query = provisioning_query.with_for_update()
        provisioning = await self._session.scalar(provisioning_query)
        if provisioning is not None:
            return "PROVISIONING", provisioning
        raise AppError(404, "OPERATION_NOT_FOUND", "The operation was not found.")

    async def _assignment(
        self,
        resource_type: ResourceType,
        operation_id: UUID,
        *,
        lock: bool,
    ) -> OperationAssignment:
        query = select(OperationAssignment)
        if resource_type == "OPERATION":
            query = query.where(OperationAssignment.operation_id == operation_id)
        else:
            query = query.where(OperationAssignment.provisioning_request_id == operation_id)
        if lock:
            query = query.with_for_update()
        assignment = await self._session.scalar(query)
        if assignment is None:
            assignment = OperationAssignment(
                operation_id=operation_id if resource_type == "OPERATION" else None,
                provisioning_request_id=(operation_id if resource_type == "PROVISIONING" else None),
                version=1,
            )
            self._session.add(assignment)
            await self._session.flush()
        return assignment

    async def _retry_operation(self, original: Operation) -> UUID:
        if (
            original.status
            not in {
                OperationStatus.FAILED.value,
                OperationStatus.TIMEOUT.value,
            }
            or not original.retryable
        ):
            raise AppError(
                409,
                "OPERATION_NOT_RETRYABLE",
                "The operation cannot be retried safely.",
            )
        existing = await self._session.scalar(
            select(Operation).where(Operation.retry_of_id == original.id)
        )
        if existing is not None:
            return existing.id
        conflict = await self._session.scalar(
            select(Operation.id).where(
                Operation.workload_id == original.workload_id,
                Operation.status.in_(
                    [
                        OperationStatus.QUEUED.value,
                        OperationStatus.RUNNING.value,
                        OperationStatus.CANCEL_REQUESTED.value,
                    ]
                ),
            )
        )
        if conflict is not None:
            raise AppError(409, "OPERATION_CONFLICT", "Another operation is already active.")
        if (
            original.operation_type in {"VM_SPEC_UPDATE", "VM_DELETE", "WORKLOAD_RESTORE"}
            and self._principal.role is not UserRole.SUPER_ADMIN
        ):
            raise AppError(403, "FORBIDDEN", "This retry requires super administrator access.")
        new_id = uuid4()
        now = datetime.now(UTC)
        retry = Operation(
            id=new_id,
            retry_of_id=original.id,
            operation_type=original.operation_type,
            action=original.action,
            status=OperationStatus.QUEUED.value,
            requested_by_id=self._principal.user_id,
            source_ip=self._source_ip,
            organization_id=original.organization_id,
            cluster_id=original.cluster_id,
            workload_id=original.workload_id,
            idempotency_key_hash=hashlib.sha256(f"retry:{new_id}".encode()).digest(),
            request_fingerprint=original.request_fingerprint,
            celery_task_id=str(uuid4()),
            result={**original.result, "retry_of_id": str(original.id)},
            retryable=None,
            attempt_count=0,
            requested_at=now,
            queued_at=now,
            version=1,
        )
        self._session.add(retry)
        event_type = self._outbox_event_type(retry.operation_type)
        outbox = add_operation_event(self._session, retry, event_type, now=now)
        if original.operation_type == "WORKLOAD_BACKUP":
            run = await self._session.scalar(
                select(BackupRun).where(BackupRun.operation_id == original.id)
            )
            if run is None:
                raise AppError(409, "OPERATION_RETRY_STATE_MISSING", "Backup state is missing.")
            self._session.add(
                BackupRun(
                    operation_id=retry.id,
                    backup_target_id=run.backup_target_id,
                    workload_id=run.workload_id,
                    organization_id=run.organization_id,
                    mode=run.mode,
                    compression=run.compression,
                    status=OperationStatus.QUEUED.value,
                )
            )
        elif original.operation_type == "WORKLOAD_RESTORE":
            run = await self._session.scalar(
                select(RestoreRun).where(RestoreRun.operation_id == original.id)
            )
            if run is None:
                raise AppError(409, "OPERATION_RETRY_STATE_MISSING", "Restore state is missing.")
            self._session.add(
                RestoreRun(
                    operation_id=retry.id,
                    backup_run_id=run.backup_run_id,
                    cluster_id=run.cluster_id,
                    source_workload_id=run.source_workload_id,
                    target_node=run.target_node,
                    target_vmid=run.target_vmid,
                    target_name=run.target_name,
                    status=OperationStatus.QUEUED.value,
                )
            )
        self._audit("OPERATION_RETRIED", "OPERATION", original, created_id=retry.id)
        await self._session.commit()
        try:
            self._publishers[event_type](retry.id, retry.celery_task_id)
        except Exception:
            logger.exception(
                "Operation retry enqueue failed; outbox recovery will retry",
                extra={"operation_id": str(retry.id)},
            )
        else:
            outbox.status = "PUBLISHED"
            outbox.published_at = datetime.now(UTC)
            outbox.attempt_count += 1
            await self._session.commit()
        return retry.id

    async def _retry_provisioning(self, original: ProvisioningRequest) -> UUID:
        require_service_role(self._principal, UserRole.SUPER_ADMIN)
        if (
            original.status != ProvisioningStatus.FAILED.value
            or original.clone_submitted
            or original.ip_address_id is not None
        ):
            raise AppError(
                409,
                "OPERATION_NOT_RETRYABLE",
                "Provisioning cannot be retried after resource reservation or clone submission.",
            )
        existing = await self._session.scalar(
            select(ProvisioningRequest).where(
                ProvisioningRequest.retry_of_request_id == original.id
            )
        )
        if existing is not None:
            return existing.id
        new_id = uuid4()
        retry = ProvisioningRequest(
            id=new_id,
            retry_of_request_id=original.id,
            requested_by_id=self._principal.user_id,
            idempotency_key_hash=hashlib.sha256(f"provision-retry:{new_id}".encode()).digest(),
            request_fingerprint=original.request_fingerprint,
            product_id=original.product_id,
            template_id=original.template_id,
            organization_id=original.organization_id,
            target_cluster_id=original.target_cluster_id,
            target_node_id=original.target_node_id,
            target_vmid=original.target_vmid,
            target_name=original.target_name,
            ip_pool_id=original.ip_pool_id,
            requested_ip_address=original.requested_ip_address,
            status=ProvisioningStatus.QUEUED.value,
            current_step=PROVISIONING_STEPS[0],
            spec_snapshot=original.spec_snapshot,
            celery_task_id=str(uuid4()),
            clone_submitted=False,
            version=1,
        )
        self._session.add(retry)
        self._session.add_all(
            [
                ProvisioningStep(
                    provisioning_request_id=retry.id,
                    step_order=index,
                    step_name=name,
                    status=ProvisioningStepStatus.PENDING.value,
                    attempt_count=0,
                    safe_result={},
                )
                for index, name in enumerate(PROVISIONING_STEPS, start=1)
            ]
        )
        self._audit("OPERATION_RETRIED", "PROVISIONING", original, created_id=retry.id)
        await self._session.commit()
        try:
            self._provisioning_publisher(retry.id, retry.celery_task_id)
        except Exception:
            logger.exception(
                "Provisioning retry enqueue failed; watchdog recovery will retry",
                extra={"provisioning_request_id": str(retry.id)},
            )
        return retry.id

    async def _operation_rows(
        self,
        *,
        operation_id: UUID | None = None,
        status: str | None = None,
        operation_type: str | None = None,
        cluster_id: UUID | None = None,
        organization_id: UUID | None = None,
        actor_id: UUID | None = None,
        error_code: str | None = None,
        requested_from: datetime | None = None,
        requested_to: datetime | None = None,
    ) -> list[OperationCenterItemResponse]:
        requester = aliased(User)
        assignee = aliased(User)
        query = (
            select(
                Operation,
                Cluster.name,
                Organization.name,
                requester.display_name,
                Workload.name,
                OperationAssignment,
                assignee.display_name,
            )
            .join(Cluster, Cluster.id == Operation.cluster_id)
            .join(requester, requester.id == Operation.requested_by_id)
            .outerjoin(Organization, Organization.id == Operation.organization_id)
            .outerjoin(Workload, Workload.id == Operation.workload_id)
            .outerjoin(OperationAssignment, OperationAssignment.operation_id == Operation.id)
            .outerjoin(assignee, assignee.id == OperationAssignment.assigned_to_id)
        )
        query = self._filters(
            query,
            model=Operation,
            operation_id=operation_id,
            status=status,
            operation_type=operation_type,
            cluster_id=cluster_id,
            organization_id=organization_id,
            actor_id=actor_id,
            error_code=error_code,
            requested_from=requested_from,
            requested_to=requested_to,
        )
        rows = (await self._session.execute(query)).all()
        return [
            self._operation_response(
                operation,
                cluster_name=cluster_name,
                organization_name=organization_name,
                requester_name=requester_name,
                workload_name=workload_name,
                assignment=assignment,
                assignee_name=assignee_name,
            )
            for (
                operation,
                cluster_name,
                organization_name,
                requester_name,
                workload_name,
                assignment,
                assignee_name,
            ) in rows
        ]

    async def _provisioning_rows(
        self,
        *,
        operation_id: UUID | None = None,
        status: str | None = None,
        operation_type: str | None = None,
        cluster_id: UUID | None = None,
        organization_id: UUID | None = None,
        actor_id: UUID | None = None,
        error_code: str | None = None,
        requested_from: datetime | None = None,
        requested_to: datetime | None = None,
    ) -> list[OperationCenterItemResponse]:
        if operation_type is not None and operation_type != "PROVISION_VM":
            return []
        requester = aliased(User)
        assignee = aliased(User)
        query = (
            select(
                ProvisioningRequest,
                Cluster.name,
                Organization.name,
                requester.display_name,
                Workload.name,
                OperationAssignment,
                assignee.display_name,
            )
            .join(Cluster, Cluster.id == ProvisioningRequest.target_cluster_id)
            .join(requester, requester.id == ProvisioningRequest.requested_by_id)
            .join(Organization, Organization.id == ProvisioningRequest.organization_id)
            .outerjoin(Workload, Workload.id == ProvisioningRequest.workload_id)
            .outerjoin(
                OperationAssignment,
                OperationAssignment.provisioning_request_id == ProvisioningRequest.id,
            )
            .outerjoin(assignee, assignee.id == OperationAssignment.assigned_to_id)
        )
        query = self._filters(
            query,
            model=ProvisioningRequest,
            operation_id=operation_id,
            status=status,
            operation_type=None,
            cluster_id=cluster_id,
            organization_id=organization_id,
            actor_id=actor_id,
            error_code=error_code,
            requested_from=requested_from,
            requested_to=requested_to,
        )
        rows = (await self._session.execute(query)).all()
        return [
            self._provisioning_response(
                request,
                cluster_name=cluster_name,
                organization_name=organization_name,
                requester_name=requester_name,
                workload_name=workload_name,
                assignment=assignment,
                assignee_name=assignee_name,
            )
            for (
                request,
                cluster_name,
                organization_name,
                requester_name,
                workload_name,
                assignment,
                assignee_name,
            ) in rows
        ]

    @staticmethod
    def _filters(
        query: Select[Any],
        *,
        model: type[Operation] | type[ProvisioningRequest],
        operation_id: UUID | None,
        status: str | None,
        operation_type: str | None,
        cluster_id: UUID | None,
        organization_id: UUID | None,
        actor_id: UUID | None,
        error_code: str | None,
        requested_from: datetime | None,
        requested_to: datetime | None,
    ) -> Select[Any]:
        typed_query = query
        if operation_id is not None:
            typed_query = typed_query.where(model.id == operation_id)
        if status is not None:
            typed_query = typed_query.where(model.status == status)
        if operation_type is not None and model is Operation:
            typed_query = typed_query.where(Operation.operation_type == operation_type)
        cluster_column = (
            Operation.cluster_id if model is Operation else ProvisioningRequest.target_cluster_id
        )
        if cluster_id is not None:
            typed_query = typed_query.where(cluster_column == cluster_id)
        if organization_id is not None:
            typed_query = typed_query.where(model.organization_id == organization_id)
        if actor_id is not None:
            typed_query = typed_query.where(model.requested_by_id == actor_id)
        if error_code is not None:
            typed_query = typed_query.where(model.error_code == error_code)
        if requested_from is not None:
            typed_query = typed_query.where(model.requested_at >= requested_from)
        if requested_to is not None:
            typed_query = typed_query.where(model.requested_at <= requested_to)
        return typed_query

    def _operation_response(
        self,
        operation: Operation,
        *,
        cluster_name: str,
        organization_name: str | None,
        requester_name: str,
        workload_name: str | None,
        assignment: OperationAssignment | None,
        assignee_name: str | None,
    ) -> OperationCenterItemResponse:
        current_step = None
        return OperationCenterItemResponse(
            id=operation.id,
            resource_type="OPERATION",
            operation_type=operation.operation_type,
            action=operation.action,
            status=operation.status,
            cluster_id=operation.cluster_id,
            cluster_name=cluster_name,
            organization_id=operation.organization_id,
            organization_name=organization_name,
            requested_by_id=operation.requested_by_id,
            requested_by_name=requester_name,
            workload_id=operation.workload_id,
            workload_name=workload_name,
            current_step=current_step,
            error_code=operation.error_code,
            error_summary=operation.error_summary,
            retryable=bool(operation.retryable),
            retry_of_id=operation.retry_of_id,
            requested_at=operation.requested_at,
            started_at=operation.started_at,
            finished_at=operation.finished_at,
            heartbeat_at=operation.heartbeat_at,
            is_stuck=self._is_stuck(
                operation.status,
                operation.heartbeat_at or operation.requested_at,
            ),
            available_actions=self._available_actions(
                "OPERATION",
                operation.status,
                bool(operation.retryable),
                operation.operation_type,
                assignment,
                provisioning_safe_retry=False,
            ),
            impact_summary=self._impact_summary(
                operation.organization_id,
                organization_name,
                workload_name,
            ),
            recommended_action=self._recommendation(
                operation.status,
                operation.error_code,
                bool(operation.retryable),
            ),
            assignment=self._assignment_response(assignment, assignee_name),
            version=operation.version,
        )

    def _provisioning_response(
        self,
        request: ProvisioningRequest,
        *,
        cluster_name: str,
        organization_name: str,
        requester_name: str,
        workload_name: str | None,
        assignment: OperationAssignment | None,
        assignee_name: str | None,
    ) -> OperationCenterItemResponse:
        safe_retry = (
            request.status == ProvisioningStatus.FAILED.value
            and not request.clone_submitted
            and request.ip_address_id is None
        )
        return OperationCenterItemResponse(
            id=request.id,
            resource_type="PROVISIONING",
            operation_type="PROVISION_VM",
            action="provision",
            status=request.status,
            cluster_id=request.target_cluster_id,
            cluster_name=cluster_name,
            organization_id=request.organization_id,
            organization_name=organization_name,
            requested_by_id=request.requested_by_id,
            requested_by_name=requester_name,
            workload_id=request.workload_id,
            workload_name=workload_name or request.target_name,
            current_step=request.current_step,
            error_code=request.error_code,
            error_summary=request.error_summary,
            retryable=safe_retry,
            retry_of_id=request.retry_of_request_id,
            requested_at=request.requested_at,
            started_at=request.started_at,
            finished_at=request.finished_at,
            heartbeat_at=request.heartbeat_at,
            is_stuck=self._is_stuck(
                request.status,
                request.heartbeat_at or request.requested_at,
            ),
            available_actions=self._available_actions(
                "PROVISIONING",
                request.status,
                safe_retry,
                "PROVISION_VM",
                assignment,
                provisioning_safe_retry=safe_retry,
            ),
            impact_summary=self._impact_summary(
                request.organization_id,
                organization_name,
                workload_name or request.target_name,
            ),
            recommended_action=self._recommendation(
                request.status,
                request.error_code,
                safe_retry,
            ),
            assignment=self._assignment_response(assignment, assignee_name),
            version=request.version,
        )

    def _available_actions(
        self,
        resource_type: ResourceType,
        status: str,
        retryable: bool,
        operation_type: str,
        assignment: OperationAssignment | None,
        *,
        provisioning_safe_retry: bool,
    ) -> list[str]:
        actions: list[str] = []
        resolved = assignment is not None and assignment.resolved_at is not None
        if status == "QUEUED":
            actions.append("CANCEL")
        if not resolved and status not in {"SUCCEEDED", "CANCELLED"}:
            actions.append("ASSIGN")
            if assignment is None or assignment.acknowledged_at is None:
                actions.append("ACKNOWLEDGE")
        retry_allowed = retryable and status in {"FAILED", "TIMEOUT"}
        if resource_type == "PROVISIONING":
            retry_allowed = provisioning_safe_retry
        requires_super = operation_type in {
            "VM_SPEC_UPDATE",
            "VM_DELETE",
            "WORKLOAD_RESTORE",
            "PROVISION_VM",
        }
        if retry_allowed and (not requires_super or self._principal.role is UserRole.SUPER_ADMIN):
            actions.append("RETRY")
        if not resolved and status in {
            "NEEDS_ATTENTION",
            "MANUAL_REVIEW",
            "FAILED",
            "TIMEOUT",
        }:
            actions.append("RESOLVE_MANUALLY")
        return actions

    def _is_stuck(self, status: str, heartbeat_at: datetime) -> bool:
        if status not in {"QUEUED", "RUNNING", "CANCEL_REQUESTED"}:
            return False
        return heartbeat_at <= datetime.now(UTC) - timedelta(
            seconds=self._settings.operation_watchdog_seconds
        )

    @staticmethod
    def _impact_summary(
        organization_id: UUID | None,
        organization_name: str | None,
        workload_name: str | None,
    ) -> str:
        target = workload_name or "an infrastructure resource"
        if organization_id is not None:
            return f"{target} assigned to {organization_name or 'an organization'} is affected."
        return f"{target} is affected; no customer organization is currently assigned."

    @staticmethod
    def _recommendation(status: str, error_code: str | None, retryable: bool) -> str:
        if status in {"QUEUED", "RUNNING", "CANCEL_REQUESTED"}:
            return "Monitor heartbeat and step progress; the watchdog will redeliver stale work."
        if status == "SUCCEEDED":
            return "No operator action is required."
        if status == "CANCELLED":
            return "Confirm that no PVE task was submitted before cancellation."
        if status in {"NEEDS_ATTENTION", "MANUAL_REVIEW"}:
            return "Inspect the target state in PVE, assign an owner, and record manual resolution."
        if retryable:
            return "Verify cluster health and use the safe retry action."
        if error_code:
            return (
                f"Review policy and target state for error {error_code}; do not resubmit blindly."
            )
        return "Review the event timeline and target state before taking further action."

    @staticmethod
    def _assignment_response(
        assignment: OperationAssignment | None,
        assignee_name: str | None,
    ) -> OperationAssignmentResponse | None:
        if assignment is None:
            return None
        return OperationAssignmentResponse(
            assigned_to_id=assignment.assigned_to_id,
            assigned_to_name=assignee_name,
            assigned_at=assignment.assigned_at,
            acknowledged_by_id=assignment.acknowledged_by_id,
            acknowledged_at=assignment.acknowledged_at,
            resolved_by_id=assignment.resolved_by_id,
            resolved_at=assignment.resolved_at,
            resolution_note=assignment.resolution_note,
            version=assignment.version,
        )

    def _add_manual_event(
        self,
        resource_type: ResourceType,
        operation_id: UUID,
        event_type: str,
        message: str,
        details: dict[str, object],
    ) -> None:
        self._session.add(
            OperationEvent(
                operation_id=operation_id if resource_type == "OPERATION" else None,
                provisioning_request_id=(operation_id if resource_type == "PROVISIONING" else None),
                event_type=event_type,
                message=message,
                details=details,
                actor_user_id=self._principal.user_id,
                occurred_at=datetime.now(UTC),
            )
        )

    def _audit(
        self,
        action: str,
        resource_type: ResourceType,
        target: Operation | ProvisioningRequest,
        *,
        created_id: UUID | None = None,
    ) -> None:
        workload_id = (
            target.workload_id if isinstance(target, (Operation, ProvisioningRequest)) else None
        )
        add_audit_event(
            self._session,
            action=action,
            outcome="SUCCEEDED",
            request_id=self._request_id,
            actor_user_id=self._principal.user_id,
            actor_role=self._principal.role,
            organization_id=target.organization_id,
            workload_id=workload_id,
            operation_id=target.id if resource_type == "OPERATION" else None,
            source_ip=self._source_ip,
            target_type="operation" if resource_type == "OPERATION" else "provisioning_request",
            target_id=target.id,
            after={"created_operation_id": str(created_id) if created_id else None},
        )

    @staticmethod
    def _version_conflict() -> AppError:
        return AppError(
            409,
            "OPERATION_VERSION_CONFLICT",
            "The operation changed. Refresh and try again.",
        )

    @staticmethod
    def _safe_upid_reference(upid: str) -> str:
        return f"upid:{hashlib.sha256(upid.encode()).hexdigest()[:12]}"

    @staticmethod
    def _outbox_event_type(operation_type: str) -> str:
        if operation_type == "WORKLOAD_BACKUP":
            return BACKUP_EVENT
        if operation_type == "WORKLOAD_RESTORE":
            return RESTORE_EVENT
        return POWER_EVENT

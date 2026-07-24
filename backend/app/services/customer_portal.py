import hmac
import logging
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import UUID, uuid4

from sqlalchemy import Select, exists, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError
from app.models.auth import Organization, OrganizationMember, UserRole
from app.models.cluster import Cluster
from app.models.ipam import IpAddress, IpAllocation, IpAllocationStatus
from app.models.operation import Operation, OperationStatus, PowerAction, Workload
from app.schemas.customer import (
    CustomerJobResponse,
    CustomerVmDetailResponse,
    CustomerVmSummary,
)
from app.security.access import Principal, require_service_role
from app.services.audit import add_audit_event
from app.services.outbox import (
    POWER_EVENT,
    add_operation_event,
    record_publish_failure,
    record_publish_success,
)

CustomerOperationPublisher = Callable[[UUID, str], None]
logger = logging.getLogger(__name__)


class CustomerPortalService:
    """Customer-only queries with ownership predicates embedded in SQL."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        settings: Settings,
        principal: Principal,
        publisher: CustomerOperationPublisher,
        request_id: str,
        source_ip: str,
    ) -> None:
        self._session = session
        self._settings = settings
        self._principal = principal
        self._publisher = publisher
        self._request_id = request_id
        self._source_ip = source_ip
        require_service_role(principal, UserRole.CUSTOMER)

    async def list_vms(self) -> list[CustomerVmSummary]:
        workloads = await self._session.scalars(
            self._owned_workloads_query().order_by(Workload.name.asc(), Workload.id.asc())
        )
        owned = workloads.all()
        assigned_ips = await self._assigned_ip_addresses([item.id for item in owned])
        organization_names = await self._organization_names(owned)
        sync_intervals = await self._cluster_sync_intervals(owned)
        return [
            self._vm_summary(
                item,
                organization_name=organization_names[item.organization_id],
                assigned_ip_addresses=assigned_ips.get(item.id, []),
                sync_interval_seconds=sync_intervals[item.cluster_id],
            )
            for item in owned
            if item.organization_id in organization_names and item.cluster_id in sync_intervals
        ]

    async def get_vm(self, vm_id: UUID) -> CustomerVmDetailResponse:
        workload = await self._owned_vm(vm_id)
        jobs = await self._recent_jobs(workload.id)
        assigned_ips = await self._assigned_ip_addresses([workload.id])
        organization_names = await self._organization_names([workload])
        sync_intervals = await self._cluster_sync_intervals([workload])
        sync_interval_seconds = sync_intervals.get(workload.cluster_id)
        if workload.organization_id not in organization_names or sync_interval_seconds is None:
            raise self._not_found()
        summary = self._vm_summary(
            workload,
            organization_name=organization_names[workload.organization_id],
            assigned_ip_addresses=assigned_ips.get(workload.id, []),
            sync_interval_seconds=sync_interval_seconds,
        )
        return CustomerVmDetailResponse(**summary.model_dump(), recent_jobs=jobs)

    async def request_power_action(
        self,
        *,
        vm_id: UUID,
        action: PowerAction,
        idempotency_key: str,
        reason: str | None,
        confirm_forced: bool,
    ) -> CustomerJobResponse:
        if action not in {
            PowerAction.START,
            PowerAction.SHUTDOWN,
            PowerAction.STOP,
            PowerAction.REBOOT,
        }:
            raise AppError(
                status_code=403,
                code="CUSTOMER_ACTION_FORBIDDEN",
                message="This power action is not available to customers.",
            )
        workload = await self._owned_vm(vm_id)
        sync_intervals = await self._cluster_sync_intervals([workload])
        sync_interval_seconds = sync_intervals.get(workload.cluster_id)
        if sync_interval_seconds is None:
            raise self._not_found()
        self._require_fresh_inventory(
            workload,
            sync_interval_seconds=sync_interval_seconds,
        )
        if workload.organization_id is None:
            raise self._not_found()
        if action is PowerAction.STOP and not confirm_forced:
            raise AppError(
                status_code=422,
                code="FORCED_ACTION_CONFIRMATION_REQUIRED",
                message="Forced stop requires explicit confirmation.",
            )

        key_hash = self._key_hash(idempotency_key)
        fingerprint = sha256(f"customer:{vm_id}:{action.value}".encode()).digest()
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
            if (
                existing.workload_id != workload.id
                or existing.organization_id != workload.organization_id
            ):
                raise self._not_found()
            return self._job_response(existing)

        conflict = await self._session.scalar(
            select(Operation.id).where(
                Operation.workload_id == workload.id,
                Operation.status.in_([OperationStatus.QUEUED.value, OperationStatus.RUNNING.value]),
            )
        )
        if conflict is not None:
            raise AppError(
                status_code=409,
                code="OPERATION_CONFLICT",
                message="Another power operation is already running for this VM.",
            )

        operation = Operation(
            id=uuid4(),
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
            celery_task_id=str(uuid4()),
            result={
                "action_mode": self._action_mode(action),
                "reason_recorded": reason is not None,
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
        outbox = add_operation_event(self._session, operation, POWER_EVENT)
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
            details={"portal": "customer", "action_mode": operation.result["action_mode"]},
        )
        await self._session.commit()
        try:
            self._publisher(operation.id, operation.celery_task_id)
        except Exception:
            await record_publish_failure(self._session, outbox, self._settings)
            logger.exception(
                "Customer power operation enqueue failed; worker recovery will retry",
                extra={"operation_id": str(operation.id)},
            )
        else:
            await record_publish_success(self._session, outbox)
        return self._job_response(operation)

    @staticmethod
    def _action_mode(action: PowerAction) -> str:
        if action is PowerAction.SHUTDOWN:
            return "GRACEFUL"
        if action is PowerAction.STOP:
            return "FORCED"
        return "STANDARD"

    async def get_job(self, job_id: UUID) -> CustomerJobResponse:
        current_owner = exists(
            select(Workload.id).where(
                Workload.id == Operation.workload_id,
                Workload.organization_id == Operation.organization_id,
            )
        )
        membership = exists(
            select(OrganizationMember.id).where(
                OrganizationMember.user_id == self._principal.user_id,
                OrganizationMember.organization_id == Operation.organization_id,
            )
        )
        active_organization = exists(
            select(Organization.id).where(
                Organization.id == Operation.organization_id,
                Organization.is_active.is_(True),
            )
        )
        operation = await self._session.scalar(
            select(Operation).where(
                Operation.id == job_id,
                Operation.requested_by_id == self._principal.user_id,
                current_owner,
                membership,
                active_organization,
            )
        )
        if operation is None:
            raise AppError(status_code=404, code="JOB_NOT_FOUND", message="The job was not found.")
        return self._job_response(operation)

    async def list_jobs(self, *, limit: int) -> list[CustomerJobResponse]:
        current_owner = exists(
            select(Workload.id).where(
                Workload.id == Operation.workload_id,
                Workload.organization_id == Operation.organization_id,
            )
        )
        membership = exists(
            select(OrganizationMember.id).where(
                OrganizationMember.user_id == self._principal.user_id,
                OrganizationMember.organization_id == Operation.organization_id,
            )
        )
        active_organization = exists(
            select(Organization.id).where(
                Organization.id == Operation.organization_id,
                Organization.is_active.is_(True),
            )
        )
        operations = await self._session.scalars(
            select(Operation)
            .where(
                Operation.requested_by_id == self._principal.user_id,
                Operation.operation_type.like("POWER_%"),
                current_owner,
                membership,
                active_organization,
            )
            .order_by(Operation.requested_at.desc(), Operation.id.desc())
            .limit(limit)
        )
        return [self._job_response(item) for item in operations.all()]

    def _owned_workloads_query(self) -> Select[tuple[Workload]]:
        membership = exists(
            select(OrganizationMember.id).where(
                OrganizationMember.user_id == self._principal.user_id,
                OrganizationMember.organization_id == Workload.organization_id,
            )
        )
        active_organization = exists(
            select(Organization.id).where(
                Organization.id == Workload.organization_id,
                Organization.is_active.is_(True),
            )
        )
        active_cluster = exists(
            select(Cluster.id).where(
                Cluster.id == Workload.cluster_id,
                Cluster.is_active.is_(True),
            )
        )
        return select(Workload).where(
            Workload.organization_id.is_not(None),
            Workload.kind == "QEMU",
            Workload.is_present.is_(True),
            Workload.is_template.is_(False),
            membership,
            active_organization,
            active_cluster,
        )

    async def _owned_vm(self, vm_id: UUID) -> Workload:
        workload = await self._session.scalar(
            self._owned_workloads_query().where(Workload.id == vm_id)
        )
        if workload is None:
            raise self._not_found()
        return workload

    async def _recent_jobs(self, vm_id: UUID) -> list[CustomerJobResponse]:
        operations = await self._session.scalars(
            select(Operation)
            .where(
                Operation.workload_id == vm_id,
                Operation.requested_by_id == self._principal.user_id,
            )
            .order_by(Operation.requested_at.desc())
            .limit(10)
        )
        return [self._job_response(item) for item in operations.all()]

    async def _assigned_ip_addresses(self, workload_ids: list[UUID]) -> dict[UUID, list[str]]:
        if not workload_ids:
            return {}
        rows = await self._session.execute(
            select(IpAllocation.workload_id, IpAddress.address)
            .join(IpAddress, IpAddress.id == IpAllocation.ip_address_id)
            .where(
                IpAllocation.workload_id.in_(workload_ids),
                IpAllocation.status == IpAllocationStatus.ASSIGNED.value,
            )
            .order_by(IpAllocation.workload_id, IpAddress.address)
        )
        result: dict[UUID, list[str]] = {}
        for workload_id, address in rows.all():
            if workload_id is not None:
                result.setdefault(workload_id, []).append(str(address))
        return result

    async def _organization_names(self, workloads: Sequence[Workload]) -> dict[UUID, str]:
        organization_ids = {
            item.organization_id for item in workloads if item.organization_id is not None
        }
        if not organization_ids:
            return {}
        rows = await self._session.execute(
            select(Organization.id, Organization.name).where(
                Organization.id.in_(organization_ids),
                Organization.is_active.is_(True),
            )
        )
        return {organization_id: name for organization_id, name in rows.all()}

    async def _cluster_sync_intervals(
        self,
        workloads: Sequence[Workload],
    ) -> dict[UUID, int]:
        cluster_ids = {item.cluster_id for item in workloads}
        if not cluster_ids:
            return {}
        rows = await self._session.execute(
            select(Cluster.id, Cluster.sync_interval_seconds).where(
                Cluster.id.in_(cluster_ids),
                Cluster.is_active.is_(True),
            )
        )
        return {cluster_id: sync_interval for cluster_id, sync_interval in rows.all()}

    def _vm_summary(
        self,
        workload: Workload,
        *,
        organization_name: str,
        assigned_ip_addresses: list[str],
        sync_interval_seconds: int,
    ) -> CustomerVmSummary:
        is_stale = self._is_stale(
            workload,
            sync_interval_seconds=sync_interval_seconds,
        )
        return CustomerVmSummary(
            id=workload.id,
            name=workload.name or "Unnamed VM",
            organization_name=organization_name,
            power_state=workload.power_state,
            cpu_cores=workload.cpu_cores,
            memory_bytes=workload.memory_bytes,
            disk_bytes=workload.disk_bytes,
            assigned_ip_addresses=assigned_ip_addresses,
            observed_at=workload.observed_at,
            is_stale=is_stale,
            stale_reason="LAST_OBSERVATION_EXPIRED" if is_stale else None,
        )

    def _is_stale(self, workload: Workload, *, sync_interval_seconds: int) -> bool:
        stale_after_seconds = max(
            self._settings.inventory_stale_after_seconds,
            sync_interval_seconds * 3,
        )
        return workload.observed_at < datetime.now(UTC) - timedelta(seconds=stale_after_seconds)

    def _require_fresh_inventory(
        self,
        workload: Workload,
        *,
        sync_interval_seconds: int,
    ) -> None:
        if self._is_stale(workload, sync_interval_seconds=sync_interval_seconds):
            raise AppError(
                status_code=503,
                code="INVENTORY_STALE",
                message="The VM state is stale. Try again after inventory synchronization.",
            )

    @staticmethod
    def _job_response(operation: Operation) -> CustomerJobResponse:
        safe_result = {
            key: value
            for key, value in operation.result.items()
            if key in {"action_mode", "no_op", "message", "final_power_state"}
        }
        return CustomerJobResponse(
            id=operation.id,
            job_id=operation.id,
            vm_id=operation.workload_id,
            action=PowerAction(operation.action),
            action_mode=str(operation.result.get("action_mode", "STANDARD")),
            status=OperationStatus(operation.status),
            result=safe_result,
            error_code=operation.error_code,
            error_summary=operation.error_summary,
            retryable=operation.retryable,
            requested_at=operation.requested_at,
            started_at=operation.started_at,
            finished_at=operation.finished_at,
        )

    def _key_hash(self, key: str) -> bytes:
        secret = self._settings.app_secret_key.get_secret_value().encode()
        return hmac.new(secret, key.encode(), sha256).digest()

    @staticmethod
    def _not_found() -> AppError:
        return AppError(
            status_code=404,
            code="VM_NOT_FOUND",
            message="The virtual machine was not found.",
        )

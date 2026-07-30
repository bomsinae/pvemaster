from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models.auth import Organization, UserRole
from app.models.cluster import Cluster
from app.models.ipam import IpAddress, IpAllocation, IpAllocationStatus
from app.models.operation import Operation, OperationStatus, Workload, WorkloadAssignment
from app.schemas.workload import WorkloadAssignmentResponse, WorkloadResponse
from app.security.access import Principal, require_service_role
from app.services.audit import add_audit_event


class WorkloadService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        principal: Principal,
        request_id: str,
        inventory_stale_after_seconds: int = 180,
    ) -> None:
        self._session = session
        self._principal = principal
        self._request_id = request_id
        self._inventory_stale_after_seconds = inventory_stale_after_seconds
        require_service_role(principal, UserRole.SUPER_ADMIN, UserRole.OPERATOR)

    async def list_workloads(
        self,
        *,
        organization_id: UUID | None,
        cluster_id: UUID | None,
        is_present: bool = True,
    ) -> list[WorkloadResponse]:
        query = (
            select(
                Workload,
                Cluster.name,
                Cluster.sync_interval_seconds,
                Organization.name,
            )
            .join(Cluster, Cluster.id == Workload.cluster_id)
            .outerjoin(Organization, Organization.id == Workload.organization_id)
            .where(
                Cluster.is_active.is_(True),
                Workload.is_present.is_(is_present),
            )
            .order_by(Cluster.name.asc(), Workload.vmid.asc())
        )
        if organization_id is not None:
            query = query.where(Workload.organization_id == organization_id)
        if cluster_id is not None:
            query = query.where(Workload.cluster_id == cluster_id)
        rows = (await self._session.execute(query)).all()
        assigned_ips = await self._assigned_ip_addresses(
            [workload.id for workload, _, _, _ in rows]
        )
        return [
            self._response(
                workload,
                cluster_name,
                organization_name,
                assigned_ip_addresses=assigned_ips.get(workload.id, []),
                sync_interval_seconds=sync_interval_seconds,
            )
            for workload, cluster_name, sync_interval_seconds, organization_name in rows
        ]

    async def get(self, workload_id: UUID) -> WorkloadResponse:
        row = (
            await self._session.execute(
                select(
                    Workload,
                    Cluster.name,
                    Cluster.sync_interval_seconds,
                    Organization.name,
                )
                .join(Cluster, Cluster.id == Workload.cluster_id)
                .outerjoin(Organization, Organization.id == Workload.organization_id)
                .where(
                    Workload.id == workload_id,
                    Cluster.is_active.is_(True),
                )
            )
        ).one_or_none()
        if row is None:
            raise self._not_found()
        workload, cluster_name, sync_interval_seconds, organization_name = row
        assigned_ips = await self._assigned_ip_addresses([workload.id])
        return self._response(
            workload,
            cluster_name,
            organization_name,
            assigned_ip_addresses=assigned_ips.get(workload.id, []),
            sync_interval_seconds=sync_interval_seconds,
        )

    async def assign(self, workload_id: UUID, organization_id: UUID) -> WorkloadAssignmentResponse:
        workload = await self._locked_workload(workload_id)
        organization = await self._session.get(Organization, organization_id)
        if organization is None or not organization.is_active:
            raise AppError(404, "ORGANIZATION_NOT_FOUND", "The organization was not found.")
        if not workload.is_present or workload.is_template:
            raise AppError(
                409,
                "WORKLOAD_NOT_ASSIGNABLE",
                "Only present, non-template workloads can be assigned.",
            )
        active = await self._session.scalar(
            select(WorkloadAssignment)
            .where(
                WorkloadAssignment.workload_id == workload.id,
                WorkloadAssignment.revoked_at.is_(None),
            )
            .with_for_update()
        )
        if active is not None:
            if active.organization_id != organization.id:
                raise AppError(
                    409,
                    "WORKLOAD_ALREADY_ASSIGNED",
                    "The workload is already assigned to another organization.",
                )
            return self._assignment_response(active, organization.name)
        if workload.organization_id not in {None, organization.id}:
            raise AppError(
                409,
                "WORKLOAD_ALREADY_ASSIGNED",
                "The workload is already assigned to another organization.",
            )
        assignment = WorkloadAssignment(
            workload_id=workload.id,
            organization_id=organization.id,
            assigned_by_id=self._principal.user_id,
        )
        workload.organization_id = organization.id
        workload.version += 1
        self._session.add(assignment)
        add_audit_event(
            self._session,
            action="WORKLOAD_ASSIGNED",
            outcome="SUCCEEDED",
            request_id=self._request_id,
            actor_user_id=self._principal.user_id,
            actor_role=self._principal.role,
            organization_id=organization.id,
            workload_id=workload.id,
            target_type="workload",
            target_id=workload.id,
            before={"organization_id": None},
            after={"organization_id": str(organization.id)},
        )
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise AppError(
                409,
                "WORKLOAD_ALREADY_ASSIGNED",
                "The workload was assigned by another request.",
            ) from exc
        await self._session.refresh(assignment)
        return self._assignment_response(assignment, organization.name)

    async def unassign(self, workload_id: UUID, reason: str | None) -> None:
        workload = await self._locked_workload(workload_id)
        if workload.organization_id is None:
            return
        operation = await self._session.scalar(
            select(Operation.id).where(
                Operation.workload_id == workload.id,
                Operation.status.in_([OperationStatus.QUEUED.value, OperationStatus.RUNNING.value]),
            )
        )
        if operation is not None:
            raise AppError(
                409,
                "WORKLOAD_OPERATION_ACTIVE",
                "The workload cannot be unassigned while an operation is active.",
            )
        assignment = await self._session.scalar(
            select(WorkloadAssignment)
            .where(
                WorkloadAssignment.workload_id == workload.id,
                WorkloadAssignment.revoked_at.is_(None),
            )
            .with_for_update()
        )
        previous_organization_id = workload.organization_id
        now = datetime.now(UTC)
        if assignment is not None:
            assignment.revoked_by_id = self._principal.user_id
            assignment.revoked_at = now
            assignment.revoke_reason = reason.strip() if reason else None
        workload.organization_id = None
        workload.version += 1
        add_audit_event(
            self._session,
            action="WORKLOAD_UNASSIGNED",
            outcome="SUCCEEDED",
            request_id=self._request_id,
            actor_user_id=self._principal.user_id,
            actor_role=self._principal.role,
            organization_id=previous_organization_id,
            workload_id=workload.id,
            target_type="workload",
            target_id=workload.id,
            before={"organization_id": str(previous_organization_id)},
            after={"organization_id": None, "reason_recorded": reason is not None},
        )
        await self._session.commit()

    async def assignment_history(self, workload_id: UUID) -> list[WorkloadAssignmentResponse]:
        if await self._session.get(Workload, workload_id) is None:
            raise self._not_found()
        rows = await self._session.execute(
            select(WorkloadAssignment, Organization.name)
            .join(Organization, Organization.id == WorkloadAssignment.organization_id)
            .where(WorkloadAssignment.workload_id == workload_id)
            .order_by(WorkloadAssignment.assigned_at.desc())
        )
        return [
            self._assignment_response(item, organization_name)
            for item, organization_name in rows.all()
        ]

    async def _locked_workload(self, workload_id: UUID) -> Workload:
        workload = await self._session.scalar(
            select(Workload)
            .join(Cluster, Cluster.id == Workload.cluster_id)
            .where(
                Workload.id == workload_id,
                Workload.is_present.is_(True),
                Cluster.is_active.is_(True),
            )
            .with_for_update()
        )
        if workload is None:
            raise self._not_found()
        return workload

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

    def _response(
        self,
        workload: Workload,
        cluster_name: str,
        organization_name: str | None,
        *,
        assigned_ip_addresses: list[str],
        sync_interval_seconds: int,
    ) -> WorkloadResponse:
        is_stale = self._is_stale(
            workload,
            sync_interval_seconds=sync_interval_seconds,
        )
        return WorkloadResponse(
            id=workload.id,
            cluster_id=workload.cluster_id,
            cluster_name=cluster_name,
            vmid=workload.vmid,
            node=workload.node,
            kind=workload.kind,
            name=workload.name,
            power_state=workload.power_state,
            cpu_cores=workload.cpu_cores,
            memory_bytes=workload.memory_bytes,
            disk_bytes=workload.disk_bytes,
            is_template=workload.is_template,
            is_present=workload.is_present,
            sync_generation=workload.sync_generation,
            missing_since=workload.missing_since,
            organization_id=workload.organization_id,
            organization_name=organization_name,
            assigned_ip_addresses=assigned_ip_addresses,
            observed_at=workload.observed_at,
            is_stale=is_stale,
            stale_reason="LAST_OBSERVATION_EXPIRED" if is_stale else None,
            version=workload.version,
        )

    def _is_stale(self, workload: Workload, *, sync_interval_seconds: int) -> bool:
        stale_after_seconds = max(
            self._inventory_stale_after_seconds,
            sync_interval_seconds * 3,
        )
        return workload.observed_at < datetime.now(UTC) - timedelta(seconds=stale_after_seconds)

    @staticmethod
    def _assignment_response(
        assignment: WorkloadAssignment,
        organization_name: str,
    ) -> WorkloadAssignmentResponse:
        return WorkloadAssignmentResponse(
            id=assignment.id,
            workload_id=assignment.workload_id,
            organization_id=assignment.organization_id,
            organization_name=organization_name,
            assigned_by_id=assignment.assigned_by_id,
            assigned_at=assignment.assigned_at,
            revoked_by_id=assignment.revoked_by_id,
            revoked_at=assignment.revoked_at,
            revoke_reason=assignment.revoke_reason,
        )

    @staticmethod
    def _not_found() -> AppError:
        return AppError(404, "WORKLOAD_NOT_FOUND", "The workload was not found.")

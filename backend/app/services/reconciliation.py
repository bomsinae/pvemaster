from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError
from app.models.auth import User, UserRole
from app.models.cluster import Cluster
from app.models.inventory import FindingStatus, ReconciliationFinding
from app.models.operation import Workload
from app.models.scheduling import RunStatus, SyncRun
from app.schemas.inventory import (
    InventoryFreshnessResponse,
    ReconciliationFindingResponse,
    SyncRunResponse,
)
from app.security.access import Principal, require_service_role
from app.services.audit import add_audit_event

InventoryPublisher = Callable[[UUID], None]


async def create_sync_run(
    session: AsyncSession,
    *,
    cluster_id: UUID,
    triggered_by: str,
    requested_by_id: UUID | None = None,
    target_workload_id: UUID | None = None,
) -> tuple[SyncRun, bool]:
    cluster = await session.scalar(
        select(Cluster)
        .where(Cluster.id == cluster_id, Cluster.is_active.is_(True))
        .with_for_update()
    )
    if cluster is None:
        raise AppError(404, "CLUSTER_NOT_FOUND", "The cluster was not found.")
    if target_workload_id is not None:
        target_exists = await session.scalar(
            select(Workload.id).where(
                Workload.id == target_workload_id,
                Workload.cluster_id == cluster_id,
            )
        )
        if target_exists is None:
            await session.rollback()
            raise AppError(
                404,
                "WORKLOAD_NOT_FOUND",
                "The workload was not found in the selected cluster.",
            )
    scope = "TARGET" if target_workload_id is not None else "FULL"
    active_query = select(SyncRun).where(
        SyncRun.cluster_id == cluster_id,
        SyncRun.scope == scope,
        SyncRun.status.in_([RunStatus.QUEUED.value, RunStatus.RUNNING.value]),
    )
    if target_workload_id is not None:
        active_query = active_query.where(SyncRun.target_workload_id == target_workload_id)
    active = await session.scalar(active_query)
    if active is not None:
        await session.commit()
        return active, False
    generation = (
        int(
            (
                await session.scalar(
                    select(func.coalesce(func.max(SyncRun.generation), 0)).where(
                        SyncRun.cluster_id == cluster_id
                    )
                )
            )
            or 0
        )
        + 1
    )
    run = SyncRun(
        cluster_id=cluster_id,
        generation=generation,
        status=RunStatus.QUEUED.value,
        scope=scope,
        target_workload_id=target_workload_id,
        partial_failure=False,
        triggered_by=triggered_by,
        requested_by_id=requested_by_id,
        started_at=datetime.now(UTC),
        resource_counts={},
    )
    session.add(run)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        active = await session.scalar(active_query)
        if active is None:
            raise
        return active, False
    return run, True


class ReconciliationService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        settings: Settings,
        principal: Principal,
        publisher: InventoryPublisher,
        request_id: str,
    ) -> None:
        self._session = session
        self._settings = settings
        self._principal = principal
        self._publisher = publisher
        self._request_id = request_id
        require_service_role(principal, UserRole.SUPER_ADMIN, UserRole.OPERATOR)

    async def request_sync(
        self,
        cluster_id: UUID,
        *,
        triggered_by: str = "admin",
        target_workload_id: UUID | None = None,
    ) -> SyncRun:
        run, created = await create_sync_run(
            self._session,
            cluster_id=cluster_id,
            triggered_by=triggered_by,
            requested_by_id=self._principal.user_id,
            target_workload_id=target_workload_id,
        )
        if created:
            add_audit_event(
                self._session,
                action="INVENTORY_SYNC_REQUESTED",
                outcome="ATTEMPTED",
                request_id=self._request_id,
                actor_user_id=self._principal.user_id,
                actor_role=self._principal.role,
                target_type="cluster",
                target_id=cluster_id,
                after={"sync_run_id": str(run.id), "scope": run.scope},
            )
            await self._session.commit()
        if run.status == RunStatus.QUEUED.value:
            try:
                self._publisher(run.id)
            except Exception:
                # The periodic dispatcher republishes durable QUEUED runs.
                pass
        return run

    async def list_runs(
        self,
        *,
        cluster_id: UUID | None,
        limit: int,
    ) -> list[SyncRunResponse]:
        query = (
            select(SyncRun, Cluster.name)
            .join(Cluster, Cluster.id == SyncRun.cluster_id)
            .order_by(SyncRun.started_at.desc())
            .limit(limit)
        )
        if cluster_id is not None:
            query = query.where(SyncRun.cluster_id == cluster_id)
        rows = await self._session.execute(query)
        return [self._run_response(run, name) for run, name in rows.all()]

    async def get_run(self, run_id: UUID) -> SyncRunResponse:
        row = (
            await self._session.execute(
                select(SyncRun, Cluster.name)
                .join(Cluster, Cluster.id == SyncRun.cluster_id)
                .where(SyncRun.id == run_id)
            )
        ).one_or_none()
        if row is None:
            raise AppError(404, "SYNC_RUN_NOT_FOUND", "The inventory sync run was not found.")
        return self._run_response(*row)

    async def freshness(self) -> list[InventoryFreshnessResponse]:
        latest = (
            select(
                SyncRun.cluster_id,
                func.max(SyncRun.started_at).label("latest_started_at"),
            )
            .group_by(SyncRun.cluster_id)
            .subquery()
        )
        rows = await self._session.execute(
            select(Cluster, SyncRun.status)
            .outerjoin(latest, latest.c.cluster_id == Cluster.id)
            .outerjoin(
                SyncRun,
                (SyncRun.cluster_id == Cluster.id)
                & (SyncRun.started_at == latest.c.latest_started_at),
            )
            .where(Cluster.is_active.is_(True))
            .order_by(Cluster.name)
        )
        now = datetime.now(UTC)
        result: list[InventoryFreshnessResponse] = []
        for cluster, latest_status in rows.all():
            stale_after = max(
                self._settings.inventory_stale_after_seconds,
                cluster.sync_interval_seconds * 3,
            )
            stale = (
                cluster.last_sync_succeeded_at is None
                or cluster.last_sync_succeeded_at < now - timedelta(seconds=stale_after)
            )
            result.append(
                InventoryFreshnessResponse(
                    cluster_id=cluster.id,
                    cluster_name=cluster.name,
                    last_full_success_at=cluster.last_sync_succeeded_at,
                    stale_after_seconds=stale_after,
                    is_stale=stale,
                    stale_reason="LAST_FULL_SYNC_EXPIRED" if stale else None,
                    latest_status=latest_status,
                )
            )
        return result

    async def list_findings(
        self,
        *,
        cluster_id: UUID | None,
        status: str | None,
        severity: str | None,
        limit: int,
    ) -> list[ReconciliationFindingResponse]:
        query = (
            select(ReconciliationFinding, Cluster.name)
            .join(Cluster, Cluster.id == ReconciliationFinding.cluster_id)
            .order_by(
                ReconciliationFinding.last_observed_at.desc(),
                ReconciliationFinding.id,
            )
            .limit(limit)
        )
        if cluster_id is not None:
            query = query.where(ReconciliationFinding.cluster_id == cluster_id)
        if status is not None:
            query = query.where(ReconciliationFinding.status == status)
        if severity is not None:
            query = query.where(ReconciliationFinding.severity == severity)
        rows = await self._session.execute(query)
        return [self._finding_response(item, name) for item, name in rows.all()]

    async def get_finding(self, finding_id: UUID) -> ReconciliationFindingResponse:
        finding, cluster_name = await self._finding(finding_id)
        return self._finding_response(finding, cluster_name)

    async def acknowledge(
        self,
        finding_id: UUID,
        *,
        assigned_to_id: UUID | None,
    ) -> ReconciliationFindingResponse:
        finding, cluster_name = await self._finding(finding_id, lock=True)
        if finding.status == FindingStatus.RESOLVED.value:
            raise AppError(409, "FINDING_ALREADY_RESOLVED", "The finding is already resolved.")
        if assigned_to_id is not None:
            assignee = await self._session.get(User, assigned_to_id)
            if (
                assignee is None
                or not assignee.is_active
                or assignee.role not in {UserRole.SUPER_ADMIN.value, UserRole.OPERATOR.value}
            ):
                raise AppError(404, "ASSIGNEE_NOT_FOUND", "The assignee was not found.")
        now = datetime.now(UTC)
        finding.status = FindingStatus.ACKNOWLEDGED.value
        finding.acknowledged_by_id = self._principal.user_id
        finding.acknowledged_at = now
        finding.assigned_to_id = assigned_to_id
        self._audit_finding("RECONCILIATION_FINDING_ACKNOWLEDGED", finding)
        await self._session.commit()
        return self._finding_response(finding, cluster_name)

    async def resolve(
        self,
        finding_id: UUID,
        *,
        resolution_note: str,
    ) -> ReconciliationFindingResponse:
        finding, cluster_name = await self._finding(finding_id, lock=True)
        if finding.status != FindingStatus.RESOLVED.value:
            finding.status = FindingStatus.RESOLVED.value
            finding.resolved_by_id = self._principal.user_id
            finding.resolved_at = datetime.now(UTC)
            finding.resolution_note = resolution_note.strip()
            self._audit_finding("RECONCILIATION_FINDING_RESOLVED", finding)
            await self._session.commit()
        return self._finding_response(finding, cluster_name)

    async def _finding(
        self,
        finding_id: UUID,
        *,
        lock: bool = False,
    ) -> tuple[ReconciliationFinding, str]:
        query = (
            select(ReconciliationFinding, Cluster.name)
            .join(Cluster, Cluster.id == ReconciliationFinding.cluster_id)
            .where(ReconciliationFinding.id == finding_id)
        )
        if lock:
            query = query.with_for_update()
        row = (await self._session.execute(query)).one_or_none()
        if row is None:
            raise AppError(
                404,
                "RECONCILIATION_FINDING_NOT_FOUND",
                "The reconciliation finding was not found.",
            )
        return row[0], row[1]

    def _audit_finding(self, action: str, finding: ReconciliationFinding) -> None:
        add_audit_event(
            self._session,
            action=action,
            outcome="SUCCEEDED",
            request_id=self._request_id,
            actor_user_id=self._principal.user_id,
            actor_role=self._principal.role,
            workload_id=finding.workload_id,
            target_type="reconciliation_finding",
            target_id=finding.id,
            after={"status": finding.status, "assigned": finding.assigned_to_id is not None},
        )

    @staticmethod
    def _run_response(run: SyncRun, cluster_name: str) -> SyncRunResponse:
        duration_ms = (
            int((run.finished_at - run.started_at).total_seconds() * 1000)
            if run.finished_at is not None
            else None
        )
        return SyncRunResponse(
            id=run.id,
            operation_id=run.id,
            cluster_id=run.cluster_id,
            cluster_name=cluster_name,
            generation=run.generation,
            scope=run.scope,
            status=run.status,
            partial_failure=run.partial_failure,
            triggered_by=run.triggered_by,
            requested_by_id=run.requested_by_id,
            target_workload_id=run.target_workload_id,
            started_at=run.started_at,
            finished_at=run.finished_at,
            duration_ms=duration_ms,
            error_code=run.error_code,
            resource_counts=run.resource_counts,
        )

    @staticmethod
    def _finding_response(
        finding: ReconciliationFinding,
        cluster_name: str,
    ) -> ReconciliationFindingResponse:
        return ReconciliationFindingResponse(
            id=finding.id,
            kind=finding.kind,
            severity=finding.severity,
            status=finding.status,
            cluster_id=finding.cluster_id,
            cluster_name=cluster_name,
            workload_id=finding.workload_id,
            sync_run_id=finding.sync_run_id,
            target_type=finding.target_type,
            target_id=finding.target_id,
            summary=finding.summary,
            details=finding.details,
            first_observed_at=finding.first_observed_at,
            last_observed_at=finding.last_observed_at,
            acknowledged_by_id=finding.acknowledged_by_id,
            acknowledged_at=finding.acknowledged_at,
            assigned_to_id=finding.assigned_to_id,
            resolved_by_id=finding.resolved_by_id,
            resolved_at=finding.resolved_at,
            resolution_note=finding.resolution_note,
        )

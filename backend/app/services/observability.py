import ipaddress
from collections import defaultdict
from collections.abc import Awaitable
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

from redis.asyncio import Redis
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.config import Settings
from app.models.auth import AuditLog, Organization, User, UserRole
from app.models.backup import BackupPolicy, BackupRun, BackupVerification
from app.models.cluster import Cluster
from app.models.inventory import (
    FindingSeverity,
    FindingStatus,
    ReconciliationFinding,
    WorkloadChangeEvent,
)
from app.models.ipam import IpAddress, IpAddressState, IpPool, IpPoolExclusion
from app.models.operation import Operation, Workload
from app.models.provisioning import ProvisioningRequest, ProvisioningStatus
from app.models.scheduling import MaintenanceRun, RunStatus
from app.schemas.observability import (
    AuditLogListResponse,
    AuditLogResponse,
    ClusterConnectionStatus,
    DirectoryCountStatus,
    DirectoryInventoryStatus,
    OperationalAlert,
    OperationsStatusResponse,
    QueueStatus,
    SchedulerJobStatus,
    WorkerStatus,
    WorkloadInventoryStatus,
)
from app.security.access import Principal, require_service_role
from app.services.audit import add_audit_event


class ObservabilityService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        redis: Redis,
        settings: Settings,
        principal: Principal | None = None,
    ) -> None:
        self._session = session
        self._redis = redis
        self._settings = settings
        self._principal = principal

    async def audit_logs(
        self,
        *,
        action: str | None,
        actor_user_id: UUID | None,
        organization_id: UUID | None,
        result: str | None,
        limit: int,
        offset: int,
    ) -> AuditLogListResponse:
        self._require(UserRole.SUPER_ADMIN)
        filters = []
        if action:
            filters.append(AuditLog.action == action)
        if actor_user_id:
            filters.append(AuditLog.actor_user_id == actor_user_id)
        if organization_id:
            filters.append(AuditLog.organization_id == organization_id)
        if result:
            filters.append(AuditLog.result == result)
        total = await self._session.scalar(
            select(func.count()).select_from(AuditLog).where(*filters)
        )
        rows = await self._session.execute(
            self._audit_statement()
            .where(*filters)
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .limit(limit)
            .offset(offset)
        )
        response = AuditLogListResponse(
            items=[self._audit_response(*row) for row in rows.all()],
            total=total or 0,
            limit=limit,
            offset=offset,
        )
        assert self._principal is not None
        add_audit_event(
            self._session,
            action="AUDIT_LOG_SEARCH",
            outcome="SUCCEEDED",
            request_id=None,
            actor_user_id=self._principal.user_id,
            actor_role=self._principal.role,
            target_type="audit_log",
            after={
                "filters": {
                    "action": action,
                    "actor_user_id": str(actor_user_id) if actor_user_id else None,
                    "organization_id": str(organization_id) if organization_id else None,
                    "result": result,
                },
                "returned": len(response.items),
            },
        )
        await self._session.commit()
        return response

    async def audit_log(self, audit_id: UUID) -> AuditLogResponse:
        self._require(UserRole.SUPER_ADMIN)
        row = (
            await self._session.execute(self._audit_statement().where(AuditLog.id == audit_id))
        ).one_or_none()
        if row is None:
            from app.core.errors import AppError

            raise AppError(404, "AUDIT_LOG_NOT_FOUND", "The audit log was not found.")
        item = row[0]
        response = self._audit_response(*row)
        assert self._principal is not None
        add_audit_event(
            self._session,
            action="AUDIT_LOG_VIEW",
            outcome="SUCCEEDED",
            request_id=None,
            actor_user_id=self._principal.user_id,
            actor_role=self._principal.role,
            target_type="audit_log",
            target_id=item.id,
        )
        await self._session.commit()
        return response

    @staticmethod
    def _audit_statement() -> Select[
        tuple[
            AuditLog,
            str,
            str,
            str | None,
            int,
            str,
            str,
            str,
        ]
    ]:
        actor = aliased(User)
        return (
            select(
                AuditLog,
                actor.display_name,
                actor.email,
                Workload.name,
                Workload.vmid,
                Workload.kind,
                Workload.node,
                Cluster.name,
            )
            .outerjoin(actor, AuditLog.actor_user_id == actor.id)
            .outerjoin(Workload, AuditLog.workload_id == Workload.id)
            .outerjoin(Cluster, Workload.cluster_id == Cluster.id)
        )

    @staticmethod
    def _audit_response(
        item: AuditLog,
        actor_display_name: str | None,
        actor_email: str | None,
        workload_name: str | None,
        workload_vmid: int | None,
        workload_kind: str | None,
        workload_node: str | None,
        workload_cluster_name: str | None,
    ) -> AuditLogResponse:
        return AuditLogResponse.model_validate(item).model_copy(
            update={
                "actor_display_name": actor_display_name,
                "actor_email": actor_email,
                "workload_name": workload_name,
                "workload_vmid": workload_vmid,
                "workload_kind": workload_kind,
                "workload_node": workload_node,
                "workload_cluster_name": workload_cluster_name,
            }
        )

    async def status(self) -> OperationsStatusResponse:
        self._require(UserRole.SUPER_ADMIN, UserRole.OPERATOR)
        worker = await self._worker_status()
        queue = await self._queue_status()
        workloads = await self._workload_inventory()
        directory = await self._directory_inventory()
        clusters = await self._cluster_statuses()
        scheduler = await self._scheduler_status()
        open_findings = await self._open_finding_count()
        stale_clusters = await self._stale_cluster_count()
        alerts = await self._alerts(worker, queue, clusters, scheduler)
        return OperationsStatusResponse(
            status="degraded" if alerts else "ok",
            worker=worker,
            queue=queue,
            workloads=workloads,
            directory=directory,
            clusters=clusters,
            scheduler=scheduler,
            open_reconciliation_findings=open_findings,
            stale_inventory_clusters=stale_clusters,
            alerts=alerts,
        )

    async def evaluate_alerts(self) -> list[OperationalAlert]:
        """Compute the current control-plane signals without requiring a dashboard principal."""
        worker = await self._worker_status()
        queue = await self._queue_status()
        clusters = await self._cluster_statuses()
        scheduler = await self._scheduler_status()
        return await self._alerts(worker, queue, clusters, scheduler)

    async def prometheus_metrics(self) -> str:
        worker = await self._worker_status()
        queue = await self._queue_status()
        clusters = await self._cluster_statuses()
        lines = [
            "# HELP pvemaster_worker_up Whether at least one Celery worker heartbeat is current.",
            "# TYPE pvemaster_worker_up gauge",
            f"pvemaster_worker_up {int(worker.alive)}",
            "# HELP pvemaster_job_queue_length Number of jobs waiting in a Celery queue.",
            "# TYPE pvemaster_job_queue_length gauge",
        ]
        for name, length in sorted(queue.queues.items()):
            lines.append(f'pvemaster_job_queue_length{{queue="{name}"}} {length}')
        lines.extend(
            [
                "# HELP pvemaster_cluster_connection_up Last known Proxmox connection state.",
                "# TYPE pvemaster_cluster_connection_up gauge",
            ]
        )
        for cluster in clusters:
            name = cluster.name.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
            labels = f'cluster_id="{cluster.cluster_id}",name="{name}"'
            lines.append(f"pvemaster_cluster_connection_up{{{labels}}} {int(cluster.connected)}")
        operation_counts = await self._session.execute(
            select(Operation.status, func.count()).group_by(Operation.status)
        )
        lines.extend(
            [
                "# HELP pvemaster_operations Number of operation records by status.",
                "# TYPE pvemaster_operations gauge",
            ]
        )
        for status, count in operation_counts:
            lines.append(f'pvemaster_operations{{status="{status}"}} {count}')
        finding_counts = await self._session.execute(
            select(
                ReconciliationFinding.status,
                ReconciliationFinding.severity,
                func.count(),
            ).group_by(
                ReconciliationFinding.status,
                ReconciliationFinding.severity,
            )
        )
        lines.extend(
            [
                "# HELP pvemaster_reconciliation_findings Number of drift findings.",
                "# TYPE pvemaster_reconciliation_findings gauge",
            ]
        )
        for status, severity, count in finding_counts:
            lines.append(
                "pvemaster_reconciliation_findings"
                f'{{status="{status}",severity="{severity}"}} {count}'
            )
        lines.extend(
            [
                "# HELP pvemaster_inventory_stale_clusters Number of active clusters "
                "without a recent full inventory sync.",
                "# TYPE pvemaster_inventory_stale_clusters gauge",
                f"pvemaster_inventory_stale_clusters {await self._stale_cluster_count()}",
            ]
        )
        scheduler = await self._scheduler_status()
        lines.extend(
            [
                "# HELP pvemaster_scheduler_job_last_success_timestamp_seconds "
                "Unix timestamp of the last successful scheduled job run.",
                "# TYPE pvemaster_scheduler_job_last_success_timestamp_seconds gauge",
                "# HELP pvemaster_scheduler_job_failed Whether the latest scheduled job failed.",
                "# TYPE pvemaster_scheduler_job_failed gauge",
            ]
        )
        for job in scheduler:
            name = job.job_name.replace("\\", "\\\\").replace('"', '\\"')
            success_timestamp = (
                int(job.last_success_at.timestamp()) if job.last_success_at is not None else 0
            )
            lines.append(
                "pvemaster_scheduler_job_last_success_timestamp_seconds"
                f'{{job="{name}"}} {success_timestamp}'
            )
            lines.append(
                f'pvemaster_scheduler_job_failed{{job="{name}"}} '
                f"{int(job.status == RunStatus.FAILED.value)}"
            )
        pool_counts = await self._ip_pool_counts()
        lines.extend(
            [
                "# HELP pvemaster_ip_pool_available Number of available addresses in an IP pool.",
                "# TYPE pvemaster_ip_pool_available gauge",
            ]
        )
        for pool_id, _name, available in pool_counts:
            lines.append(f'pvemaster_ip_pool_available{{pool_id="{pool_id}"}} {available}')
        return "\n".join(lines) + "\n"

    async def _worker_status(self) -> WorkerStatus:
        workers: list[str] = []
        try:
            async for raw_key in self._redis.scan_iter(match="pvemaster:worker:heartbeat:*"):
                key = raw_key.decode() if isinstance(raw_key, bytes) else str(raw_key)
                workers.append(key.rsplit(":", 1)[-1])
        except Exception:
            return WorkerStatus(
                available=False,
                alive=False,
                workers=[],
                stale_after_seconds=self._settings.worker_heartbeat_ttl_seconds,
            )
        return WorkerStatus(
            alive=bool(workers),
            workers=sorted(workers),
            stale_after_seconds=self._settings.worker_heartbeat_ttl_seconds,
        )

    async def _queue_status(self) -> QueueStatus:
        try:
            queues = {
                "operations": int(await cast(Awaitable[int], self._redis.llen("operations"))),
                "inventory": int(await cast(Awaitable[int], self._redis.llen("inventory"))),
                "maintenance": int(await cast(Awaitable[int], self._redis.llen("maintenance"))),
                "celery": int(await cast(Awaitable[int], self._redis.llen("celery"))),
            }
        except Exception:
            return QueueStatus(
                available=False,
                total=0,
                queues={},
                backlog_threshold=self._settings.queue_backlog_alert_threshold,
            )
        return QueueStatus(
            total=sum(queues.values()),
            queues=queues,
            backlog_threshold=self._settings.queue_backlog_alert_threshold,
        )

    async def _cluster_statuses(self) -> list[ClusterConnectionStatus]:
        rows = await self._session.scalars(
            select(Cluster).where(Cluster.is_active.is_(True)).order_by(Cluster.name)
        )
        return [
            ClusterConnectionStatus(
                cluster_id=item.id,
                name=item.name,
                enabled=item.is_active,
                connected=item.is_active
                and item.last_connection_error_code is None
                and item.last_connected_at is not None,
                last_connected_at=item.last_connected_at,
                error_code=item.last_connection_error_code,
            )
            for item in rows.all()
        ]

    async def _workload_inventory(self) -> WorkloadInventoryStatus:
        base_query = (
            Workload.is_present.is_(True),
            Workload.is_template.is_(False),
        )
        total = await self._session.scalar(
            select(func.count())
            .select_from(Workload)
            .join(Cluster, Cluster.id == Workload.cluster_id)
            .where(Cluster.is_active.is_(True), *base_query)
        )
        assigned = await self._session.scalar(
            select(func.count())
            .select_from(Workload)
            .join(Cluster, Cluster.id == Workload.cluster_id)
            .where(
                Cluster.is_active.is_(True),
                *base_query,
                Workload.organization_id.is_not(None),
            )
        )
        total_count = int(total or 0)
        assigned_count = int(assigned or 0)
        return WorkloadInventoryStatus(
            total=total_count,
            assigned=assigned_count,
            unassigned=max(0, total_count - assigned_count),
        )

    async def _directory_inventory(self) -> DirectoryInventoryStatus:
        user_total = await self._session.scalar(
            select(func.count()).select_from(User).where(User.deleted_at.is_(None))
        )
        active_users = await self._session.scalar(
            select(func.count())
            .select_from(User)
            .where(User.deleted_at.is_(None), User.is_active.is_(True))
        )
        organization_total = await self._session.scalar(
            select(func.count()).select_from(Organization)
        )
        active_organizations = await self._session.scalar(
            select(func.count()).select_from(Organization).where(Organization.is_active.is_(True))
        )
        return DirectoryInventoryStatus(
            users=DirectoryCountStatus(
                total=int(user_total or 0),
                active=int(active_users or 0),
            ),
            organizations=DirectoryCountStatus(
                total=int(organization_total or 0),
                active=int(active_organizations or 0),
            ),
        )

    async def _alerts(
        self,
        worker: WorkerStatus,
        queue: QueueStatus,
        clusters: list[ClusterConnectionStatus],
        scheduler: list[SchedulerJobStatus] | None = None,
    ) -> list[OperationalAlert]:
        alerts: list[OperationalAlert] = []
        if not worker.available or not queue.available:
            alerts.append(
                OperationalAlert(
                    code="REDIS_UNAVAILABLE",
                    severity="critical",
                    resource_type="redis",
                    message="Worker and queue state could not be read from Redis.",
                )
            )
        if not worker.alive:
            alerts.append(
                OperationalAlert(
                    code="WORKER_DOWN",
                    severity="critical",
                    resource_type="worker",
                    message="No current worker heartbeat was found.",
                )
            )
        if queue.total >= queue.backlog_threshold:
            alerts.append(
                OperationalAlert(
                    code="JOB_QUEUE_BACKLOG",
                    severity="warning",
                    resource_type="queue",
                    message="The job queue backlog threshold was reached.",
                    value=queue.total,
                    threshold=queue.backlog_threshold,
                )
            )
        for cluster in clusters:
            if cluster.enabled and not cluster.connected:
                alerts.append(
                    OperationalAlert(
                        code="CLUSTER_CONNECTION_FAILED",
                        severity="critical",
                        resource_type="cluster",
                        resource_id=str(cluster.cluster_id),
                        message="The Proxmox cluster is not connected.",
                    )
                )
                if cluster.error_code in {"PVE_AUTH_FAILED", "PVE_PERMISSION_DENIED"}:
                    alerts.append(
                        OperationalAlert(
                            code="CLUSTER_CREDENTIAL_REJECTED",
                            severity="critical",
                            resource_type="cluster",
                            resource_id=str(cluster.cluster_id),
                            message="The cluster credential was rejected or lacks permission.",
                        )
                    )
        critical_findings = await self._session.scalar(
            select(func.count())
            .select_from(ReconciliationFinding)
            .where(
                ReconciliationFinding.status != FindingStatus.RESOLVED.value,
                ReconciliationFinding.severity == FindingSeverity.CRITICAL.value,
            )
        )
        if critical_findings:
            alerts.append(
                OperationalAlert(
                    code="RECONCILIATION_CRITICAL",
                    severity="critical",
                    resource_type="inventory",
                    message="Critical inventory drift findings require review.",
                    value=int(critical_findings),
                )
            )
        stale_clusters = await self._stale_cluster_count()
        if stale_clusters:
            alerts.append(
                OperationalAlert(
                    code="INVENTORY_STALE",
                    severity="critical",
                    resource_type="inventory",
                    message="One or more clusters have stale inventory.",
                    value=stale_clusters,
                )
            )
        for job in scheduler or []:
            if job.status == RunStatus.FAILED.value:
                alerts.append(
                    OperationalAlert(
                        code="SCHEDULER_JOB_FAILED",
                        severity="critical",
                        resource_type="scheduler",
                        resource_id=job.job_name,
                        message="A scheduled maintenance job failed.",
                    )
                )
        cutoff = datetime.now(UTC) - timedelta(
            minutes=self._settings.provisioning_failure_window_minutes
        )
        failures = await self._session.scalar(
            select(func.count())
            .select_from(ProvisioningRequest)
            .where(
                ProvisioningRequest.status.in_(
                    [ProvisioningStatus.FAILED.value, ProvisioningStatus.MANUAL_REVIEW.value]
                ),
                ProvisioningRequest.finished_at >= cutoff,
            )
        )
        if (failures or 0) >= self._settings.provisioning_failure_alert_count:
            alerts.append(
                OperationalAlert(
                    code="PROVISIONING_REPEATED_FAILURE",
                    severity="critical",
                    resource_type="provisioning",
                    message="Provisioning failures exceeded the configured window threshold.",
                    value=failures or 0,
                    threshold=self._settings.provisioning_failure_alert_count,
                )
            )
        for pool_id, name, available in await self._ip_pool_counts():
            if available <= self._settings.ip_pool_low_available_threshold:
                alerts.append(
                    OperationalAlert(
                        code="IP_POOL_LOW",
                        severity="warning",
                        resource_type="ip_pool",
                        resource_id=str(pool_id),
                        message=f"IP pool {name} has low availability.",
                        value=available,
                        threshold=self._settings.ip_pool_low_available_threshold,
                    )
                )
        alerts.extend(await self._incident_alerts())
        return alerts

    async def _incident_alerts(self) -> list[OperationalAlert]:
        incident_cutoff = datetime.now(UTC) - timedelta(hours=24)
        alerts: list[OperationalAlert] = []
        failed_operations = (
            await self._session.execute(
                select(Operation, Workload.organization_id)
                .outerjoin(Workload, Workload.id == Operation.workload_id)
                .where(
                    Operation.status.in_(
                        [
                            "FAILED",
                            "TIMEOUT",
                            "NEEDS_ATTENTION",
                        ]
                    ),
                    Operation.requested_at >= incident_cutoff,
                )
            )
        ).all()
        for operation, organization_id in failed_operations:
            alerts.append(
                OperationalAlert(
                    code="OPERATION_REQUIRES_ATTENTION",
                    severity="critical" if operation.status == "NEEDS_ATTENTION" else "warning",
                    resource_type="operation",
                    resource_id=str(operation.id),
                    organization_id=organization_id,
                    workload_id=operation.workload_id,
                    message="An operation failed or requires manual review.",
                )
            )
        failed_backups = (
            await self._session.scalars(
                select(BackupRun).where(
                    BackupRun.status.in_(["FAILED", "TIMEOUT", "NEEDS_ATTENTION"]),
                    BackupRun.created_at >= incident_cutoff,
                )
            )
        ).all()
        for backup in failed_backups:
            alerts.append(
                OperationalAlert(
                    code="BACKUP_FAILED",
                    severity="critical",
                    resource_type="backup",
                    resource_id=str(backup.id),
                    organization_id=backup.organization_id,
                    workload_id=backup.workload_id,
                    message="A workload backup failed or needs review.",
                )
            )
        missed_policies = (
            await self._session.scalars(
                select(BackupPolicy).where(
                    BackupPolicy.is_enabled.is_(True),
                    BackupPolicy.next_run_at < datetime.now(UTC) - timedelta(minutes=10),
                )
            )
        ).all()
        for policy in missed_policies:
            alerts.append(
                OperationalAlert(
                    code="BACKUP_SCHEDULE_MISSED",
                    severity="critical",
                    resource_type="backup_policy",
                    resource_id=str(policy.id),
                    message="An enabled backup policy missed its scheduled dispatch.",
                )
            )
        due_verifications = (
            await self._session.scalars(
                select(BackupVerification).where(
                    BackupVerification.status.in_(["DUE", "FAILED"])
                )
            )
        ).all()
        for verification in due_verifications:
            alerts.append(
                OperationalAlert(
                    code="BACKUP_VERIFICATION_DUE",
                    severity="warning" if verification.status == "DUE" else "critical",
                    resource_type="backup_verification",
                    resource_id=str(verification.id),
                    message="A backup restore verification is due or failed.",
                )
            )
        change_cutoff = datetime.now(UTC) - timedelta(minutes=10)
        change_events = (
            await self._session.execute(
                select(WorkloadChangeEvent, Workload.organization_id)
                .join(Workload, Workload.id == WorkloadChangeEvent.workload_id)
                .where(
                    WorkloadChangeEvent.observed_at >= change_cutoff,
                    WorkloadChangeEvent.kind.in_(["POWER_STATE_CHANGED", "MISSING"]),
                    Workload.organization_id.is_not(None),
                )
            )
        ).all()
        for event, organization_id in change_events:
            alerts.append(
                OperationalAlert(
                    code="CUSTOMER_VM_STATE_CHANGED",
                    severity="info",
                    resource_type="workload",
                    resource_id=str(event.workload_id),
                    organization_id=organization_id,
                    workload_id=event.workload_id,
                    message="A managed VM state changed.",
                )
            )
        return alerts

    async def _open_finding_count(self) -> int:
        count = await self._session.scalar(
            select(func.count())
            .select_from(ReconciliationFinding)
            .where(ReconciliationFinding.status != FindingStatus.RESOLVED.value)
        )
        return int(count or 0)

    async def _stale_cluster_count(self) -> int:
        now = datetime.now(UTC)
        clusters = await self._session.scalars(select(Cluster).where(Cluster.is_active.is_(True)))
        return sum(
            1
            for cluster in clusters
            if cluster.last_sync_succeeded_at is None
            or cluster.last_sync_succeeded_at
            < now
            - timedelta(
                seconds=max(
                    self._settings.inventory_stale_after_seconds,
                    cluster.sync_interval_seconds * 3,
                )
            )
        )

    async def _scheduler_status(self) -> list[SchedulerJobStatus]:
        rows = (
            await self._session.scalars(
                select(MaintenanceRun).order_by(
                    MaintenanceRun.started_at.desc(),
                    MaintenanceRun.id.desc(),
                )
            )
        ).all()
        latest: dict[str, MaintenanceRun] = {}
        last_success: dict[str, datetime] = {}
        for item in rows:
            latest.setdefault(item.job_name, item)
            if (
                item.status == RunStatus.SUCCEEDED.value
                and item.finished_at is not None
                and item.job_name not in last_success
            ):
                last_success[item.job_name] = item.finished_at
        return [
            SchedulerJobStatus(
                job_name=name,
                status=item.status,
                last_started_at=item.started_at,
                last_finished_at=item.finished_at,
                last_success_at=last_success.get(name),
                processed_count=item.processed_count,
                error_code=item.error_code,
            )
            for name, item in sorted(latest.items())
        ]

    async def _ip_pool_counts(self) -> list[tuple[UUID, str, int]]:
        pools = list(
            await self._session.scalars(
                select(IpPool).where(IpPool.is_active.is_(True)).order_by(IpPool.name)
            )
        )
        if not pools:
            return []

        pool_ids = [pool.id for pool in pools]
        exclusion_rows = await self._session.execute(
            select(
                IpPoolExclusion.pool_id,
                IpPoolExclusion.start_address,
                IpPoolExclusion.end_address,
            ).where(IpPoolExclusion.pool_id.in_(pool_ids))
        )
        exclusions: defaultdict[UUID, list[tuple[str, str]]] = defaultdict(list)
        for pool_id, start, end in exclusion_rows:
            exclusions[pool_id].append((str(start), str(end)))

        unavailable_rows = await self._session.execute(
            select(IpAddress.pool_id, IpAddress.address).where(
                IpAddress.pool_id.in_(pool_ids),
                IpAddress.state != IpAddressState.AVAILABLE.value,
            )
        )
        unavailable: defaultdict[UUID, list[str]] = defaultdict(list)
        for pool_id, address in unavailable_rows:
            unavailable[pool_id].append(str(address))

        return [
            (
                pool.id,
                pool.name,
                self._available_address_count(
                    cidr=str(pool.cidr),
                    gateway=str(pool.gateway) if pool.gateway is not None else None,
                    exclusions=exclusions[pool.id],
                    unavailable=unavailable[pool.id],
                ),
            )
            for pool in pools
        ]

    @staticmethod
    def _available_address_count(
        *,
        cidr: str,
        gateway: str | None,
        exclusions: list[tuple[str, str]],
        unavailable: list[str],
    ) -> int:
        network = ipaddress.ip_network(cidr, strict=True)
        first = int(network.network_address)
        last = int(network.broadcast_address)
        blocked: list[tuple[int, int]] = [(first, first)]
        if isinstance(network, ipaddress.IPv4Network):
            blocked.append((last, last))
        if gateway is not None:
            value = int(ipaddress.ip_address(gateway))
            blocked.append((value, value))
        blocked.extend(
            (int(ipaddress.ip_address(start)), int(ipaddress.ip_address(end)))
            for start, end in exclusions
        )
        blocked.extend(
            (value, value)
            for value in (int(ipaddress.ip_address(address)) for address in unavailable)
        )

        merged: list[tuple[int, int]] = []
        for low, high in sorted(blocked):
            low, high = max(first, low), min(last, high)
            if low > high:
                continue
            if merged and low <= merged[-1][1] + 1:
                merged[-1] = (merged[-1][0], max(merged[-1][1], high))
            else:
                merged.append((low, high))
        blocked_count = sum(high - low + 1 for low, high in merged)
        return max(0, network.num_addresses - blocked_count)

    def _require(self, *roles: UserRole) -> None:
        if self._principal is None:
            raise RuntimeError("an authenticated principal is required")
        require_service_role(self._principal, *roles)

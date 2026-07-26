from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models.auth import Organization
from app.models.backup import BackupRun
from app.models.ipam import IpAllocation
from app.models.operation import Workload
from app.models.organization_governance import OrganizationQuota, QuotaReservation

DEFAULT_QUOTA_LIMITS = {
    "vcpu": 64,
    "memory_bytes": 512 * 1024**3,
    "disk_bytes": 16 * 1024**4,
    "vms": 20,
    "ips": 64,
    "backup_bytes": 64 * 1024**4,
}
QUOTA_KEYS = tuple(DEFAULT_QUOTA_LIMITS)


async def quota_state(
    session: AsyncSession,
    organization_id: UUID,
    *,
    lock: bool,
) -> tuple[dict[str, int], dict[str, int], dict[str, int]]:
    organization_query = select(Organization).where(Organization.id == organization_id)
    if lock:
        organization_query = organization_query.with_for_update()
    organization = await session.scalar(organization_query)
    if organization is None or not organization.is_active:
        raise AppError(404, "ORGANIZATION_NOT_FOUND", "The organization was not found.")
    quota_query = select(OrganizationQuota).where(
        OrganizationQuota.organization_id == organization_id
    )
    if lock:
        quota_query = quota_query.with_for_update()
    quota = await session.scalar(quota_query.execution_options(populate_existing=True))
    limits = {
        "vcpu": quota.max_vcpu if quota else DEFAULT_QUOTA_LIMITS["vcpu"],
        "memory_bytes": (
            quota.max_memory_bytes if quota else DEFAULT_QUOTA_LIMITS["memory_bytes"]
        ),
        "disk_bytes": quota.max_disk_bytes if quota else DEFAULT_QUOTA_LIMITS["disk_bytes"],
        "vms": quota.max_vms if quota else DEFAULT_QUOTA_LIMITS["vms"],
        "ips": quota.max_ips if quota else DEFAULT_QUOTA_LIMITS["ips"],
        "backup_bytes": (
            quota.max_backup_bytes if quota else DEFAULT_QUOTA_LIMITS["backup_bytes"]
        ),
    }
    used_vcpu, used_memory, used_disk, used_vms = (
        await session.execute(
            select(
                func.coalesce(func.sum(Workload.cpu_cores), 0),
                func.coalesce(func.sum(Workload.memory_bytes), 0),
                func.coalesce(func.sum(Workload.disk_bytes), 0),
                func.count(Workload.id),
            ).where(
                Workload.organization_id == organization_id,
                Workload.is_present.is_(True),
                Workload.is_template.is_(False),
            )
        )
    ).one()
    used_ips = await session.scalar(
        select(func.count(IpAllocation.id))
        .join(Workload, Workload.id == IpAllocation.workload_id)
        .where(
            Workload.organization_id == organization_id,
            IpAllocation.status.in_(["RESERVED", "ASSIGNED", "QUARANTINED"]),
        )
    )
    used_backup = await session.scalar(
        select(func.coalesce(func.sum(BackupRun.size_bytes), 0)).where(
            BackupRun.organization_id == organization_id,
            BackupRun.status == "SUCCEEDED",
        )
    )
    usage = {
        "vcpu": int(used_vcpu or 0),
        "memory_bytes": int(used_memory or 0),
        "disk_bytes": int(used_disk or 0),
        "vms": int(used_vms or 0),
        "ips": int(used_ips or 0),
        "backup_bytes": int(used_backup or 0),
    }
    reserved_row = (
        await session.execute(
            select(
                func.coalesce(func.sum(QuotaReservation.vcpu), 0),
                func.coalesce(func.sum(QuotaReservation.memory_bytes), 0),
                func.coalesce(func.sum(QuotaReservation.disk_bytes), 0),
                func.coalesce(func.sum(QuotaReservation.vms), 0),
                func.coalesce(func.sum(QuotaReservation.ips), 0),
                func.coalesce(func.sum(QuotaReservation.backup_bytes), 0),
            ).where(
                QuotaReservation.organization_id == organization_id,
                QuotaReservation.status == "ACTIVE",
            )
        )
    ).one()
    reserved = {
        key: int(value or 0)
        for key, value in zip(QUOTA_KEYS, reserved_row, strict=True)
    }
    return limits, usage, reserved


async def reserve_quota(
    session: AsyncSession,
    organization_id: UUID,
    *,
    provisioning_request_id: UUID | None = None,
    service_request_id: UUID | None = None,
    vcpu: int = 0,
    memory_bytes: int = 0,
    disk_bytes: int = 0,
    vms: int = 0,
    ips: int = 0,
    backup_bytes: int = 0,
) -> QuotaReservation:
    if (provisioning_request_id is None) == (service_request_id is None):
        raise ValueError("exactly one quota reservation request is required")
    requested = {
        "vcpu": vcpu,
        "memory_bytes": memory_bytes,
        "disk_bytes": disk_bytes,
        "vms": vms,
        "ips": ips,
        "backup_bytes": backup_bytes,
    }
    if any(value < 0 for value in requested.values()):
        raise ValueError("quota reservations cannot be negative")
    limits, usage, reserved = await quota_state(session, organization_id, lock=True)
    request_filter = (
        QuotaReservation.provisioning_request_id == provisioning_request_id
        if provisioning_request_id is not None
        else QuotaReservation.service_request_id == service_request_id
    )
    existing = await session.scalar(
        select(QuotaReservation).where(request_filter).with_for_update()
    )
    committed_reserved = dict(reserved)
    if existing is not None and existing.status == "ACTIVE":
        for key in QUOTA_KEYS:
            committed_reserved[key] -= int(getattr(existing, key))
    exceeded = [
        key
        for key in QUOTA_KEYS
        if usage[key] + committed_reserved[key] + requested[key] > limits[key]
    ]
    if exceeded:
        raise AppError(
            409,
            "ORGANIZATION_QUOTA_EXCEEDED",
            "The request exceeds the organization quota.",
            details={
                "resources": exceeded,
                "limits": {key: limits[key] for key in exceeded},
                "committed": {
                    key: usage[key] + committed_reserved[key] for key in exceeded
                },
            },
        )
    if existing is not None:
        if existing.status == "CONSUMED":
            raise AppError(
                409,
                "QUOTA_RESERVATION_FINALIZED",
                "The quota reservation was already consumed.",
            )
        for key, value in requested.items():
            setattr(existing, key, value)
        existing.status = "ACTIVE"
        existing.finished_at = None
        await session.flush([existing])
        return existing
    item = QuotaReservation(
        id=uuid4(),
        organization_id=organization_id,
        provisioning_request_id=provisioning_request_id,
        service_request_id=service_request_id,
        status="ACTIVE",
        **requested,
    )
    session.add(item)
    await session.flush([item])
    return item


async def finish_quota_reservation(
    session: AsyncSession,
    *,
    status: str,
    provisioning_request_id: UUID | None = None,
    service_request_id: UUID | None = None,
) -> None:
    if status not in {"CONSUMED", "RELEASED"}:
        raise ValueError("invalid quota reservation status")
    filters = []
    if provisioning_request_id is not None:
        filters.append(
            QuotaReservation.provisioning_request_id == provisioning_request_id
        )
    if service_request_id is not None:
        filters.append(QuotaReservation.service_request_id == service_request_id)
    if len(filters) != 1:
        raise ValueError("exactly one quota reservation request is required")
    item = await session.scalar(
        select(QuotaReservation).where(*filters).with_for_update()
    )
    if item is not None and item.status == "ACTIVE":
        item.status = status
        item.finished_at = datetime.now(UTC)

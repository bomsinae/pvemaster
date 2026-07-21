import asyncio
import os
from datetime import UTC, datetime
from secrets import token_urlsafe
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import delete, select, text

from app.core.config import Settings
from app.core.errors import AppError
from app.main import create_app
from app.models.auth import AuditLog, LoginThrottle, RefreshToken, User, UserRole
from app.models.cluster import Cluster, ClusterCredential
from app.models.ipam import (
    IpAddress,
    IpAddressState,
    IpAllocation,
    IpAllocationStatus,
    IpPool,
    IpPoolExclusion,
)
from app.models.operation import Operation, PveTask, Workload, WorkloadAssignment
from app.schemas.ipam import IpPoolCreate, IpPoolUpdate, IpRangeRequest
from app.security.access import Principal
from app.security.passwords import PasswordManager
from app.services.ipam import IpamService
from app.services.observability import ObservabilityService

pytestmark = pytest.mark.skipif(
    "AUTH_TEST_DATABASE_URL" not in os.environ,
    reason="AUTH_TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


async def _clear(app: object) -> None:
    async with app.state.db_session_factory() as session:
        await session.execute(text("SET LOCAL app.audit_retention = 'on'"))
        for model in (
            AuditLog,
            IpAllocation,
            IpAddress,
            IpPoolExclusion,
            IpPool,
            PveTask,
            Operation,
            WorkloadAssignment,
            Workload,
            ClusterCredential,
            Cluster,
            RefreshToken,
            LoginThrottle,
            User,
        ):
            await session.execute(delete(model))
        await session.commit()


async def test_transactional_ipam_concurrency_exclusions_quarantine_and_ipv6() -> None:
    settings = Settings(
        _env_file=None,
        database_url=SecretStr(os.environ["AUTH_TEST_DATABASE_URL"]),
        redis_url=SecretStr("redis://localhost:6379/15"),
        app_secret_key=SecretStr(token_urlsafe(32)),
    )
    app = create_app(settings)
    await _clear(app)
    admin_id, cluster_id = uuid4(), uuid4()
    workload_ids = [uuid4() for _ in range(10)]
    principal = Principal(
        user_id=admin_id,
        email="ipam-admin@example.test",
        role=UserRole.SUPER_ADMIN,
        session_epoch=0,
    )
    async with app.state.db_session_factory() as session:
        session.add(
            User(
                id=admin_id,
                email=principal.email,
                display_name="IPAM Admin",
                role=UserRole.SUPER_ADMIN.value,
                password_hash=PasswordManager().hash(token_urlsafe(24)),
                is_active=True,
            )
        )
        session.add(
            Cluster(
                id=cluster_id,
                name="ipam-test-cluster",
                api_base_url="https://ipam-pve.example.test:8006",
                is_active=True,
                version=1,
            )
        )
        session.add_all(
            [
                Workload(
                    id=workload_id,
                    cluster_id=cluster_id,
                    vmid=500 + index,
                    node="pve-test",
                    kind="QEMU",
                    name=f"IPAM VM {index}",
                    power_state="STOPPED",
                    is_template=False,
                    is_present=True,
                    observed_at=datetime.now(UTC),
                    version=1,
                )
                for index, workload_id in enumerate(workload_ids)
            ]
        )
        await session.commit()

    def service(session: object, suffix: str = "test") -> IpamService:
        return IpamService(
            session=session,
            principal=principal,
            request_id=f"ipam-{suffix}",
            source_ip="127.0.0.1",
        )

    try:
        async with app.state.db_session_factory() as session:
            ipam = service(session)
            pool = await ipam.create_pool(
                IpPoolCreate(
                    name="transactional-v4",
                    cluster_id=cluster_id,
                    cidr="192.0.2.0/28",
                    gateway="192.0.2.1",
                    dns_servers=["192.0.2.53"],
                    bridge="vmbr120",
                    vlan_tag=120,
                    excluded_ranges=[
                        IpRangeRequest(start="192.0.2.2", end="192.0.2.3", reason="network gear")
                    ],
                    quarantine_seconds=3600,
                )
            )
            reserved = await ipam.reserve_address(pool.id, "192.0.2.4", "reserved appliance")
            assert reserved.state is IpAddressState.DISABLED

            with pytest.raises(AppError) as duplicate:
                await ipam.create_pool(
                    IpPoolCreate(name="duplicate-v4", cidr="192.0.2.0/28", bridge="vmbr0")
                )
            assert duplicate.value.code == "IP_POOL_OVERLAP"
            await session.rollback()

            with pytest.raises(AppError) as overlap:
                await ipam.create_pool(
                    IpPoolCreate(name="overlap-v4", cidr="192.0.2.8/29", bridge="vmbr0")
                )
            assert overlap.value.code == "IP_POOL_OVERLAP"
            await session.rollback()

        async def allocate_one(workload_id: UUID, index: int) -> object:
            async with app.state.db_session_factory() as concurrent_session:
                return await service(concurrent_session, str(index)).allocate(
                    pool.id, workload_id, None
                )

        allocations = await asyncio.gather(
            *(
                allocate_one(workload_id, index)
                for index, workload_id in enumerate(workload_ids[:6])
            )
        )
        addresses = [item.address for item in allocations]
        assert len(addresses) == len(set(addresses))
        assert not {
            "192.0.2.0",
            "192.0.2.1",
            "192.0.2.2",
            "192.0.2.3",
            "192.0.2.4",
            "192.0.2.15",
        }.intersection(addresses)

        async with app.state.db_session_factory() as session:
            ipam = service(session)
            with pytest.raises(AppError) as manual_duplicate:
                await ipam.allocate(pool.id, workload_ids[6], addresses[0])
            assert manual_duplicate.value.code == "IP_ADDRESS_UNAVAILABLE"
            await session.rollback()

            workload = await session.get(Workload, workload_ids[0])
            assert workload is not None
            workload.is_present = False
            await session.commit()
            unchanged = await session.get(IpAllocation, allocations[0].id)
            assert unchanged is not None
            assert unchanged.status == IpAllocationStatus.ASSIGNED.value

            quarantined = await ipam.release(allocations[0].id, "VM removed; reviewed by admin")
            assert quarantined.status is IpAllocationStatus.QUARANTINED
            assert quarantined.quarantined_until is not None
            with pytest.raises(AppError) as still_quarantined:
                await ipam.approve_release(quarantined.ip_address_id)
            assert still_quarantined.value.code == "IP_QUARANTINE_ACTIVE"
            await session.rollback()

            pool_counts = await ObservabilityService(
                session=session,
                redis=app.state.redis,
                settings=settings,
                principal=principal,
            )._ip_pool_counts()
            assert {name: available for _pool_id, name, available in pool_counts}[
                "transactional-v4"
            ] == 4

            tiny = await ipam.create_pool(
                IpPoolCreate(
                    name="exhaustion-v4",
                    cidr="198.51.100.0/30",
                    gateway="198.51.100.1",
                    bridge="vmbr0",
                )
            )
            only = await ipam.allocate(tiny.id, workload_ids[6], None)
            assert only.address == "198.51.100.2"
            with pytest.raises(AppError) as exhausted:
                await ipam.allocate(tiny.id, workload_ids[7], None)
            assert exhausted.value.code == "IP_POOL_EXHAUSTED"
            await session.rollback()

            ipv6 = await ipam.create_pool(
                IpPoolCreate(
                    name="sparse-v6",
                    cidr="2001:db8:120::/64",
                    gateway="2001:db8:120::1",
                    dns_servers=["2001:4860:4860::8888"],
                    bridge="vmbr6",
                    quarantine_seconds=0,
                )
            )
            ipv6_allocation = await ipam.allocate(ipv6.id, workload_ids[8], None)
            assert ipv6_allocation.address == "2001:db8:120::2"
            assert ipv6.ip_family == 6
            ipv6_quarantine = await ipam.release(ipv6_allocation.id, "retired after review")
            released_address = await ipam.approve_release(ipv6_quarantine.ip_address_id)
            assert released_address.state is IpAddressState.AVAILABLE
            history = await session.get(IpAllocation, ipv6_allocation.id)
            assert history is not None
            assert history.status == IpAllocationStatus.RELEASED.value

            editable = await ipam.create_pool(
                IpPoolCreate(
                    name="editable-v4",
                    cidr="203.0.113.0/29",
                    gateway="203.0.113.1",
                    bridge="vmbr-edit",
                    quarantine_seconds=0,
                )
            )
            edited = await ipam.update_pool(
                editable.id,
                IpPoolUpdate(
                    name="edited-v4",
                    cidr="203.0.113.0/28",
                    gateway="203.0.113.1",
                    dns_servers=["203.0.113.2"],
                    bridge="vmbr-edited",
                    allocation_strategy="RANDOM",
                    quarantine_seconds=0,
                    version=editable.version,
                ),
            )
            assert edited.name == "edited-v4"
            assert edited.cidr == "203.0.113.0/28"
            assert edited.bridge == "vmbr-edited"
            edited_pool_id = edited.id
            edited_pool_version = edited.version
            editable_allocation = await ipam.allocate(edited_pool_id, workload_ids[9], None)
            with pytest.raises(AppError) as address_change:
                await ipam.update_pool(
                    edited_pool_id,
                    IpPoolUpdate(cidr="203.0.113.0/29", version=edited_pool_version),
                )
            assert address_change.value.code == "IP_POOL_ADDRESSES_EXIST"
            await session.rollback()
            with pytest.raises(AppError) as in_use_delete:
                await ipam.delete_pool(edited_pool_id, version=edited_pool_version)
            assert in_use_delete.value.code == "IP_POOL_IN_USE"
            await session.rollback()
            editable_quarantine = await ipam.release(editable_allocation.id, "pool retirement")
            await ipam.approve_release(editable_quarantine.ip_address_id)
            current_editable = await ipam.get_pool(edited_pool_id)
            await ipam.delete_pool(edited_pool_id, version=current_editable.version)
            assert all(item.id != edited_pool_id for item in await ipam.list_pools())

            active_rows = await session.scalars(
                select(IpAllocation).where(
                    IpAllocation.status.in_(
                        [
                            IpAllocationStatus.RESERVED.value,
                            IpAllocationStatus.ASSIGNED.value,
                            IpAllocationStatus.QUARANTINED.value,
                        ]
                    )
                )
            )
            active_address_ids = [row.ip_address_id for row in active_rows]
            assert len(active_address_ids) == len(set(active_address_ids))
    finally:
        await _clear(app)
        await app.state.db_engine.dispose()
